"""tests/test_config.py — 配置模块单元测试"""

from __future__ import annotations

import pytest

from feifeile.config import HNAConfig, MonitorConfig, WeComConfig


class TestHNAConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("HNA_USERNAME", "13800000000")
        monkeypatch.setenv("HNA_PASSWORD", "secret")
        cfg = HNAConfig()
        assert cfg.username == "13800000000"
        assert cfg.password == "secret"
        assert cfg.app_version == "7.8.0"
        assert cfg.max_retries == 3
        assert "hnair" in cfg.base_url

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("HNA_USERNAME", "user1")
        monkeypatch.setenv("HNA_PASSWORD", "pass1")
        monkeypatch.setenv("HNA_APP_VERSION", "9.0.0")
        cfg = HNAConfig()
        assert cfg.app_version == "9.0.0"

    def test_missing_required_field(self, monkeypatch):
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)
        with pytest.raises(Exception):
            HNAConfig()


class TestWeComConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.com/hook")
        cfg = WeComConfig()
        assert cfg.webhook_url == "https://example.com/hook"
        assert cfg.timeout == 10.0


class TestMonitorConfig:
    def test_defaults(self, monkeypatch):
        cfg = MonitorConfig()
        assert cfg.price_threshold == 199.0
        assert cfg.interval_hours == 4.0
        assert cfg.subscriptions_file == "subscriptions.json"

    def test_invalid_interval(self, monkeypatch):
        monkeypatch.setenv("MONITOR_INTERVAL_HOURS", "-1")
        with pytest.raises(Exception):
            MonitorConfig()

    def test_invalid_threshold(self, monkeypatch):
        monkeypatch.setenv("MONITOR_PRICE_THRESHOLD", "0")
        with pytest.raises(Exception):
            MonitorConfig()
