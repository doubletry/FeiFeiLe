"""tests/test_notifier.py — 企业微信应用消息通知模块单元测试"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from feifeile.config import WeComConfig
from feifeile.flight import FlightOffer
from feifeile.notifier import NotifyError, WeComNotifier, _TOKEN_URL, _SEND_URL


@pytest.fixture
def wecom_config(monkeypatch):
    monkeypatch.setenv("WECOM_CORP_ID", "ww_test_corp")
    monkeypatch.setenv("WECOM_SECRET", "test_secret")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
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
            price=149.0,
            tax=50.0,
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
            price=100.0,
            tax=50.0,
            seats_remaining=0,
            is_member_price=False,
        ),
    ]


def _mock_token_success():
    """Mock 成功获取 access_token"""
    return respx.get(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "access_token": "test_access_token",
                "expires_in": 7200,
            },
        )
    )


class TestWeComNotifier:
    @pytest.mark.asyncio
    async def test_send_flight_alerts_success(self, wecom_config, sample_offers):
        with respx.mock:
            _mock_token_success()
            respx.post(_SEND_URL).mock(
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
            _mock_token_success()
            respx.post(_SEND_URL).mock(
                return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
            )
            notifier = WeComNotifier(wecom_config)
            await notifier.send_text("测试消息")

    @pytest.mark.asyncio
    async def test_send_raises_on_wecom_error(self, wecom_config, sample_offers):
        with respx.mock:
            _mock_token_success()
            respx.post(_SEND_URL).mock(
                return_value=httpx.Response(
                    200, json={"errcode": 93000, "errmsg": "invalid agentid"}
                )
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="企业微信错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_send_raises_on_http_error(self, wecom_config, sample_offers):
        with respx.mock:
            _mock_token_success()
            respx.post(_SEND_URL).mock(
                return_value=httpx.Response(500)
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="HTTP 错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_send_raises_on_network_error(self, wecom_config, sample_offers):
        with respx.mock:
            _mock_token_success()
            respx.post(_SEND_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="网络错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_get_token_failure(self, wecom_config, sample_offers):
        """获取 access_token 失败时应抛出 NotifyError。"""
        with respx.mock:
            respx.get(_TOKEN_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={"errcode": 40013, "errmsg": "invalid corpid"},
                )
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="access_token"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)

    @pytest.mark.asyncio
    async def test_token_caching(self, wecom_config):
        """第二次调用应复用缓存的 access_token，不再请求 gettoken。"""
        with respx.mock:
            token_route = _mock_token_success()
            respx.post(_SEND_URL).mock(
                return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
            )
            notifier = WeComNotifier(wecom_config)
            await notifier.send_text("第一次")
            await notifier.send_text("第二次")
            # gettoken 应只被调用 1 次
            assert token_route.call_count == 1

    @pytest.mark.asyncio
    async def test_expired_token_cleared(self, wecom_config, sample_offers):
        """发送消息返回 token 过期错误时应清除缓存。"""
        with respx.mock:
            _mock_token_success()
            respx.post(_SEND_URL).mock(
                return_value=httpx.Response(
                    200, json={"errcode": 42001, "errmsg": "access_token expired"}
                )
            )
            notifier = WeComNotifier(wecom_config)
            with pytest.raises(NotifyError, match="企业微信错误"):
                await notifier.send_flight_alerts(sample_offers, threshold=199.0)
            # Token 缓存应已被清除
            assert notifier._access_token is None

    def test_build_textcard_contains_flight_info(self, sample_offers):
        card = WeComNotifier._build_textcard(sample_offers, 199.0)
        assert isinstance(card, dict)
        assert "title" in card
        assert "description" in card
        assert "url" in card
        assert "btntxt" in card
        desc = card["description"]
        assert "HU7822" in desc
        assert "HAK" in desc
        assert "PEK" in desc
        assert "199" in card["title"]
        assert "2" in desc  # 2 个航班
        # 验证机票和税费分别列出
        assert "机票¥149" in desc
        assert "税费¥50" in desc

    def test_build_textcard_no_tax(self):
        offers = [
            FlightOffer(
                flight_no="HU7822",
                origin="HAK",
                destination="PEK",
                depart_date="2025-02-01",
                depart_time="08:00",
                arrive_time="12:00",
                cabin_class="Y",
                price=199.0,
                tax=0.0,
            ),
        ]
        card = WeComNotifier._build_textcard(offers, 199.0)
        desc = card["description"]
        assert "机票¥199" in desc
        assert "税费" not in desc

    @pytest.mark.asyncio
    async def test_send_flight_alerts_sends_textcard(self, wecom_config, sample_offers):
        """验证 send_flight_alerts 发送 textcard 类型而非 text 类型。"""
        with respx.mock:
            _mock_token_success()
            send_route = respx.post(_SEND_URL).mock(
                return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
            )
            notifier = WeComNotifier(wecom_config)
            await notifier.send_flight_alerts(sample_offers, threshold=199.0)
            # 检查发送的 payload 使用了 textcard 类型
            sent_body = json.loads(send_route.calls[0].request.content)
            assert sent_body["msgtype"] == "textcard"
            assert "textcard" in sent_body
            assert "title" in sent_body["textcard"]
            assert "description" in sent_body["textcard"]
