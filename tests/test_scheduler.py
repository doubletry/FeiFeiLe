"""tests/test_scheduler.py — 调度模块单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from feifeile.scheduler import build_scheduler, run_scheduler


@pytest.fixture
def mock_monitor():
    m = MagicMock()
    m.run_once = AsyncMock(return_value={})
    return m


class TestBuildScheduler:
    def test_creates_scheduler_with_job(self, mock_monitor):
        scheduler = build_scheduler(mock_monitor, interval_hours=4.0)
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "flight_monitor"

    def test_scheduler_not_started(self, mock_monitor):
        scheduler = build_scheduler(mock_monitor, interval_hours=4.0)
        assert not scheduler.running


class TestRunScheduler:
    def test_run_now_executes_immediately(self, mock_monitor):
        """run_now=True 应在启动调度器前立即执行一次 run_once。"""
        with patch("feifeile.scheduler.build_scheduler") as mock_build:
            mock_scheduler = MagicMock()
            mock_scheduler.start.side_effect = KeyboardInterrupt
            mock_build.return_value = mock_scheduler

            run_scheduler(mock_monitor, interval_hours=4.0, run_now=True)

        mock_monitor.run_once.assert_called_once()

    def test_run_now_false_skips_immediate(self, mock_monitor):
        """run_now=False 不应立即执行。"""
        with patch("feifeile.scheduler.build_scheduler") as mock_build:
            mock_scheduler = MagicMock()
            mock_scheduler.start.side_effect = KeyboardInterrupt
            mock_build.return_value = mock_scheduler

            run_scheduler(mock_monitor, interval_hours=4.0, run_now=False)

        mock_monitor.run_once.assert_not_called()

    def test_keyboard_interrupt_exits_cleanly(self, mock_monitor):
        """KeyboardInterrupt 不应向上抛出异常。"""
        with patch("feifeile.scheduler.build_scheduler") as mock_build:
            mock_scheduler = MagicMock()
            mock_scheduler.start.side_effect = KeyboardInterrupt
            mock_build.return_value = mock_scheduler

            # Should not raise
            run_scheduler(mock_monitor, interval_hours=4.0, run_now=False)
