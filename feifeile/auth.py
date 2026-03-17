"""海南航空移动 App 认证模块

实现模拟移动端登录、Token 获取与刷新。
海南航空移动 API 使用 Bearer Token 认证；Token 有效期约 2 小时，
本模块在每次请求前检查有效期并自动刷新。

请求签名算法（hnairSign）和公共参数构建也在此模块中定义，
供 flight 等模块复用。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from feifeile.config import HNAConfig

# ---------------------------------------------------------------------------
# RSA 公钥（用于密码加密，来自 HNA Web App JS）
# ---------------------------------------------------------------------------
_RSA_EXPONENT = 0x10001
_RSA_MODULUS = int(
    "BA58236D7F337C2B728A05F31028833AF83220330B129DC2407109776B644492"
    "BD7BBD8B15498C9C510B915FC4C559FE986F61867337785DB32C284C4E07FF2"
    "56965DE53490CBBA28F14D413D407986ED3DF0E03032031EDD97054C3E6F4F8"
    "B322238EB5B0249556F99D9182B281F04B18CE9155332AF71C8A1A2E49087A571B",
    16,
)
_RSA_KEY_SIZE = 128  # 1024 bits

# ---------------------------------------------------------------------------
# 请求头模板
# ---------------------------------------------------------------------------
_DEFAULT_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "appver": "{app_version}",
    "hna-app": "APP",
    "hna-channel": "HTML5",
}

# ---------------------------------------------------------------------------
# 重试配置
# ---------------------------------------------------------------------------
_RETRYABLE_STATUS_CODES = {502, 503, 504}
_RETRY_BASE_DELAY = 2.0

# ---------------------------------------------------------------------------
# 接口路径
# ---------------------------------------------------------------------------
_LOGIN_PATH = "/appum/common/auth/v2/login"
_REFRESH_PATH = "/appum/common/auth/v2/refresh"


# ===================================================================
# 公共函数：签名 & 请求参数
# ===================================================================

def _build_common_params(config: HNAConfig) -> dict[str, Any]:
    """构建所有请求通用的 ``common`` 参数块。"""
    return {
        "sname": "Linux",
        "sver": "5.0",
        "schannel": "HTML5",
        "caller": "HTML5",
        "slang": "zh-CN",
        "did": config.device_id,
        "stime": int(time.time() * 1000),
        "szone": -480,
        "aname": "com.hnair.spa.web.standard",
        "aver": config.app_version,
        "akey": config.akey,
        "abuild": "63741",
        "atarget": "standard",
        "slat": "slat",
        "slng": "slng",
        "gtcid": "defualt_web_gtcid",
        "riskToken": "",
        "captchaToken": "",
        "blackBox": "",
        "validateToken": "",
    }


def _compute_sign(
    headers: dict[str, str],
    query_params: dict[str, str],
    body_params: dict[str, Any],
    certificate_hash: str,
    hard_code: str,
) -> str:
    """计算 hnairSign（HMAC-SHA1），返回大写十六进制摘要。"""
    values: list[str] = []

    # 1. Header values for keys starting with "hna" (sorted)
    for k in sorted(k for k in headers if k.lower().startswith("hna")):
        values.append(str(headers[k]))

    # 2. Query param values for sorted keys
    for k in sorted(query_params.keys()):
        values.append(str(query_params[k]))

    # 3. Body values for sorted keys (only primitives)
    for k in sorted(body_params.keys()):
        v = body_params[k]
        if isinstance(v, bool):
            values.append("true" if v else "false")
        elif isinstance(v, (str, int, float)):
            values.append(str(v))

    raw = "".join(values) + certificate_hash
    return hmac.new(
        hard_code.encode(), raw.encode(), hashlib.sha1
    ).hexdigest().upper()


# ===================================================================
# RSA 加密（PKCS#1 v1.5，用于密码传输）
# ===================================================================

def _rsa_encrypt(plaintext: str) -> str:
    """使用 RSA 公钥加密明文并返回 Base64 字符串。"""
    msg = plaintext.encode("utf-8")
    padding_len = _RSA_KEY_SIZE - 3 - len(msg)
    if padding_len < 8:
        raise ValueError("Message too long for RSA key size")

    # PKCS#1 v1.5: 0x00 0x02 <random non-zero bytes> 0x00 <message>
    padding = bytearray()
    while len(padding) < padding_len:
        byte = os.urandom(1)[0]
        if byte != 0:
            padding.append(byte)
    padded = b"\x00\x02" + bytes(padding) + b"\x00" + msg

    msg_int = int.from_bytes(padded, "big")
    encrypted_int = pow(msg_int, _RSA_EXPONENT, _RSA_MODULUS)
    encrypted_bytes = encrypted_int.to_bytes(_RSA_KEY_SIZE, "big")
    return base64.b64encode(encrypted_bytes).decode("ascii")


# ===================================================================
# 数据类 & 异常
# ===================================================================

@dataclass
class AuthToken:
    """持有认证 Token 及过期时间"""

    access_token: str
    refresh_token: str
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


# ===================================================================
# 认证客户端
# ===================================================================

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

    async def _login(self) -> None:
        """使用账号密码登录，获取 Token。"""
        data_params: dict[str, Any] = {
            "number": self._config.username,
            "pin": _rsa_encrypt(self._config.password),
            "toSave": True,
        }
        common = _build_common_params(self._config)
        body = {
            "common": common,
            "data": data_params,
        }
        url = f"{self._config.base_url}{_LOGIN_PATH}"
        headers = self._build_headers()
        # 签名使用 common + data 合并后的扁平字典
        flat_params = {**common, **data_params}
        sign = _compute_sign(
            headers, {}, flat_params,
            self._config.certificate_hash, self._config.hard_code,
        )
        logger.info("正在登录海南航空账号: {}", self._config.username)
        result = await self._post(url, body, headers, params={"hnairSign": sign})
        self._token = self._parse_token(result)
        if not self._token.member_id:
            logger.warning("登录成功但未获取到会员 ID，响应数据: {}", result)
        else:
            logger.info("登录成功，会员 ID: {}", self._token.member_id)

    async def _refresh(self) -> None:
        """使用 Refresh Token 刷新 Access Token。"""
        if self._token is None:
            raise AuthError("无可刷新的 Token")
        data_params: dict[str, Any] = {
            "refreshToken": self._token.refresh_token,
        }
        common = _build_common_params(self._config)
        body = {
            "common": common,
            "data": data_params,
        }
        url = f"{self._config.base_url}{_REFRESH_PATH}"
        headers = self._build_headers()
        flat_params = {**common, **data_params}
        sign = _compute_sign(
            headers, {}, flat_params,
            self._config.certificate_hash, self._config.hard_code,
        )
        logger.debug("正在刷新 Token")
        result = await self._post(url, body, headers, params={"hnairSign": sign})
        self._token = self._parse_token(result)
        logger.debug("Token 刷新成功")

    async def _post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发送 POST 请求并返回业务数据字典。

        对 502/503/504 等网关瞬态错误自动重试（指数退避）。
        """
        max_retries = self._config.max_retries

        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    resp = await client.post(
                        url, json=payload, headers=headers, params=params,
                    )
                    if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                        wait = _RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "请求 {} 返回 {}，第 {}/{} 次重试（等待 {:.1f}s）",
                            url, resp.status_code, attempt + 1, max_retries, wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    logger.exception(
                        "请求 {} 返回 HTTP 错误 {}",
                        url, exc.response.status_code,
                    )
                    raise AuthError(
                        f"HTTP 错误 {exc.response.status_code}: {exc.response.text}"
                    ) from exc
                except httpx.RequestError as exc:
                    if attempt < max_retries:
                        wait = _RETRY_BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "请求 {} 网络异常: {}，第 {}/{} 次重试（等待 {:.1f}s）",
                            url, exc, attempt + 1, max_retries, wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.exception("请求 {} 网络错误（已重试 {} 次）", url, max_retries)
                    raise AuthError(f"网络错误（已重试 {max_retries} 次）: {exc}") from exc
                else:
                    break

        body: dict[str, Any] = resp.json()
        if not body.get("success", False):
            code = body.get("errorCode") or "UNKNOWN"
            msg = body.get("errorMessage") or "响应格式异常"
            raise AuthError(f"业务错误 {code}: {msg}")
        return body.get("data") or body

    @staticmethod
    def _parse_token(data: dict[str, Any]) -> AuthToken:
        """从响应数据中解析 AuthToken。

        HNA 登录响应格式::

            {
                "ok": true,
                "token": "access_token_value",
                "secret": "...",
                "user": {"cid": "会员号", "ucUserId": "...", "userCode": "..."}
            }
        """
        access_token = (
            data.get("accessToken")
            or data.get("access_token")
            or data.get("token")
            or ""
        )
        refresh_token = (
            data.get("refreshToken") or data.get("refresh_token")
            or data.get("secret") or ""
        )
        expires_in = int(
            data.get("expiresIn") or data.get("expires_in") or 7200
        )
        # 会员 ID 可能在 data.user.cid / data.memberId 等位置
        user_info = data.get("user") or {}
        member_id = str(
            user_info.get("cid")
            or data.get("memberId")
            or data.get("member_id")
            or user_info.get("ucUserId")
            or user_info.get("userCode")
            or ""
        )
        if not access_token:
            raise AuthError(f"响应中未找到 accessToken: {data}")
        return AuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            member_id=member_id,
        )
