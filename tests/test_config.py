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
        assert cfg.app_version == "10.12.0"
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

    def test_load_from_custom_env_file(self, tmp_path, monkeypatch):
        """通过 _env_file 构造参数从自定义文件加载。"""
        env_file = tmp_path / "custom.env"
        env_file.write_text("HNA_USERNAME=file_user\nHNA_PASSWORD=file_pass\n")
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)
        cfg = HNAConfig(_env_file=str(env_file))
        assert cfg.username == "file_user"
        assert cfg.password == "file_pass"


class TestWeComConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("WECOM_CORP_ID", "ww_test")
        monkeypatch.setenv("WECOM_SECRET", "test_secret")
        monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
        cfg = WeComConfig()
        assert cfg.corp_id == "ww_test"
        assert cfg.secret == "test_secret"
        assert cfg.agent_id == 1000002
        assert cfg.timeout == 10.0


class TestMonitorConfig:
    def test_defaults(self, monkeypatch):
        cfg = MonitorConfig()
        assert cfg.price_threshold == 199.0
        assert cfg.subscriptions_file == "subscriptions.json"

    def test_invalid_threshold(self, monkeypatch):
        monkeypatch.setenv("MONITOR_PRICE_THRESHOLD", "0")
        with pytest.raises(Exception):
            MonitorConfig()
