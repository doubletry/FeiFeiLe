"""tests/test_auth.py — 认证模块单元测试"""

from __future__ import annotations

import time

import pytest
import respx
import httpx

from feifeile.auth import AuthError, AuthToken, HNAAuth
from feifeile.config import HNAConfig

# 新版 API 登录 / 刷新路径
_LOGIN_URL_SUFFIX = "/appum/common/auth/v2/login"
_REFRESH_URL_SUFFIX = "/appum/common/auth/v2/refresh"


@pytest.fixture
def hna_config(monkeypatch):
    monkeypatch.setenv("HNA_USERNAME", "13800000000")
    monkeypatch.setenv("HNA_PASSWORD", "test_password")
    return HNAConfig()


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
    async def test_login_success(self, hna_config):
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "tok_abc",
                "refreshToken": "ref_xyz",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )

            auth = HNAAuth(hna_config)
            token = await auth.get_token()

        assert token.access_token == "tok_abc"
        assert token.refresh_token == "ref_xyz"
        assert token.member_id == "MBR001"
        assert not token.is_expired

    @pytest.mark.asyncio
    async def test_login_business_error(self, hna_config):
        error_response = {
            "success": False,
            "errorCode": "E00003",
            "errorMessage": "密码错误",
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=error_response)
            )

            auth = HNAAuth(hna_config)
            with pytest.raises(AuthError, match="业务错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_http_error(self, hna_config):
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(500)
            )

            auth = HNAAuth(hna_config)
            with pytest.raises(AuthError, match="HTTP 错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_network_error(self, hna_config):
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                side_effect=httpx.ConnectError("connection failed")
            )

            auth = HNAAuth(hna_config)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                with pytest.raises(AuthError, match="网络错误"):
                    await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

    @pytest.mark.asyncio
    async def test_token_refresh_when_expired(self, hna_config):
        """过期 Token 应自动触发刷新。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "old_tok",
                "refreshToken": "ref_xyz",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        refresh_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "new_tok",
                "refreshToken": "new_ref",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )
            respx.post(_refresh_url(hna_config)).mock(
                return_value=httpx.Response(200, json=refresh_response)
            )

            auth = HNAAuth(hna_config)
            # 先正常登录
            await auth.get_token()
            # 手动让 Token 过期
            auth._token.expires_at = time.time() - 100  # type: ignore[union-attr]
            # 再次获取 Token，应触发刷新
            token = await auth.get_token()

        assert token.access_token == "new_tok"

    @pytest.mark.asyncio
    async def test_invalidate_clears_token(self, hna_config):
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "tok",
                "refreshToken": "ref",
                "expiresIn": 7200,
            },
        }
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(200, json=login_response)
            )

            auth = HNAAuth(hna_config)
            await auth.get_token()
            assert auth._token is not None
            await auth.invalidate()
            assert auth._token is None

    @pytest.mark.asyncio
    async def test_retry_on_504_then_success(self, hna_config):
        """504 网关超时应自动重试，最终成功。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "tok_retry",
                "refreshToken": "ref_retry",
                "expiresIn": 7200,
                "memberId": "MBR_RETRY",
            },
        }
        with respx.mock:
            route = respx.post(_login_url(hna_config))
            route.side_effect = [
                httpx.Response(504),
                httpx.Response(200, json=login_response),
            ]

            auth = HNAAuth(hna_config)
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
    async def test_retry_exhausted_raises(self, hna_config):
        """重试次数耗尽后应抛出错误。"""
        with respx.mock:
            respx.post(_login_url(hna_config)).mock(
                return_value=httpx.Response(504)
            )

            auth = HNAAuth(hna_config)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                with pytest.raises(AuthError, match="HTTP 错误"):
                    await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

    @pytest.mark.asyncio
    async def test_retry_on_network_error_then_success(self, hna_config):
        """网络异常（如连接断开）应自动重试。"""
        login_response = {
            "success": True,
            "errorCode": None,
            "data": {
                "accessToken": "tok_net",
                "refreshToken": "ref_net",
                "expiresIn": 7200,
            },
        }
        with respx.mock:
            route = respx.post(_login_url(hna_config))
            route.side_effect = [
                httpx.ConnectError("connection reset"),
                httpx.Response(200, json=login_response),
            ]

            auth = HNAAuth(hna_config)
            import feifeile.auth
            original_delay = feifeile.auth._RETRY_BASE_DELAY
            feifeile.auth._RETRY_BASE_DELAY = 0.01
            try:
                token = await auth.get_token()
            finally:
                feifeile.auth._RETRY_BASE_DELAY = original_delay

        assert token.access_token == "tok_net"
        assert route.call_count == 2
