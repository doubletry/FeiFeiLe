"""tests/test_notifier.py — 企业微信通知模块单元测试"""

from __future__ import annotations

import httpx
import pytest
import respx

from feifeile.config import WeComConfig
from feifeile.flight import FlightOffer
from feifeile.notifier import NotifyError, WeComNotifier


WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"


@pytest.fixture
def wecom_config(monkeypatch):
    monkeypatch.setenv("WECOM_WEBHOOK_URL", WEBHOOK_URL)
    return WeComConfig()


@pytest.fixture
def sample_offers():
    return [
        FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
            seats_remaining=3,
            is_member_price=True,
        ),
        FlightOffer(
            flight_no="HU7824",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="14:00",
            arrive_time="18:00",
            cabin_class="Y",
            price=150.0,
            seats_remaining=0,
            is_member_price=False,
        ),
    ]


class TestWeComNotifier:
    @pytest.mark.asyncio
    async def test_send_flight_alerts_success(self, wecom_config, sample_offers):
        with respx.mock:
            respx.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
            )
            notifier = WeComNotifier(wecom_config)
            # Should not raise
            await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_send_flight_alerts_empty_skips(self, wecom_config):
        """空列表不应触发 HTTP 请求。"""
        with respx.mock:
            notifier = WeComNotifier(wecom_config)
            await notifier.send_flight_alerts([], threshold=199.0)
            # respx will raise if any unexpected request is made

    @pytest.mark.asyncio
    async def test_send_text_success(self, wecom_config):
        with respx.mock:
            respx.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
            )
            notifier = WeComNotifier(wecom_config)
            await notifier.send_text("测试消息")

    @pytest.mark.asyncio
    async def test_send_raises_on_wecom_error(self, wecom_config, sample_offers):
        with respx.mock:
            respx.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(
                    200, json={"errcode": 93000, "errmsg": "invalid webhook url"}
                )
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="企业微信错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_send_raises_on_http_error(self, wecom_config, sample_offers):
        with respx.mock:
            respx.post(WEBHOOK_URL).mock(
                return_value=httpx.Response(500)
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="HTTP 错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_send_raises_on_network_error(self, wecom_config, sample_offers):
        with respx.mock:
            respx.post(WEBHOOK_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="网络错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    def test_build_markdown_contains_flight_info(self, wecom_config, sample_offers):
        notifier = WeComNotifier(wecom_config)
        md = notifier._build_markdown(sample_offers, 199.0)
        assert "HU7822" in md
        assert "HAK" in md
        assert "PEK" in md
        assert "199" in md
        assert "会员特价" in md
        assert "2" in md  # 2 个航班
