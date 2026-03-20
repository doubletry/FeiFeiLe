"""命令行入口模块

提供以下子命令：
  add       添加一条航班订阅
  list      列出所有订阅
  remove    删除指定订阅
  check     立即执行一次查询

全局选项：
  -d        指定数据目录（.env / token / subscriptions 统一存放）
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import click
from loguru import logger

from feifeile.config import HNAConfig, MonitorConfig, WeComConfig
from feifeile.monitor import Monitor, Subscription, SubscriptionStore
from feifeile.auth import HNAAuth


def _load_all_configs(
    *, require_wecom: bool = True,
    data_dir: Path,
) -> tuple[HNAConfig, WeComConfig | None, MonitorConfig]:
    """从环境变量 / .env 文件加载配置。

    当 require_wecom=False 时，企业微信配置缺失不会报错（返回 None）。
    """
    kwargs: dict = {}
    env_file = data_dir / ".env"
    if env_file.exists():
        kwargs["_env_file"] = str(env_file)

    hna = HNAConfig(**kwargs)  # type: ignore[call-arg]
    monitor = MonitorConfig(**kwargs)
    wecom: WeComConfig | None = None
    if require_wecom:
        wecom = WeComConfig(**kwargs)  # type: ignore[call-arg]
    else:
        try:
            wecom = WeComConfig(**kwargs)  # type: ignore[call-arg]
        except (ValueError, KeyError):
            pass
    return hna, wecom, monitor


@click.group()
@click.option(
    "-d",
    "--data-dir",
    "data_dir",
    default=".",
    type=click.Path(file_okay=False),
    help="数据目录（.env、Token、订阅文件统一存放，默认当前目录）",
)
@click.pass_context
def main(ctx: click.Context, data_dir: str) -> None:
    """飞飞乐 — 海南航空航班特价监控工具"""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = Path(data_dir).resolve()


@main.command()
@click.option("--origin", "-o", required=True, help="出发机场三字码（如 HAK）")
@click.option("--destination", "-D", required=True, help="到达机场三字码（如 PEK）")
@click.option(
    "--date",
    "depart_date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="出发日期，格式 YYYY-MM-DD",
)
@click.option(
    "--threshold",
    "-t",
    default=None,
    type=float,
    help="价格阈值（元），默认使用配置文件中的值",
)
@click.pass_context
def add(
    ctx: click.Context,
    origin: str,
    destination: str,
    depart_date: date,
    threshold: float | None,
) -> None:
    """添加一条航班订阅。"""
    data_dir: Path = ctx.obj["data_dir"]
    _, _, monitor_config = _load_all_configs(require_wecom=False, data_dir=data_dir)
    store = SubscriptionStore(str(data_dir / "subscriptions.json"))
    price_threshold = threshold if threshold is not None else monitor_config.price_threshold
    sub = Subscription(
        id=uuid.uuid4().hex[:8],
        origin=origin.upper(),
        destination=destination.upper(),
        depart_date=depart_date.strftime("%Y-%m-%d"),
        price_threshold=price_threshold,
    )
    store.add(sub)
    click.echo(
        f"✅ 已添加订阅 [{sub.id}]: "
        f"{sub.origin}→{sub.destination} {sub.depart_date} ≤¥{price_threshold:.0f}"
    )


@main.command("list")
@click.pass_context
def list_subs(ctx: click.Context) -> None:
    """列出所有订阅。"""
    data_dir: Path = ctx.obj["data_dir"]
    store = SubscriptionStore(str(data_dir / "subscriptions.json"))
    subs = store.list_all()
    if not subs:
        click.echo("暂无订阅")
        return
    for sub in subs:
        status = "✅" if sub.active and not sub.is_expired() else "❌"
        click.echo(
            f"{status} [{sub.id}] {sub.origin}→{sub.destination} "
            f"{sub.depart_date} ≤¥{sub.price_threshold:.0f} "
            f"(创建于 {sub.created_at})"
        )


@main.command()
@click.argument("sub_id")
@click.pass_context
def remove(ctx: click.Context, sub_id: str) -> None:
    """删除指定订阅（使用 list 命令查看 ID）。"""
    data_dir: Path = ctx.obj["data_dir"]
    store = SubscriptionStore(str(data_dir / "subscriptions.json"))
    if store.remove(sub_id):
        click.echo(f"✅ 已删除订阅 [{sub_id}]")
    else:
        click.echo(f"❌ 未找到订阅 [{sub_id}]")


@main.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Dry-run 模式：只输出解析结果，不发送微信消息",
)
@click.pass_context
def check(ctx: click.Context, dry_run: bool) -> None:
    """执行一次航班查询并发送通知。"""
    data_dir: Path = ctx.obj["data_dir"]
    hna_config, wecom_config, monitor_config = _load_all_configs(
        require_wecom=not dry_run, data_dir=data_dir,
    )
    store = SubscriptionStore(str(data_dir / "subscriptions.json"))
    token_file = data_dir / ".auth_token.json"
    monitor = Monitor(
        hna_config, wecom_config, monitor_config, store,
        dry_run=dry_run, token_file=token_file,
    )
    if dry_run:
        click.echo("🔍 Dry-run 模式：仅查询并输出结果，不发送微信消息")
    results = asyncio.run(monitor.run_once())
    total = sum(len(v) for v in results.values())
    click.echo(f"查询完成，共找到 {total} 个符合条件的航班")
    for sub_id, offers in results.items():
        for o in offers:
            click.echo(f"  [{sub_id}] {o}")


# ---------------------------------------------------------------------------
# token 子命令组
# ---------------------------------------------------------------------------

@main.group()
def token() -> None:
    """Token 管理（导入、查看、清除）。"""


@token.command("import")
@click.argument("response_json", required=False)
@click.pass_context
def token_import(ctx: click.Context, response_json: str | None) -> None:
    """导入登录接口的 Response JSON，自动解析 Token。

    \b
    获取方法：
    1. 电脑浏览器打开 https://m.hnair.com 并登录
    2. F12 开发者工具 → Network → 找到 login 请求
    3. 查看该请求的 Response，复制完整 JSON
    4. 粘贴到本命令

    \b
    用法：
      feifeile token import '{"success":true,"data":{"token":"...","secret":"..."}}'
      echo '...' | feifeile token import
    """
    data_dir: Path = ctx.obj["data_dir"]
    hna_config, _, _ = _load_all_configs(require_wecom=False, data_dir=data_dir)
    token_file = data_dir / ".auth_token.json"
    auth = HNAAuth(hna_config, token_file=token_file)

    if response_json is None:
        if sys.stdin.isatty():
            response_json = click.prompt("请粘贴登录接口的完整 Response JSON")
        else:
            response_json = sys.stdin.read().strip()

    if not response_json:
        click.echo("❌ 未提供 Response JSON")
        raise SystemExit(1)

    try:
        tok = auth.inject_from_response(response_json)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        click.echo(f"❌ JSON 解析失败: {exc}")
        raise SystemExit(1)

    remaining = tok.expires_at - time.time()
    days = int(remaining // 86400)
    click.echo(
        f"✅ Token 已导入并保存\n"
        f"   会员 ID:      {tok.member_id or '未知'}\n"
        f"   Access Token: {tok.access_token[:20]}...\n"
        f"   Refresh Token:{' 有' if tok.refresh_token else ' 无'}\n"
        f"   有效期:        {days} 天"
    )


@token.command("show")
@click.pass_context
def token_show(ctx: click.Context) -> None:
    """查看当前已保存的 Token 状态。"""
    data_dir: Path = ctx.obj["data_dir"]
    hna_config, _, _ = _load_all_configs(require_wecom=False, data_dir=data_dir)
    token_file = data_dir / ".auth_token.json"
    auth = HNAAuth(hna_config, token_file=token_file)
    tok = auth._token
    if tok is None:
        click.echo("❌ 当前无已保存的 Token")
        return
    remaining = tok.expires_at - time.time()
    if remaining > 0:
        days = int(remaining // 86400)
        hours = int((remaining % 86400) // 3600)
        click.echo(
            f"✅ Token 有效\n"
            f"   会员 ID:      {tok.member_id or '未知'}\n"
            f"   Access Token: {tok.access_token[:20]}...\n"
            f"   Refresh Token:{' 有' if tok.refresh_token else ' 无'}\n"
            f"   剩余有效期:   {days} 天 {hours} 小时"
        )
    else:
        click.echo(
            f"⚠️  Token 已过期（{int(-remaining // 3600)} 小时前）\n"
            f"   会员 ID:      {tok.member_id or '未知'}\n"
            f"   Refresh Token:{' 有（可尝试刷新）' if tok.refresh_token else ' 无'}"
        )


@token.command("clear")
@click.pass_context
def token_clear(ctx: click.Context) -> None:
    """清除已保存的 Token 文件。"""
    data_dir: Path = ctx.obj["data_dir"]
    token_file = data_dir / ".auth_token.json"
    if token_file.exists():
        token_file.unlink()
        click.echo("✅ 已清除 Token 文件")
    else:
        click.echo("ℹ️  无 Token 文件")


if __name__ == "__main__":
    main()
