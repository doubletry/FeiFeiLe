"""tests/test_flight.py — 航班搜索模块单元测试"""

from __future__ import annotations

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from feifeile.auth import AuthToken, HNAAuth
from feifeile.config import HNAConfig
from feifeile.flight import FlightOffer, FlightSearchClient, FlightSearchError, _extract_price


@pytest.fixture
def hna_config(monkeypatch):
    monkeypatch.setenv("HNA_USERNAME", "13800000000")
    monkeypatch.setenv("HNA_PASSWORD", "test_password")
    return HNAConfig()


@pytest.fixture
def mock_auth(hna_config):
    auth = MagicMock(spec=HNAAuth)
    auth.get_token = AsyncMock(
        return_value=AuthToken(
            access_token="test_token",
            refresh_token="test_ref",
            expires_at=time.time() + 7200,
        )
    )
    auth.invalidate = AsyncMock()
    return auth


class TestExtractPrice:
    def test_price_field(self):
        assert _extract_price({"price": "199.0"}) == 199.0

    def test_sale_price_field(self):
        assert _extract_price({"salePrice": 299}) == 299.0

    def test_lowest_price_field(self):
        assert _extract_price({"lowestPrice": 150.5}) == 150.5

    def test_no_price(self):
        assert _extract_price({"cabinClass": "Y"}) is None

    def test_invalid_price(self):
        assert _extract_price({"price": "abc"}) is None


class TestFlightOffer:
    def test_str_normal(self):
        offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
        )
        assert "HU7822" in str(offer)
        assert "¥199" in str(offer)
        assert "【会员特价】" not in str(offer)

    def test_str_member_price(self):
        offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
            is_member_price=True,
        )
        assert "【会员特价】" in str(offer)


class TestFlightSearchClient:
    @pytest.mark.asyncio
    async def test_search_returns_qualified_flights(self, hna_config, mock_auth):
        flight_response = {
            "code": "0",
            "data": {
                "flightList": [
                    {
                        "flightNo": "HU7822",
                        "dptAirport": "HAK",
                        "arrAirport": "PEK",
                        "dptTime": "08:00",
                        "arrTime": "12:00",
                        "cabinClass": "Y",
                        "price": "199",
                        "seatCount": "5",
                    },
                    {
                        "flightNo": "HU7824",
                        "dptAirport": "HAK",
                        "arrAirport": "PEK",
                        "dptTime": "14:00",
                        "arrTime": "18:00",
                        "cabinClass": "Y",
                        "price": "500",  # 超过阈值
                        "seatCount": "10",
                    },
                ]
            },
        }
        member_response = {"code": "0", "data": {"fareList": []}}

        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/flight/queryFlightInfo"
            ).mock(return_value=httpx.Response(200, json=flight_response))
            respx.post(
                f"{hna_config.base_url}/hnapps/member/flight/memberFares"
            ).mock(return_value=httpx.Response(200, json=member_response))

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        assert offers[0].price == 199.0
        assert not offers[0].is_member_price

    @pytest.mark.asyncio
    async def test_search_member_price_overrides(self, hna_config, mock_auth):
        """会员价应覆盖同航班的普通价。"""
        flight_response = {
            "code": "0",
            "data": {
                "flightList": [
                    {
                        "flightNo": "HU7822",
                        "dptAirport": "HAK",
                        "arrAirport": "PEK",
                        "dptTime": "08:00",
                        "arrTime": "12:00",
                        "cabinClass": "Y",
                        "price": "299",
                    },
                ]
            },
        }
        member_response = {
            "code": "0",
            "data": {
                "fareList": [
                    {
                        "flightNo": "HU7822",
                        "dptAirport": "HAK",
                        "arrAirport": "PEK",
                        "dptTime": "08:00",
                        "arrTime": "12:00",
                        "cabinClass": "Y",
                        "price": "199",  # 会员特价
                    }
                ]
            },
        }

        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/flight/queryFlightInfo"
            ).mock(return_value=httpx.Response(200, json=flight_response))
            respx.post(
                f"{hna_config.base_url}/hnapps/member/flight/memberFares"
            ).mock(return_value=httpx.Response(200, json=member_response))

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].price == 199.0
        assert offers[0].is_member_price

    @pytest.mark.asyncio
    async def test_search_no_qualified_flights(self, hna_config, mock_auth):
        flight_response = {
            "code": "0",
            "data": {
                "flightList": [
                    {
                        "flightNo": "HU7822",
                        "price": "500",
                    }
                ]
            },
        }
        member_response = {"code": "0", "data": {"fareList": []}}

        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/flight/queryFlightInfo"
            ).mock(return_value=httpx.Response(200, json=flight_response))
            respx.post(
                f"{hna_config.base_url}/hnapps/member/flight/memberFares"
            ).mock(return_value=httpx.Response(200, json=member_response))

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert offers == []

    @pytest.mark.asyncio
    async def test_search_401_invalidates_token(self, hna_config, mock_auth):
        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/flight/queryFlightInfo"
            ).mock(return_value=httpx.Response(401))
            # member price also fails
            respx.post(
                f"{hna_config.base_url}/hnapps/member/flight/memberFares"
            ).mock(return_value=httpx.Response(401))

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        # Both queries fail gracefully, result is empty
        assert offers == []
        # invalidate should have been called at least once
        mock_auth.invalidate.assert_called()

    @pytest.mark.asyncio
    async def test_search_gracefully_handles_partial_failure(self, hna_config, mock_auth):
        """普通查询失败时，会员价查询结果仍应返回。"""
        member_response = {
            "code": "0",
            "data": {
                "fareList": [
                    {
                        "flightNo": "HU7822",
                        "dptAirport": "HAK",
                        "arrAirport": "PEK",
                        "dptTime": "08:00",
                        "arrTime": "12:00",
                        "cabinClass": "Y",
                        "price": "199",
                    }
                ]
            },
        }

        with respx.mock:
            respx.post(
                f"{hna_config.base_url}/hnapps/flight/queryFlightInfo"
            ).mock(return_value=httpx.Response(500))
            respx.post(
                f"{hna_config.base_url}/hnapps/member/flight/memberFares"
            ).mock(return_value=httpx.Response(200, json=member_response))

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].is_member_price
