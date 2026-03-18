"""调度模块

使用 APScheduler 每隔 N 小时执行一次 Monitor.run_once()。
支持立即执行一次后进入定时模式（--run-now 标志）。
"""

from __future__ import annotations

import asyncio
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from feifeile.monitor import Monitor


def build_scheduler(monitor: Monitor, interval_hours: float) -> BlockingScheduler:
    """创建并配置一个 BlockingScheduler。

    Args:
        monitor: 已初始化的 Monitor 实例。
        interval_hours: 两次执行之间的间隔（小时）。

    Returns:
        配置好的 BlockingScheduler，调用 .start() 即可运行。
    """
    scheduler = BlockingScheduler()

    def job() -> None:
        logger.info("开始执行定时查询任务")
        asyncio.run(monitor.run_once())
        logger.info("定时查询任务完成")

    scheduler.add_job(
        job,
        trigger=IntervalTrigger(hours=interval_hours),
        id="flight_monitor",
        name="海南航空航班价格监控",
        max_instances=1,
        coalesce=True,  # 合并错过的触发为一次执行（max_instances=1 防止重叠）
    )
    return scheduler


def run_scheduler(
    monitor: Monitor,
    interval_hours: float,
    run_now: bool = True,
) -> None:
    """启动调度循环（阻塞）。

    Args:
        monitor: Monitor 实例。
        interval_hours: 检查间隔（小时）。
        run_now: 若为 True，则在调度器启动前立即执行一次查询。
    """
    if run_now:
        logger.info("立即执行首次查询...")
        asyncio.run(monitor.run_once())

    scheduler = build_scheduler(monitor, interval_hours)
    logger.info(
        "调度器已启动，每 {} 小时执行一次查询（按 Ctrl+C 停止）",
        interval_hours,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")
