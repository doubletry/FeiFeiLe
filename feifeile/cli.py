"""命令行入口模块

提供以下子命令：
  add       添加一条航班订阅
  list      列出所有订阅
  remove    删除指定订阅
  check     立即执行一次查询

全局选项：
  --env     指定自定义 .env 文件路径
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from pathlib import Path

import click
from loguru import logger

from feifeile.config import HNAConfig, MonitorConfig, WeComConfig
from feifeile.monitor import Monitor, Subscription, SubscriptionStore


def _load_all_configs(
    *, require_wecom: bool = True,
    env_file: str | Path | None = None,
) -> tuple[HNAConfig, WeComConfig | None, MonitorConfig]:
    """从环境变量 / .env 文件加载配置。

    当 require_wecom=False 时，企业微信配置缺失不会报错（返回 None）。
    如果提供了 *env_file*，则使用该路径的 .env 文件。
    """
    kwargs: dict = {}
    if env_file is not None:
        kwargs["_env_file"] = env_file

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


def _make_store(monitor_config: MonitorConfig) -> SubscriptionStore:
    return SubscriptionStore(monitor_config.subscriptions_file)


@click.group()
@click.option(
    "--env",
    "env_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="指定 .env 配置文件路径（默认为当前目录下的 .env）",
)
@click.pass_context
def main(ctx: click.Context, env_file: str | None) -> None:
    """飞飞乐 — 海南航空航班特价监控工具"""
    ctx.ensure_object(dict)
    ctx.obj["env_file"] = env_file


@main.command()
@click.option("--origin", "-o", required=True, help="出发机场三字码（如 HAK）")
@click.option("--destination", "-d", required=True, help="到达机场三字码（如 PEK）")
@click.option(
    "--date",
    "-D",
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
    _, _, monitor_config = _load_all_configs(
        require_wecom=False, env_file=ctx.obj["env_file"],
    )
    store = _make_store(monitor_config)
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
    _, _, monitor_config = _load_all_configs(
        require_wecom=False, env_file=ctx.obj["env_file"],
    )
    store = _make_store(monitor_config)
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
    _, _, monitor_config = _load_all_configs(
        require_wecom=False, env_file=ctx.obj["env_file"],
    )
    store = _make_store(monitor_config)
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
    hna_config, wecom_config, monitor_config = _load_all_configs(
        require_wecom=not dry_run,
        env_file=ctx.obj["env_file"],
    )
    store = _make_store(monitor_config)
    monitor = Monitor(hna_config, wecom_config, monitor_config, store, dry_run=dry_run)
    if dry_run:
        click.echo("🔍 Dry-run 模式：仅查询并输出结果，不发送微信消息")
    results = asyncio.run(monitor.run_once())
    total = sum(len(v) for v in results.values())
    click.echo(f"查询完成，共找到 {total} 个符合条件的航班")
    for sub_id, offers in results.items():
        for o in offers:
            click.echo(f"  [{sub_id}] {o}")


if __name__ == "__main__":
    main()
