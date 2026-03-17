"""海南航空移动 App 认证模块

实现模拟移动端登录、Token 获取与刷新。
海南航空移动 API 使用 Bearer Token 认证；Token 有效期约 2 小时，
本模块在每次请求前检查有效期并自动刷新。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from feifeile.config import HNAConfig

# 模拟 Android 客户端请求头，与真实 App 保持一致以避免服务端拒绝
_DEFAULT_HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json",
    "User-Agent": "HNA/{app_version} (Android; HNClient)",
    "X-Channel": "Android",
    "X-Client-Type": "app",
}

# 登录接口路径
_LOGIN_PATH = "/hnapps/member/login/password"
# Token 刷新路径
_REFRESH_PATH = "/hnapps/member/login/refresh"


@dataclass
class AuthToken:
    """持有认证 Token 及过期时间"""

    access_token: str
    refresh_token: str
    # Unix 时间戳（秒），到期后需刷新
    expires_at: float
    member_id: str = ""

    @property
    def is_expired(self) -> bool:
        """是否已过期（预留 60 秒缓冲）"""
        return time.time() >= self.expires_at - 60

    @property
    def bearer(self) -> str:
        return f"Bearer {self.access_token}"


class AuthError(Exception):
    """认证相关错误"""


class HNAAuth:
    """海南航空移动端认证客户端

    Example::

        config = HNAConfig(username="138xxxx", password="my_pass")
        auth = HNAAuth(config)
        token = await auth.get_token()
    """

    def __init__(self, config: HNAConfig) -> None:
        self._config = config
        self._token: AuthToken | None = None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def get_token(self) -> AuthToken:
        """获取有效 Token，必要时自动登录或刷新。"""
        if self._token is None:
            await self._login()
        elif self._token.is_expired:
            try:
                await self._refresh()
            except AuthError:
                logger.warning("Token 刷新失败，重新登录")
                await self._login()
        return self._token  # type: ignore[return-value]

    async def invalidate(self) -> None:
        """清除缓存的 Token（如收到 401 后强制重新登录）。"""
        self._token = None

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        return {
            k: v.format(app_version=self._config.app_version)
            for k, v in _DEFAULT_HEADERS.items()
        }

    @staticmethod
    def _md5(text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    async def _login(self) -> None:
        """使用账号密码登录，获取 Token。"""
        payload = {
            "loginName": self._config.username,
            # 密码以 MD5 传输（遵循 App 实际行为）
            "loginPwd": self._md5(self._config.password),
            "loginType": "1",  # 1=密码登录
            "deviceId": self._config.device_id,
            "appVersion": self._config.app_version,
        }
        url = f"{self._config.base_url}{_LOGIN_PATH}"
        logger.info("正在登录海南航空账号: {}", self._config.username)
        data = await self._post(url, payload)
        self._token = self._parse_token(data)
        logger.info("登录成功，会员 ID: {}", self._token.member_id)

    async def _refresh(self) -> None:
        """使用 Refresh Token 刷新 Access Token。"""
        if self._token is None:
            raise AuthError("无可刷新的 Token")
        payload = {
            "refreshToken": self._token.refresh_token,
            "deviceId": self._config.device_id,
        }
        url = f"{self._config.base_url}{_REFRESH_PATH}"
        logger.debug("正在刷新 Token")
        data = await self._post(url, payload)
        self._token = self._parse_token(data)
        logger.debug("Token 刷新成功")

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发送 POST 请求并返回业务数据字典。"""
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            try:
                resp = await client.post(
                    url, json=payload, headers=self._build_headers()
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise AuthError(
                    f"HTTP 错误 {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise AuthError(f"网络错误: {exc}") from exc

        body: dict[str, Any] = resp.json()
        code = body.get("code") or body.get("resultCode") or body.get("status")
        if str(code) not in ("0", "200", "success", "SUCCESS"):
            raise AuthError(
                f"业务错误 code={code}: {body.get('msg') or body.get('message')}"
            )
        return body.get("data") or body

    @staticmethod
    def _parse_token(data: dict[str, Any]) -> AuthToken:
        """从响应数据中解析 AuthToken。"""
        access_token = (
            data.get("accessToken")
            or data.get("access_token")
            or data.get("token")
            or ""
        )
        refresh_token = (
            data.get("refreshToken") or data.get("refresh_token") or ""
        )
        expires_in = int(
            data.get("expiresIn") or data.get("expires_in") or 7200
        )
        member_id = str(
            data.get("memberId") or data.get("member_id") or ""
        )
        if not access_token:
            raise AuthError(f"响应中未找到 accessToken: {data}")
        return AuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            member_id=member_id,
        )
