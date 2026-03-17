"""tests/test_auth.py — 认证模块单元测试"""

from __future__ import annotations

import time

import pytest
import respx
import httpx

from feifeile.auth import AuthError, AuthToken, HNAAuth
from feifeile.config import HNAConfig


@pytest.fixture
def hna_config(monkeypatch):
    monkeypatch.setenv("HNA_USERNAME", "13800000000")
    monkeypatch.setenv("HNA_PASSWORD", "test_password")
    return HNAConfig()


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
            "code": "0",
            "data": {
                "accessToken": "tok_abc",
                "refreshToken": "ref_xyz",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(return_value=httpx.Response(200, json=login_response))

            auth = HNAAuth(hna_config)
            token = await auth.get_token()

        assert token.access_token == "tok_abc"
        assert token.refresh_token == "ref_xyz"
        assert token.member_id == "MBR001"
        assert not token.is_expired

    @pytest.mark.asyncio
    async def test_login_business_error(self, hna_config):
        error_response = {"code": "-1", "msg": "密码错误"}
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(return_value=httpx.Response(200, json=error_response))

            auth = HNAAuth(hna_config)
            with pytest.raises(AuthError, match="业务错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_http_error(self, hna_config):
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(return_value=httpx.Response(500))

            auth = HNAAuth(hna_config)
            with pytest.raises(AuthError, match="HTTP 错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_login_network_error(self, hna_config):
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(side_effect=httpx.ConnectError("connection failed"))

            auth = HNAAuth(hna_config)
            with pytest.raises(AuthError, match="网络错误"):
                await auth.get_token()

    @pytest.mark.asyncio
    async def test_token_refresh_when_expired(self, hna_config):
        """过期 Token 应自动触发刷新。"""
        login_response = {
            "code": "0",
            "data": {
                "accessToken": "old_tok",
                "refreshToken": "ref_xyz",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        refresh_response = {
            "code": "0",
            "data": {
                "accessToken": "new_tok",
                "refreshToken": "new_ref",
                "expiresIn": 7200,
                "memberId": "MBR001",
            },
        }
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(return_value=httpx.Response(200, json=login_response))
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/refresh"
            ).mock(return_value=httpx.Response(200, json=refresh_response))

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
            "code": "0",
            "data": {
                "accessToken": "tok",
                "refreshToken": "ref",
                "expiresIn": 7200,
            },
        }
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/member/login/password"
            ).mock(return_value=httpx.Response(200, json=login_response))

            auth = HNAAuth(hna_config)
            await auth.get_token()
            assert auth._token is not None
            await auth.invalidate()
            assert auth._token is None
