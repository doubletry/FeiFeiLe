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
    _extract_base_price_from_itinerary,
    _extract_itineraries,
    _itinerary_to_offer,
    _safe_int,
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
    tax: float = 50,
    seats: int = 5,
    sold_out: str = "0",
) -> dict:
    """构造一个逼真的 airItinerary 对象（与 HNA API 一致）。

    Args:
        price: 基础票价（不含税），对应 lowestPrice / minLowPrice。
        tax: 燃油基建费，含税总价 = price + tax，对应 minLowPriceWithTax。
    """
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
        "minLowPriceWithTax": price + tax,
        "lowestPrice": price,
        "minLowPrice": price,
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


class TestExtractBasePriceFromItinerary:
    def test_lowest_price(self):
        assert _extract_base_price_from_itinerary({"lowestPrice": 149}) == 149.0

    def test_min_low_price(self):
        assert _extract_base_price_from_itinerary({"minLowPrice": 149}) == 149.0

    def test_no_price(self):
        assert _extract_base_price_from_itinerary({}) is None

    def test_zero_price_skipped(self):
        assert _extract_base_price_from_itinerary({"lowestPrice": 0}) is None

    def test_prefers_lowest_price_over_min_low(self):
        item = {"lowestPrice": 100, "minLowPrice": 120}
        assert _extract_base_price_from_itinerary(item) == 100.0


# ===================================================================
# _safe_int 安全整数转换
# ===================================================================

class TestSafeInt:
    def test_numeric_string(self):
        assert _safe_int("5") == 5

    def test_int_value(self):
        assert _safe_int(10) == 10

    def test_non_numeric_string(self):
        assert _safe_int("A") == 0

    def test_non_numeric_string_custom_default(self):
        assert _safe_int("A", default=-1) == -1

    def test_none_value(self):
        assert _safe_int(None) == 0

    def test_empty_string(self):
        assert _safe_int("") == 0

    def test_float_string(self):
        # "3.5" 不是合法 int 字符串，应返回 default
        assert _safe_int("3.5") == 0

    def test_zero_string(self):
        assert _safe_int("0") == 0


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
        itin = _make_itinerary(price=199, tax=50)
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False)
        assert offer is not None
        assert offer.flight_no == "HU7822"
        assert offer.price == 199.0
        assert offer.tax == 50.0
        assert offer.total_price == 249.0
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
        assert offer.price == 199.0
        assert offer.tax == 0.0
        assert offer.is_member_price

    def test_non_numeric_inventory_quantity(self):
        """inventoryQuantity 为非数字字符串 'A' 时不应崩溃。"""
        itin = _make_itinerary(price=199, tax=50)
        itin["inventoryQuantity"] = "A"
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=True)
        assert offer is not None
        assert offer.seats_remaining == 0
        assert offer.price == 199.0
        assert offer.tax == 50.0

    def test_actual_api_segment_format(self):
        """使用实际 HNA API 返回的航段格式验证解析。"""
        actual_segment = {
            "cabinClass": "",
            "charter": False,
            "cutoffTime": "38",
            "bookingClass": "",
            "displayAircraftName": "空客320",
            "arrivalTime": "12:20",
            "id": "FLT-PN6333#SZX-TNA-20260404",
            "international": False,
            "operatingAirlineCode": "PN",
            "departureAirportCode": "SZX",
            "arrivalAirportCode": "TNA",
            "flightNumber": "6333",
            "arrivalDate": "2026-04-04",
            "departureTime": "09:40",
            "departureDate": "2026-04-04",
            "departureTerminal": "T3",
            "marketingAirlineCode": "PN",
            "aircraftCode": "320",
            "stopQuantity": 0,
            "duration": 160,
        }
        itin = {
            "flightSegments": [actual_segment],
            "minLowPriceWithTax": 399,
            "lowestPrice": 349,
            "inventoryQuantity": "A",
            "soldOut": "0",
        }
        offer = _itinerary_to_offer(itin, "SZX", "TNA", "2026-04-04", is_member=True)
        assert offer is not None
        assert offer.flight_no == "PN6333"
        assert offer.origin == "SZX"
        assert offer.destination == "TNA"
        assert offer.depart_time == "09:40"
        assert offer.arrive_time == "12:20"
        assert offer.depart_date == "2026-04-04"
        assert offer.price == 349.0       # 基础票价（不含税）
        assert offer.tax == 50.0          # 燃油基建费 = 399 - 349
        assert offer.total_price == 399.0 # 含税总价
        assert offer.seats_remaining == 0  # 'A' → default 0
        assert offer.cabin_class == "Y"  # empty bookingClass & cabinClass → default "Y"
        assert offer.is_member_price

    def test_cabin_class_fallback_to_cabin_class_field(self):
        """bookingClass 为空时应回退到 cabinClass 字段。"""
        itin = _make_itinerary(cabin="", price=199)
        itin["flightSegments"][0]["cabinClass"] = "W"
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False)
        assert offer is not None
        assert offer.cabin_class == "W"

    def test_only_total_price_available(self):
        """只有含税总价、无基础票价时，税费为 0。"""
        itin = _make_itinerary(price=199, tax=50)
        del itin["lowestPrice"]
        del itin["minLowPrice"]
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False)
        assert offer is not None
        assert offer.price == 249.0  # minLowPriceWithTax 作为基础票价
        assert offer.tax == 0.0      # 无法区分税费

    def test_only_base_price_available(self):
        """只有基础票价、无含税总价时，税费为 0。"""
        itin = _make_itinerary(price=199, tax=50)
        del itin["minLowPriceWithTax"]
        offer = _itinerary_to_offer(itin, "HAK", "PEK", "2025-02-01", is_member=False)
        assert offer is not None
        assert offer.price == 199.0  # 基础票价
        assert offer.tax == 0.0      # 总价未知，无法计算税费


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
        # 无税费时不显示燃油基建信息
        assert "燃油基建" not in str(offer)

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

    def test_str_with_tax(self):
        offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=149.0,
            tax=50.0,
        )
        s = str(offer)
        assert "¥149" in s
        assert "燃油基建¥50" in s
        assert offer.total_price == 199.0

    def test_total_price_property(self):
        offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date="2025-02-01",
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=149.0,
            tax=50.0,
        )
        assert offer.total_price == 199.0


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

    @pytest.mark.asyncio
    async def test_search_no_flights_business_error_returns_empty(self, hna_config, mock_auth):
        """API 返回业务错误（当天无航班）时应正常返回空列表，不抛出异常。"""
        no_flight_response = {
            "success": False,
            "errorCode": "NO_DATA",
            "errorMessage": "没有可用航班",
        }

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=no_flight_response),  # 普通查询：无航班
                httpx.Response(200, json=no_flight_response),  # 会员查询：无航班
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert offers == []

    @pytest.mark.asyncio
    async def test_search_no_flights_empty_itineraries_returns_empty(self, hna_config, mock_auth):
        """API 返回成功但航班列表为空时应正常返回空列表。"""
        empty_response = _wrap_response([])  # success=True, 但无航班

        with respx.mock:
            route = respx.post(_search_url(hna_config))
            route.side_effect = [
                httpx.Response(200, json=empty_response),
                httpx.Response(200, json=empty_response),
            ]

            client = FlightSearchClient(hna_config, mock_auth)
            offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199.0)

        assert offers == []
