"""tests/test_cli.py — CLI 模块单元测试"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from feifeile.cli import _load_all_configs, main


class TestEnvOption:
    """--env 全局选项相关测试"""

    def test_load_configs_from_custom_env_file(self, tmp_path, monkeypatch):
        """通过 _load_all_configs(env_file=...) 从自定义 .env 文件加载配置。"""
        env_file = tmp_path / "custom.env"
        env_file.write_text(
            "HNA_USERNAME=from_file\n"
            "HNA_PASSWORD=secret_file\n"
            "MONITOR_PRICE_THRESHOLD=888\n"
        )
        # 确保环境变量中没有同名变量干扰
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)
        monkeypatch.delenv("MONITOR_PRICE_THRESHOLD", raising=False)

        hna, _, monitor = _load_all_configs(
            require_wecom=False, env_file=str(env_file),
        )
        assert hna.username == "from_file"
        assert hna.password == "secret_file"
        assert monitor.price_threshold == 888.0

    def test_load_configs_env_file_none_uses_default(self, monkeypatch):
        """env_file=None 时使用默认行为（环境变量 + 默认 .env）。"""
        monkeypatch.setenv("HNA_USERNAME", "env_user")
        monkeypatch.setenv("HNA_PASSWORD", "env_pass")
        hna, _, _ = _load_all_configs(require_wecom=False, env_file=None)
        assert hna.username == "env_user"

    def test_env_vars_override_env_file(self, tmp_path, monkeypatch):
        """环境变量优先级高于 .env 文件。"""
        env_file = tmp_path / "custom.env"
        env_file.write_text("HNA_USERNAME=file_user\nHNA_PASSWORD=file_pass\n")
        monkeypatch.setenv("HNA_USERNAME", "env_user")
        monkeypatch.setenv("HNA_PASSWORD", "env_pass")

        hna, _, _ = _load_all_configs(
            require_wecom=False, env_file=str(env_file),
        )
        assert hna.username == "env_user"

    def test_cli_list_with_env_option(self, tmp_path, monkeypatch):
        """验证 CLI list 子命令接受 --env 全局选项。"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
            f"MONITOR_SUBSCRIPTIONS_FILE={tmp_path / 'subs.json'}\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(main, ["--env", str(env_file), "list"])
        assert result.exit_code == 0
        assert "暂无订阅" in result.output

    def test_cli_add_with_env_option(self, tmp_path, monkeypatch):
        """验证 CLI add 子命令通过 --env 正确加载配置。"""
        subs_file = tmp_path / "subs.json"
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
            f"MONITOR_SUBSCRIPTIONS_FILE={subs_file}\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--env", str(env_file), "add", "-o", "HAK", "-d", "PEK", "-D", "2099-01-01"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        # 验证订阅文件被创建在 env 指定的路径
        assert subs_file.exists()

    def test_cli_env_option_nonexistent_file(self, tmp_path):
        """--env 指定不存在的文件时应报错。"""
        runner = CliRunner()
        result = runner.invoke(main, ["--env", str(tmp_path / "nope.env"), "list"])
        assert result.exit_code != 0

    def test_cli_check_dry_run_with_env_option(self, tmp_path, monkeypatch):
        """验证 check --dry-run 搭配 --env 正常运行。"""
        subs_file = tmp_path / "subs.json"
        subs_file.write_text("[]")
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
            f"MONITOR_SUBSCRIPTIONS_FILE={subs_file}\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        # Monitor.run_once is async, mock it
        with patch("feifeile.cli.Monitor") as MockMonitor:
            instance = MockMonitor.return_value
            instance.run_once = AsyncMock(return_value={})
            result = runner.invoke(
                main, ["--env", str(env_file), "check", "--dry-run"],
            )
        assert result.exit_code == 0
        assert "Dry-run" in result.output
