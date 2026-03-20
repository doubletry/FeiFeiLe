"""tests/test_auth.py — 认证模块单元测试"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import respx
import httpx

from feifeile.auth import AuthError, AuthToken, CaptchaRequiredError, HNAAuth
from feifeile.config import HNAConfig

# 新版 API 登录 / 刷新路径
_LOGIN_URL_SUFFIX = "/appum/common/auth/v2/login"
_REFRESH_URL_SUFFIX = "/appum/common/auth/v2/refresh"


@pytest.fixture
def hna_config(monkeypatch):
    monkeypatch.setenv("HNA_USERNAME", "13800000000")
    monkeypatch.setenv("HNA_PASSWORD", "test_password")
    return HNAConfig()


@pytest.fixture
def token_file(tmp_path):
    """返回临时 Token 文件路径，避免测试污染真实文件。"""
    return tmp_path / ".auth_token.json"


def _make_auth(hna_config, token_file):
    """创建 HNAAuth 实例，使用临时 Token 文件。"""
    return HNAAuth(hna_config, token_file=token_file)


def _login_url(cfg: HNAConfig) -> str:
    return f"{cfg.base_url}{_LOGIN_URL_SUFFIX}"


def _refresh_url(cfg: HNAConfig) -> str:
    return f"{cfg.base_url}{_REFRESH_URL_SUFFIX}"


class TestAuthToken:
    def test_bearer(self):
        token = AuthToken(
            access_token="abc123",
            refresh_token="ref456",
            expires_at=time.time() + 3600,
        )
        assert token.bearer == "Bearer abc123"

    def test_not_expired(self):
        token = AuthToken(
            access_token="abc",
            refresh_token="ref",
            expires_at=time.time() + 3600,
        )
        assert not token.is_expired

    def test_expired(self):
        token = AuthToken(
            access_token="abc",
            refresh_token="ref",
            expires_at=time.time() - 10,
        )
        assert token.is_expired


class TestHNAAuth:
    @pytest.mark.asyncio
    async def test_login_success(self, hna_config, token_file):
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_abc",
                "secret": "ref_xyz",
                "user": {
                    "cid": "MBR001",
                    "ucUserId": "UC001",
                    "userCode": "USER001",
                    "userType": "JP",
                },
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )

            auth = _make_auth(hna_config, token_file)
            token = await auth.get_token()

        assert token.access_token == "tok_abc"
        assert token.refresh_token == "ref_xyz"
        assert token.member_id == "UC001"  # ucUserId 优先于 cid
        assert not token.is_expired

    @pytest.mark.asyncio
    async def test_login_business_error(self, hna_config, token_file):
        error_response = {
            "success": False,
            "errorCode": "E00003",
            "errorMessage": "密码错误",
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=error_response)
            )

            auth = _make_auth(hna_config, token_file)
            with pytest.raises(AuthError, match="业务错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_http_error(self, hna_config, token_file):
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(500)
            )

            auth = _make_auth(hna_config, token_file)
            with pytest.raises(AuthError, match="HTTP 错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_network_error(self, hna_config, token_file):
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                side_effect=httpx.ConnectError("connection failed")
            )

            auth = _make_auth(hna_config, token_file)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                with pytest.raises(AuthError, match="网络错误"):
                    await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

    @pytest.mark.asyncio
    async def test_token_refresh_when_expired(self, hna_config, token_file):
        """过期 Token 应自动触发刷新。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "old_tok",
                "secret": "ref_xyz",
                "user": {"cid": "MBR001"},
            },
        }
        refresh_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "new_tok",
                "secret": "new_ref",
                "user": {"cid": "MBR001"},
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )
            respx.post(_refresh_url(hna_config)).mock(
                return_value=httpx.Response(200, json=refresh_response)
            )

            auth = _make_auth(hna_config, token_file)
            # 先正常登录
            await auth.get_token()
            # 手动让 Token 过期
            auth._token.expires_at = time.time() - 100  # type: ignore[union-attr]
            # 再次获取 Token，应触发刷新
            token = await auth.get_token()

        assert token.access_token == "new_tok"

    @pytest.mark.asyncio
    async def test_invalidate_clears_token(self, hna_config, token_file):
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok",
                "secret": "ref",
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )

            auth = _make_auth(hna_config, token_file)
            await auth.get_token()
            assert auth._token is not None
            await auth.invalidate()
            assert auth._token is None

    @pytest.mark.asyncio
    async def test_retry_on_504_then_success(self, hna_config, token_file):
        """504 网关超时应自动重试，最终成功。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_retry",
                "secret": "ref_retry",
                "user": {"cid": "MBR_RETRY"},
            },
        }
        with respx.mock:
            route = respx.post(_login_url(hna_config))
            route.side_effect = [
                httpx.Response(504),
                httpx.Response(200, json=login_response),
            ]

            auth = _make_auth(hna_config, token_file)
            # monkey-patch delay to speed up test
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                token = await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

        assert token.access_token == "tok_retry"
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self, hna_config, token_file):
        """重试次数耗尽后应抛出错误。"""
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(504)
            )

            auth = _make_auth(hna_config, token_file)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                with pytest.raises(AuthError, match="HTTP 错误"):
                    await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

    @pytest.mark.asyncio
    async def test_retry_on_network_error_then_success(self, hna_config, token_file):
        """网络异常（如连接断开）应自动重试。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_net",
                "secret": "ref_net",
            },
        }
        with respx.mock:
            route = respx.post(_login_url(hna_config))
            route.side_effect = [
                httpx.ConnectError("connection reset"),
                httpx.Response(200, json=login_response),
            ]

            auth = _make_auth(hna_config, token_file)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                token = await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

        assert token.access_token == "tok_net"
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_521_cloudflare_then_success(self, hna_config, token_file):
        """Cloudflare 521 (Web Server Is Down) 应自动重试。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_cf",
                "secret": "ref_cf",
                "user": {"cid": "MBR_CF"},
            },
        }
        with respx.mock:
            route = respx.post(_login_url(hna_config))
            route.side_effect = [
                httpx.Response(521),
                httpx.Response(200, json=login_response),
            ]

            auth = _make_auth(hna_config, token_file)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                token = await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

        assert token.access_token == "tok_cf"
        assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_captcha_raises_error(self, hna_config, token_file):
        """E000167 应直接抛出 CaptchaRequiredError。"""
        captcha_response = {
            "success": False,
            "errorCode": "E000167",
            "errorMessage": "请输入验证码",
        }

        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=captcha_response)
            )

            auth = _make_auth(hna_config, token_file)
            with pytest.raises(CaptchaRequiredError):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_token_persisted_after_login(self, hna_config, token_file):
        """登录成功后 Token 应持久化到文件。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_persist",
                "secret": "ref_persist",
                "user": {"cid": "MBR_P"},
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )
            auth = _make_auth(hna_config, token_file)
            await auth.get_token()

        assert token_file.exists()
        saved = json.loads(token_file.read_text())
        assert saved["access_token"] == "tok_persist"
        assert saved["refresh_token"] == "ref_persist"
        assert saved["member_id"] == "MBR_P"

    @pytest.mark.asyncio
    async def test_token_loaded_from_file(self, hna_config, token_file):
        """已保存的有效 Token 应被自动加载，无需登录。"""
        token_file.write_text(json.dumps({
            "access_token": "tok_saved",
            "refresh_token": "ref_saved",
            "expires_at": time.time() + 3600,
            "member_id": "MBR_S",
        }))
        with respx.mock:
            login_route = respx.post(_login_url(hna_config))
            auth = _make_auth(hna_config, token_file)
            token = await auth.get_token()

        assert token.access_token == "tok_saved"
        assert token.member_id == "MBR_S"
        assert login_route.call_count == 0  # 不应触发登录

    def test_inject_token(self, hna_config, token_file):
        """inject_token 应设置 Token 并持久化。"""
        auth = _make_auth(hna_config, token_file)
        tok = auth.inject_token(
            access_token="tok_injected",
            refresh_token="ref_injected",
            member_id="MBR_INJ",
        )
        assert tok.access_token == "tok_injected"
        assert token_file.exists()
        saved = json.loads(token_file.read_text())
        assert saved["access_token"] == "tok_injected"
        assert saved["member_id"] == "MBR_INJ"

    def test_inject_from_response(self, hna_config, token_file):
        """inject_from_response 应从完整响应 JSON 中解析并导入 Token。"""
        response_json = json.dumps({
            "success": True,
            "errorCode": None,
            "data": {
                "ok": True,
                "token": "tok_from_resp",
                "secret": "ref_from_resp",
                "expireTime": int(time.time()) + 2592000,
                "user": {
                    "ucUserId": "UC_RESP",
                    "userCode": "USER_RESP",
                    "cid": "%hna%encrypted",
                },
            },
        })
        auth = _make_auth(hna_config, token_file)
        tok = auth.inject_from_response(response_json)
        assert tok.access_token == "tok_from_resp"
        assert tok.refresh_token == "ref_from_resp"
        assert tok.member_id == "UC_RESP"
        assert not tok.is_expired
        assert token_file.exists()
        saved = json.loads(token_file.read_text())
        assert saved["access_token"] == "tok_from_resp"


class TestParseToken:
    """_parse_token 解析逻辑单元测试。"""

    def test_expire_time_absolute_timestamp(self, hna_config, token_file):
        """expireTime（绝对时间戳）应直接用作 expires_at。"""
        expire_ts = int(time.time()) + 2592000  # 30 天后
        data = {
            "ok": True,
            "token": "tok_abs",
            "secret": "ref_abs",
            "expireTime": expire_ts,
            "user": {"ucUserId": "UC_ABS"},
        }
        token = HNAAuth._parse_token(data)
        assert token.access_token == "tok_abs"
        assert token.expires_at == float(expire_ts)
        assert not token.is_expired

    def test_expires_in_relative(self, hna_config, token_file):
        """expiresIn（相对秒数）应加上当前时间。"""
        data = {
            "ok": True,
            "token": "tok_rel",
            "secret": "ref_rel",
            "expiresIn": 7200,
            "user": {"cid": "MBR_REL"},
        }
        before = time.time()
        token = HNAAuth._parse_token(data)
        assert token.expires_at >= before + 7200
        assert token.expires_at <= time.time() + 7200

    def test_ucuserid_preferred_over_cid(self, hna_config, token_file):
        """ucUserId 应优先于加密的 cid。"""
        data = {
            "ok": True,
            "token": "tok_uid",
            "secret": "ref_uid",
            "user": {
                "cid": "%hna%EncryptedValue==",
                "ucUserId": "002024111313413054000111",
                "userCode": "USER_CODE",
            },
        }
        token = HNAAuth._parse_token(data)
        assert token.member_id == "002024111313413054000111"

    def test_cid_used_when_no_ucuserid(self, hna_config, token_file):
        """没有 ucUserId 时应回退到 cid。"""
        data = {
            "ok": True,
            "token": "tok_cid",
            "secret": "ref_cid",
            "user": {"cid": "PLAIN_CID"},
        }
        token = HNAAuth._parse_token(data)
        assert token.member_id == "PLAIN_CID"


class TestAuthFlow:
    """认证流程集成测试：密码优先 → CAPTCHA 时降级为 Token。"""

    @pytest.mark.asyncio
    async def test_password_login_preferred_over_saved_token(self, hna_config, token_file):
        """无已保存 Token 时，应使用密码登录。"""
        login_response = {
            "success": True,
            "data": {"ok": True, "token": "pw_tok", "secret": "pw_ref", "user": {"cid": "M1"}},
        }
        with respx.mock:
            login_route = respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )
            auth = _make_auth(hna_config, token_file)
            tok = await auth.get_token()

        assert tok.access_token == "pw_tok"
        assert login_route.call_count == 1

    @pytest.mark.asyncio
    async def test_saved_token_skips_password_login(self, hna_config, token_file):
        """已保存有效 Token 时，应跳过密码登录直接使用。"""
        token_file.write_text(json.dumps({
            "access_token": "saved_tok",
            "refresh_token": "saved_ref",
            "expires_at": time.time() + 3600,
            "member_id": "MBR_SAVED",
        }))
        with respx.mock:
            login_route = respx.post(_login_url(hna_config))
            auth = _make_auth(hna_config, token_file)
            tok = await auth.get_token()

        assert tok.access_token == "saved_tok"
        assert login_route.call_count == 0

    @pytest.mark.asyncio
    async def test_expired_token_refresh_success(self, hna_config, token_file):
        """已保存 Token 过期时，应先尝试 refresh，而非重新密码登录。"""
        token_file.write_text(json.dumps({
            "access_token": "old_tok",
            "refresh_token": "old_ref",
            "expires_at": time.time() - 100,
            "member_id": "MBR_EXP",
        }))
        refresh_response = {
            "success": True,
            "data": {"ok": True, "token": "refreshed_tok", "secret": "new_ref", "user": {"cid": "MBR_EXP"}},
        }
        with respx.mock:
            login_route = respx.post(_login_url(hna_config))
            respx.post(_refresh_url(hna_config)).mock(
                return_value=httpx.Response(200, json=refresh_response)
            )
            auth = _make_auth(hna_config, token_file)
            tok = await auth.get_token()

        assert tok.access_token == "refreshed_tok"
        assert login_route.call_count == 0  # 不应触发密码登录

    @pytest.mark.asyncio
    async def test_expired_token_refresh_fails_then_password_login(self, hna_config, token_file):
        """Token 过期且 refresh 失败时，应降级为密码登录。"""
        token_file.write_text(json.dumps({
            "access_token": "old_tok",
            "refresh_token": "bad_ref",
            "expires_at": time.time() - 100,
            "member_id": "MBR_EXP",
        }))
        refresh_error = {
            "success": False,
            "errorCode": "E99999",
            "errorMessage": "refresh token 已失效",
        }
        login_response = {
            "success": True,
            "data": {"ok": True, "token": "new_pw_tok", "secret": "new_pw_ref", "user": {"cid": "MBR_RE"}},
        }
        with respx.mock:
            respx.post(_refresh_url(hna_config)).mock(
                return_value=httpx.Response(200, json=refresh_error)
            )
            login_route = respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )
            auth = _make_auth(hna_config, token_file)
            tok = await auth.get_token()

        assert tok.access_token == "new_pw_tok"
        assert login_route.call_count == 1

    @pytest.mark.asyncio
    async def test_refresh_fails_then_login_captcha_raises(self, hna_config, token_file):
        """Token 过期 → refresh 失败 → 密码登录触发 CAPTCHA → 应抛出 CaptchaRequiredError。"""
        token_file.write_text(json.dumps({
            "access_token": "old_tok",
            "refresh_token": "bad_ref",
            "expires_at": time.time() - 100,
            "member_id": "MBR_CAP",
        }))
        refresh_error = {
            "success": False,
            "errorCode": "E99999",
            "errorMessage": "refresh token 已失效",
        }
        captcha_response = {
            "success": False,
            "errorCode": "E000167",
            "errorMessage": "请输入验证码",
        }
        with respx.mock:
            respx.post(_refresh_url(hna_config)).mock(
                return_value=httpx.Response(200, json=refresh_error)
            )
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=captcha_response)
            )
            auth = _make_auth(hna_config, token_file)
            with pytest.raises(CaptchaRequiredError):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_captcha_then_import_then_success(self, hna_config, token_file):
        """密码登录触发 CAPTCHA → 手动导入 Token → 后续无需登录。"""
        captcha_response = {
            "success": False,
            "errorCode": "E000167",
            "errorMessage": "请输入验证码",
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=captcha_response)
            )
            auth = _make_auth(hna_config, token_file)

            # 第一步：密码登录触发 CAPTCHA
            with pytest.raises(CaptchaRequiredError):
                await auth.get_token()

        # 第二步：用户手动导入 Token（模拟 `token import` 命令）
        response_json = {
            "success": True,
            "data": {
                "ok": True,
                "token": "imported_tok",
                "secret": "imported_ref",
                "expireTime": int(time.time()) + 2592000,
                "user": {"ucUserId": "UC_IMP"},
            },
        }
        tok = auth.inject_from_response(response_json)
        assert tok.access_token == "imported_tok"
        assert token_file.exists()

        # 第三步：再次执行（模拟下次 cron），应从文件加载 Token，无需登录
        with respx.mock:
            login_route = respx.post(_login_url(hna_config))
            auth2 = _make_auth(hna_config, token_file)
            tok2 = await auth2.get_token()

        assert tok2.access_token == "imported_tok"
        assert tok2.member_id == "UC_IMP"
        assert login_route.call_count == 0
