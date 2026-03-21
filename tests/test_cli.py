"""tests/test_cli.py — CLI 模块单元测试"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from feifeile.cli import _load_all_configs, main


class TestDataDirOption:
    """-d / --data-dir 全局选项相关测试"""

    def test_load_configs_from_data_dir(self, tmp_path, monkeypatch):
        """通过 _load_all_configs(data_dir=...) 从指定目录的 .env 文件加载配置。"""
        env_file = tmp_path / ".env"
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
            require_wecom=False, data_dir=tmp_path,
        )
        assert hna.username == "from_file"
        assert hna.password == "secret_file"
        assert monitor.price_threshold == 888.0

    def test_load_configs_default_dir(self, monkeypatch):
        """默认 data_dir 使用当前目录（环境变量优先）。"""
        monkeypatch.setenv("HNA_USERNAME", "env_user")
        monkeypatch.setenv("HNA_PASSWORD", "env_pass")
        hna, _, _ = _load_all_configs(require_wecom=False, data_dir=Path("."))
        assert hna.username == "env_user"

    def test_env_vars_override_env_file(self, tmp_path, monkeypatch):
        """环境变量优先级高于 .env 文件。"""
        env_file = tmp_path / ".env"
        env_file.write_text("HNA_USERNAME=file_user\nHNA_PASSWORD=file_pass\n")
        monkeypatch.setenv("HNA_USERNAME", "env_user")
        monkeypatch.setenv("HNA_PASSWORD", "env_pass")

        hna, _, _ = _load_all_configs(
            require_wecom=False, data_dir=tmp_path,
        )
        assert hna.username == "env_user"

    def test_cli_list_with_data_dir_option(self, tmp_path, monkeypatch):
        """验证 CLI list 子命令接受 -d 全局选项。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(main, ["-d", str(tmp_path), "list"])
        assert result.exit_code == 0
        assert "暂无订阅" in result.output

    def test_cli_add_with_data_dir_option(self, tmp_path, monkeypatch):
        """验证 CLI add 子命令通过 -d 正确加载配置并写入订阅到数据目录。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-d", str(tmp_path), "add", "-f", "HAK", "-t", "PEK", "--date", "2099-01-01"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        # 验证订阅文件被创建在数据目录下
        assert (tmp_path / "subscriptions.json").exists()

    def test_cli_add_with_local_data_dir(self, tmp_path, monkeypatch):
        """验证 add 子命令自身的 -d 选项可将订阅写入指定目录。"""
        data_dir = tmp_path / "custom"
        data_dir.mkdir()
        env_file = data_dir / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["add", "-d", str(data_dir), "-f", "HAK", "-t", "PEK", "--date", "2099-01-01"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        assert (data_dir / "subscriptions.json").exists()

    def test_cli_add_local_data_dir_overrides_global(self, tmp_path, monkeypatch):
        """验证 add -d 覆盖全局 -d 选项。"""
        global_dir = tmp_path / "global"
        local_dir = tmp_path / "local"
        global_dir.mkdir()
        local_dir.mkdir()
        env_file = local_dir / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-d", str(global_dir), "add", "-d", str(local_dir), "-f", "SYX", "-t", "SHA", "--date", "2099-06-01"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        # 订阅应写入 local_dir 而非 global_dir
        assert (local_dir / "subscriptions.json").exists()
        assert not (global_dir / "subscriptions.json").exists()

    def test_cli_add_with_price_option(self, tmp_path, monkeypatch):
        """验证 add 子命令的 -p / --price 参数设置价格阈值。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-d", str(tmp_path), "add", "-f", "HAK", "-t", "PEK", "--date", "2099-01-01", "-p", "299"],
        )
        assert result.exit_code == 0
        assert "≤¥299" in result.output

    def test_cli_add_with_long_options(self, tmp_path, monkeypatch):
        """验证 add 子命令的长参数 --from / --to / --price 可正常使用。"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["-d", str(tmp_path), "add", "--from", "CAN", "--to", "PKX", "--date", "2099-07-01", "--price", "399"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        assert "CAN→PKX" in result.output
        assert "≤¥399" in result.output

    def test_cli_add_creates_nonexistent_data_dir(self, tmp_path, monkeypatch):
        """验证 add -d 可自动创建不存在的数据目录。"""
        data_dir = tmp_path / "new" / "nested"
        assert not data_dir.exists()
        monkeypatch.setenv("HNA_USERNAME", "u")
        monkeypatch.setenv("HNA_PASSWORD", "p")

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["add", "-d", str(data_dir), "-f", "HAK", "-t", "PEK", "--date", "2099-01-01"],
        )
        assert result.exit_code == 0
        assert "已添加订阅" in result.output
        assert data_dir.exists()
        assert (data_dir / "subscriptions.json").exists()

    def test_cli_check_dry_run_with_data_dir(self, tmp_path, monkeypatch):
        """验证 check --dry-run 搭配 -d 正常运行。"""
        subs_file = tmp_path / "subscriptions.json"
        subs_file.write_text("[]")
        env_file = tmp_path / ".env"
        env_file.write_text(
            "HNA_USERNAME=u\nHNA_PASSWORD=p\n"
        )
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        runner = CliRunner()
        # Monitor.run_once is async, mock it
        with patch("feifeile.cli.Monitor") as MockMonitor:
            instance = MockMonitor.return_value
            instance.run_once = AsyncMock(return_value={})
            result = runner.invoke(
                main, ["-d", str(tmp_path), "check", "--dry-run"],
            )
        assert result.exit_code == 0
        assert "Dry-run" in result.output

    def test_subscriptions_and_token_in_data_dir(self, tmp_path, monkeypatch):
        """验证 token 和 subscriptions 文件都存储在 data_dir 下。"""
        env_file = tmp_path / ".env"
        env_file.write_text("HNA_USERNAME=u\nHNA_PASSWORD=p\n")
        monkeypatch.delenv("HNA_USERNAME", raising=False)
        monkeypatch.delenv("HNA_PASSWORD", raising=False)

        response_json = json.dumps({
            "success": True,
            "data": {
                "ok": True,
                "token": "tok_test",
                "secret": "ref_test",
                "expireTime": 9999999999,
                "user": {"ucUserId": "UC_TEST"},
            },
        })

        runner = CliRunner()
        result = runner.invoke(
            main, ["-d", str(tmp_path), "token", "import", response_json],
        )
        assert result.exit_code == 0
        assert "Token 已导入" in result.output
        assert (tmp_path / ".auth_token.json").exists()
