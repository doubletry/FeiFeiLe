"""企业微信应用消息通知模块

通过企业微信应用 API 发送卡片消息（textcard）。
需要提供 CORP_ID、SECRET 和 AGENT_ID。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

from feifeile.config import WeComConfig
from feifeile.flight import FlightOffer

# 企业微信 API 基础地址
_WECOM_BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"
_TOKEN_URL = f"{_WECOM_BASE_URL}/gettoken"
_SEND_URL = f"{_WECOM_BASE_URL}/message/send"


class NotifyError(Exception):
    """通知发送失败"""


class WeComNotifier:
    """企业微信应用消息通知客户端

    Example::

        config = WeComConfig(corp_id="ww...", secret="...", agent_id=1000002)
        notifier = WeComNotifier(config)
        await notifier.send_flight_alerts([offer1, offer2], threshold=199)
    """

    def __init__(self, config: WeComConfig) -> None:
        self._config = config
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    async def send_flight_alerts(
        self,
        offers: list[FlightOffer],
        threshold: float,
    ) -> None:
        """发送航班特价提醒卡片消息。

        若 offers 为空，则跳过发送。
        """
        if not offers:
            logger.debug("无符合条件的航班，跳过通知")
            return

        card = self._build_textcard(offers, threshold)
        await self._send_message({
            "touser": "@all",
            "msgtype": "textcard",
            "agentid": self._config.agent_id,
            "textcard": card,
        })

    async def send_text(self, text: str) -> None:
        """发送纯文本消息（用于状态播报等）。"""
        await self._send_message({
            "touser": "@all",
            "msgtype": "text",
            "agentid": self._config.agent_id,
            "text": {"content": text},
        })

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _build_textcard(
        offers: list[FlightOffer], threshold: float,
    ) -> dict[str, str]:
        """构建企业微信 textcard 卡片内容。"""
        title = f"✈️ 特价机票提醒（≤ ¥{threshold:.0f}）"
        lines: list[str] = [
            f'<div class="gray">共找到 {len(offers)} 个符合条件的航班</div>',
        ]
        for offer in offers:
            tag = "🏷️" if offer.is_member_price else ""
            seats = f" 余{offer.seats_remaining}张" if offer.seats_remaining > 0 else ""
            lines.append(
                f'<div class="normal">{tag}{offer.flight_no} '
                f"{offer.depart_date} "
                f"{offer.origin}→{offer.destination} "
                f"{offer.depart_time}→{offer.arrive_time}{seats}</div>"
            )
            tax_info = f" + 税费¥{offer.tax:.0f}" if offer.tax > 0 else ""
            lines.append(
                f'<div class="highlight">'
                f"机票¥{offer.price:.0f}{tax_info}"
                f"</div>"
            )
        description = "\n".join(lines)
        return {
            "title": title,
            "description": description,
            "url": "https://m.hnair.com/",
            "btntxt": "立即购买",
        }

    async def _get_access_token(self) -> str:
        """获取企业微信 access_token，带缓存。"""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        params = {
            "corpid": self._config.corp_id,
            "corpsecret": self._config.secret,
        }
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            try:
                resp = await client.get(_TOKEN_URL, params=params)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise NotifyError(
                    f"获取 access_token HTTP 错误 {exc.response.status_code}: "
                    f"{exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise NotifyError(f"获取 access_token 网络错误: {exc}") from exc

        body: dict[str, Any] = resp.json()
        err_code = body.get("errcode", 0)
        if err_code != 0:
            raise NotifyError(
                f"获取 access_token 失败 errcode={err_code}: {body.get('errmsg')}"
            )

        self._access_token = body["access_token"]
        # 提前 5 分钟（300s）过期，避免边界问题
        expires_in = body.get("expires_in", 7200)
        self._token_expires_at = time.time() + expires_in - 300
        logger.debug("获取 access_token 成功，有效期 {}s", expires_in)
        return self._access_token

    async def _send_message(self, payload: dict[str, Any]) -> None:
        """通过企业微信应用 API 发送消息（支持 text / textcard 等类型）。"""
        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            try:
                resp = await client.post(
                    _SEND_URL,
                    params={"access_token": token},
                    json=payload,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise NotifyError(
                    f"HTTP 错误 {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise NotifyError(f"网络错误: {exc}") from exc

        body: dict[str, Any] = resp.json()
        err_code = body.get("errcode", 0)
        if err_code != 0:
            # Token 过期时清除缓存以便下次刷新
            if err_code in (40014, 42001):
                self._access_token = None
                self._token_expires_at = 0.0
            raise NotifyError(
                f"企业微信错误 errcode={err_code}: {body.get('errmsg')}"
            )
        logger.info("企业微信应用消息已发送")
