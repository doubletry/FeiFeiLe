"""企业微信机器人通知模块

通过企业微信群机器人 Webhook 发送 Markdown 消息。
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from feifeile.config import WeComConfig
from feifeile.flight import FlightOffer


class NotifyError(Exception):
    """通知发送失败"""


class WeComNotifier:
    """企业微信群机器人通知客户端

    Example::

        config = WeComConfig(webhook_url="https://qyapi.weixin.qq.com/...")
        notifier = WeComNotifier(config)
        await notifier.send_flight_alerts([offer1, offer2], threshold=199)
    """

    def __init__(self, config: WeComConfig) -> None:
        self._config = config

    async def send_flight_alerts(
        self,
        offers: list[FlightOffer],
        threshold: float,
    ) -> None:
        """发送航班特价提醒消息。

        若 offers 为空，则跳过发送。
        """
        if not offers:
            logger.debug("无符合条件的航班，跳过通知")
            return

        content = self._build_markdown(offers, threshold)
        await self._send_markdown(content)

    async def send_text(self, text: str) -> None:
        """发送纯文本消息（用于状态播报等）。"""
        payload: dict[str, Any] = {
            "msgtype": "text",
            "text": {"content": text},
        }
        await self._post(payload)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _build_markdown(offers: list[FlightOffer], threshold: float) -> str:
        lines = [
            f"## ✈️ 海南航空特价机票提醒（≤ ¥{threshold:.0f}）",
            f"> 共找到 **{len(offers)}** 个符合条件的航班\n",
        ]
        for offer in offers:
            tag = "🏷️【会员特价】" if offer.is_member_price else ""
            seats = f"，余票 {offer.seats_remaining} 张" if offer.seats_remaining > 0 else ""
            lines.append(
                f"- {tag}**{offer.flight_no}** "
                f"{offer.origin}→{offer.destination} "
                f"{offer.depart_date} {offer.depart_time}→{offer.arrive_time}  "
                f"<font color='warning'>¥{offer.price:.0f}</font>{seats}"
            )
        lines.append("\n> 请及时登录海南航空 App 购买！")
        return "\n".join(lines)

    async def _send_markdown(self, content: str) -> None:
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            try:
                resp = await client.post(
                    self._config.webhook_url, json=payload
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
            raise NotifyError(
                f"企业微信错误 errcode={err_code}: {body.get('errmsg')}"
            )
        logger.info("企业微信通知已发送，消息类型: {}", payload.get("msgtype"))
