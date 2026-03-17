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
from feifeile.flight import (
    FlightOffer,
    FlightSearchClient,
    FlightSearchError,
    _extract_price,
    _extract_price_from_itinerary,
    _extract_itineraries,
    _itinerary_to_offer,
)

# 新版航班搜索端点（普通查询与会员价使用同一路径）
_SEARCH_URL_SUFFIX = "/ticket/lfs/airLowFareSearch"


def _make_itinerary(
    flight_no: str = "7822",
    airline: str = "HU",
    dep_airport: str = "HAK",
    arr_airport: str = "PEK",
    dep_date: str = "2025-02-01",
    dep_time: str = "08:00",
    arr_time: str = "12:00",
    cabin: str = "Y",
    price: float = 199,
    seats: int = 5,
    sold_out: str = "0",
) -> dict:
    """构造一个逼真的 airItinerary 对象（与 HNA API 一致）。"""
    return {
        "flightSegments": [
            {
                "marketingAirlineCode": airline,
                "flightNumber": flight_no,
                "departureAirportCode": dep_airport,
                "arrivalAirportCode": arr_airport,
                "departureDate": dep_date,
                "departureTime": dep_time,
                "arrivalTime": arr_time,
                "bookingClass": cabin,
            }
        ],
        "minLowPriceWithTax": price,
        "lowestPrice": price - 50,
        "minLowPrice": price - 50,
        "inventoryQuantity": seats,
        "soldOut": sold_out,
    }


def _wrap_response(itineraries: list[dict], success: bool = True) -> dict:
    """将 airItinerary 列表包装为完整 API 响应。"""
    return {
        "success": success,
        "data": {
            "originDestinations": [
                {"airItineraries": itineraries}
            ],
        },
    }


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


def _search_url(cfg: HNAConfig) -> str:
    return f"{cfg.base_url}{_SEARCH_URL_SUFFIX}"


# ===================================================================
# 价格提取
# ===================================================================

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


class TestExtractPriceFromItinerary:
    def test_min_low_price_with_tax(self):
        assert _extract_price_from_itinerary({"minLowPriceWithTax": 299}) == 299.0

    def test_lowest_price(self):
        assert _extract_price_from_itinerary({"lowestPrice": 199}) == 199.0

    def test_from_itinerary_prices(self):
        item = {
            "airItineraryPrices": [
                {
                    "travelerPrices": [
                        {"farePrices": [{"totalFare": "399"}]}
                    ]
                }
            ]
        }
        assert _extract_price_from_itinerary(item) == 399.0

    def test_no_price(self):
        assert _extract_price_from_itinerary({}) is None

    def test_zero_price_skipped(self):
        assert _extract_price_from_itinerary({"minLowPriceWithTax": 0}) is None


# ===================================================================
# 行程提取
# ===================================================================

class TestExtractItineraries:
    def test_origin_destinations_format(self):
        data = {
            "originDestinations": [
                {"airItineraries": [{"id": "1"}, {"id": "2"}]}
            ]
        }
        assert len(_extract_itineraries(data)) == 2

    def test_flat_flight_list_fallback(self):
        data = {"flightList": [{"id": "1"}]}
        assert len(_extract_itineraries(data)) == 1

    def test_empty_response(self):
        assert _extract_itineraries({}) == []


# ===================================================================
# 行程 → FlightOffer 转换
# ===================================================================

class TestItineraryToOffer:
    def test_nested_format(self):
        itin = _make_itinerary(price=199)
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False)
        assert offer is not None
        assert offer.flight_no == "HU7822"
        assert offer.price == 199.0
        assert offer.origin == "HAK"
        assert not offer.is_member_price

    def test_sold_out_skipped(self):
        itin = _make_itinerary(sold_out="1")
        assert _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False) is None

    def test_sold_out_bool_skipped(self):
        itin = _make_itinerary()
        itin["soldOut"] = True
        assert _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False) is None

    def test_flat_format_fallback(self):
        item = {"flightNo": "HU7822", "price": "199"}
        offer = _itinerary_to_offer(item, "HAK", "PEK", "2025-02-01", is_member=True)
        assert offer is not None
        assert offer.flight_no == "HU7822"
        assert offer.is_member_price


# ===================================================================
# FlightOffer __str__
# ===================================================================

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


# ===================================================================
# FlightSearchClient.search 端到端
# ===================================================================

class TestFlightSearchClient:
    @pytest.mark.asyncio
    async def test_search_returns_qualified_flights(self, hna_config, mock_auth):
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199),
            _make_itinerary(flight_no="7824", dep_time="14:00", arr_time="18:00", price=500),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=flight_response),
                httpx.Response(200, json=member_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        assert offers[0].price == 199.0
        assert not offers[0].is_member_price

    @pytest.mark.asyncio
    async def test_search_member_price_overrides(self, hna_config, mock_auth):
        """会员价应覆盖同航班的普通价。"""
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=299),
        ])
        member_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199),
        ])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=flight_response),
                httpx.Response(200, json=member_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].price == 199.0
        assert offers[0].is_member_price

    @pytest.mark.asyncio
    async def test_search_no_qualified_flights(self, hna_config, mock_auth):
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=500),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=flight_response),
                httpx.Response(200, json=member_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert offers == []

    @pytest.mark.asyncio
    async def test_search_401_invalidates_token(self, hna_config, mock_auth):
        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(401),
                httpx.Response(401),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        # Both queries fail gracefully, result is empty
        assert offers == []
        # invalidate should have been called at least once
        mock_auth.invalidate.assert_called()

    @pytest.mark.asyncio
    async def test_search_gracefully_handles_partial_failure(self, hna_config, mock_auth):
        """普通查询失败时，会员价查询结果仍应返回。"""
        member_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199),
        ])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(500),
                httpx.Response(200, json=member_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].is_member_price

    @pytest.mark.asyncio
    async def test_retry_on_504_then_success(self, hna_config, mock_auth):
        """504 网关超时应自动重试并最终返回结果。"""
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(504),               # 1st attempt of flight query
                httpx.Response(200, json=flight_response),  # retry succeeds
                httpx.Response(200, json=member_response),  # member query
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            import feifeile.flight
            original_delay = feifeile.flight._RETRY_BASE_DELAY
            feifeile.flight._RETRY_BASE_DELAY = 0.01
            try:
                offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)
            finally:
                feifeile.flight._RETRY_BASE_DELAY = original_delay

        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        # 3 calls: 504 retry + flight query success + member query (same endpoint)
        assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_network_error_then_success(self, hna_config, mock_auth):
        """网络异常应自动重试。"""
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7830", price=150),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.ConnectError("connection reset"),     # 1st attempt
                httpx.Response(200, json=flight_response),  # retry succeeds
                httpx.Response(200, json=member_response),  # member query
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            import feifeile.flight
            original_delay = feifeile.flight._RETRY_BASE_DELAY
            feifeile.flight._RETRY_BASE_DELAY = 0.01
            try:
                offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)
            finally:
                feifeile.flight._RETRY_BASE_DELAY = original_delay

        assert len(offers) == 1
        assert offers[0].price == 150.0
        # 3 calls: network error retry + flight query success + member query (same endpoint)
        assert route.call_count == 3

    @pytest.mark.asyncio
    async def test_sold_out_flights_excluded(self, hna_config, mock_auth):
        """已售罄航班不应出现在结果中。"""
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199, sold_out="1"),
            _make_itinerary(flight_no="7824", price=150, sold_out="0"),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=flight_response),
                httpx.Response(200, json=member_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert len(offers) == 1
        assert offers[0].flight_no == "HU7824"

    @pytest.mark.asyncio
    async def test_retry_on_521_cloudflare_then_success(self, hna_config, mock_auth):
        """Cloudflare 521 (Web Server Is Down) 应自动重试并最终返回结果。"""
        flight_response = _wrap_response([
            _make_itinerary(flight_no="7822", price=199),
        ])
        member_response = _wrap_response([])

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(521),               # Cloudflare 521
                httpx.Response(200, json=flight_response),  # retry succeeds
                httpx.Response(200, json=member_response),  # member query
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            import feifeile.flight
            original_delay = feifeile.flight._RETRY_BASE_DELAY
            feifeile.flight._RETRY_BASE_DELAY = 0.01
            try:
                offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)
            finally:
                feifeile.flight._RETRY_BASE_DELAY = original_delay

        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        # 3 calls: 521 retry + flight query success + member query
        assert route.call_count == 3
