"""命令行入口模块

提供以下子命令：
  add       添加一条航班订阅
  list      列出所有订阅
  remove    删除指定订阅
  check     立即执行一次查询
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import click
from loguru import logger

from feifeile.config import HNAConfig, MonitorConfig, WeComConfig
from feifeile.monitor import Monitor, Subscription, SubscriptionStore


def _load_all_configs(
    *, require_wecom: bool = True,
) -> tuple[HNAConfig, WeComConfig | None, MonitorConfig]:
    """从环境变量 / .env 文件加载配置。

    当 require_wecom=False 时，企业微信配置缺失不会报错（返回 None）。
    """
    hna = HNAConfig()  # type: ignore[call-arg]
    monitor = MonitorConfig()
    wecom: WeComConfig | None = None
    if require_wecom:
        wecom = WeComConfig()  # type: ignore[call-arg]
    else:
        try:
            wecom = WeComConfig()  # type: ignore[call-arg]
        except (ValueError, KeyError):
            pass
    return hna, wecom, monitor


def _make_store(monitor_config: MonitorConfig) -> SubscriptionStore:
    return SubscriptionStore(monitor_config.subscriptions_file)


@click.group()
def main() -> None:
    """飞飞乐 — 海南航空航班特价监控工具"""


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
def add(
    origin: str,
    destination: str,
    depart_date: date,
    threshold: float | None,
) -> None:
    """添加一条航班订阅。"""
    _, _, monitor_config = _load_all_configs(require_wecom=False)
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
def list_subs() -> None:
    """列出所有订阅。"""
    _, _, monitor_config = _load_all_configs(require_wecom=False)
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
def remove(sub_id: str) -> None:
    """删除指定订阅（使用 list 命令查看 ID）。"""
    _, _, monitor_config = _load_all_configs(require_wecom=False)
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
def check(dry_run: bool) -> None:
    """执行一次航班查询并发送通知。"""
    hna_config, wecom_config, monitor_config = _load_all_configs(
        require_wecom=not dry_run,
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
