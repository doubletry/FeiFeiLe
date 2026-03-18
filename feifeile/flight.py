"""海南航空航班搜索与解析模块

调用移动端 API 搜索指定日期、航线的航班，
并筛选出满足会员特价（≤ 阈值）的票价。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from loguru import logger

from feifeile.auth import (
    AuthError,
    HNAAuth,
    _build_common_params,
    _compute_sign,
)
from feifeile.config import HNAConfig

# 航班查询接口路径（低价搜索，含普通及会员价）
_FLIGHT_SEARCH_PATH = "/ticket/lfs/airLowFareSearch"
# 会员专属特价接口（同一端点，携带会员 Token 返回会员价）
_MEMBER_PRICE_PATH = "/ticket/lfs/airLowFareSearch"

# 遇到以下 HTTP 状态码时自动重试（网关/上游瞬态故障 + Cloudflare 专属瞬态错误）
_RETRYABLE_STATUS_CODES = {502, 503, 504, 520, 521, 522, 523, 524, 530}
# 首次重试等待秒数，后续指数退避 (2s → 4s → 8s ...)
_RETRY_BASE_DELAY = 2.0

# 标准请求头（与 auth 模块一致，包含 User-Agent 以避免 Cloudflare 拦截）
_DEFAULT_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; Pixel 6 Pro) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://m.hnair.com",
    "Referer": "https://m.hnair.com/",
    "appver": "{app_version}",
    "hna-app": "APP",
    "hna-channel": "HTML5",
}


@dataclass
class FlightOffer:
    """单条航班报价"""

    flight_no: str          # 航班号，如 HU7822
    origin: str             # 出发三字码，如 HAK
    destination: str        # 到达三字码，如 PEK
    depart_date: str        # 出发日期，YYYY-MM-DD
    depart_time: str        # 出发时刻，HH:MM
    arrive_time: str        # 到达时刻，HH:MM
    cabin_class: str        # 舱位代码
    price: float            # 机票价格（不含燃油基建）
    tax: float = 0.0        # 燃油基建费
    currency: str = "CNY"
    seats_remaining: int = 0  # 剩余座位数（0 表示未知）
    is_member_price: bool = False  # 是否为会员专属价

    @property
    def total_price(self) -> float:
        """含税总价（机票 + 燃油基建）"""
        return self.price + self.tax

    def __str__(self) -> str:
        tag = "【会员特价】" if self.is_member_price else ""
        tax_info = f"（+燃油基建¥{self.tax:.0f}）" if self.tax > 0 else ""
        return (
            f"{tag}{self.flight_no} "
            f"{self.origin}→{self.destination} "
            f"{self.depart_date} {self.depart_time}-{self.arrive_time} "
            f"¥{self.price:.0f}{tax_info}"
        )


class FlightSearchError(Exception):
    """航班查询相关错误"""


class FlightSearchClient:
    """航班查询客户端

    Example::

        config = HNAConfig(username="...", password="...")
        auth = HNAAuth(config)
        client = FlightSearchClient(config, auth)
        offers = await client.search("HAK", "PEK", date(2025, 2, 1), threshold=199)
    """

    def __init__(self, config: HNAConfig, auth: HNAAuth) -> None:
        self._config = config
        self._auth = auth

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def search(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        threshold: float = 199.0,
    ) -> list[FlightOffer]:
        """查询指定航线并返回价格 ≤ threshold 的航班列表。"""
        origin = origin.upper()
        destination = destination.upper()
        date_str = depart_date.strftime("%Y-%m-%d")
        logger.info(
            "查询航班 {}->{} {} (阈值 ¥{})",
            origin,
            destination,
            date_str,
            threshold,
        )

        token = await self._auth.get_token()
        headers = self._build_headers(token.bearer)

        offers: list[FlightOffer] = []

        # 1. 通用航班列表查询
        try:
            raw_flights = await self._query_flights(
                origin, destination, date_str, headers, token.access_token
            )
            offers.extend(self._parse_flights(raw_flights, origin, destination, date_str))
        except FlightSearchError as exc:
            logger.exception("普通航班查询失败: {}", exc)

        # 2. 会员专属特价查询（叠加）
        try:
            raw_member = await self._query_member_fares(
                origin, destination, date_str, headers, token.access_token
            )
            member_offers = self._parse_member_fares(
                raw_member, origin, destination, date_str
            )
            # 用会员价覆盖普通价（按航班号去重）
            existing = {o.flight_no for o in offers}
            for mo in member_offers:
                if mo.flight_no in existing:
                    offers = [
                        mo if o.flight_no == mo.flight_no else o
                        for o in offers
                    ]
                else:
                    offers.append(mo)
        except FlightSearchError as exc:
            logger.exception("会员特价查询失败: {}", exc)

        qualified = [o for o in offers if o.price <= threshold]
        logger.info(
            "共找到 {} 个航班，其中 {} 个满足价格条件",
            len(offers),
            len(qualified),
        )
        return qualified

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _build_headers(self, bearer: str) -> dict[str, str]:
        headers = {
            k: v.format(app_version=self._config.app_version)
            for k, v in _DEFAULT_HEADERS.items()
        }
        headers["Authorization"] = bearer
        return headers

    async def _query_flights(
        self,
        origin: str,
        destination: str,
        date_str: str,
        headers: dict[str, str],
        access_token: str,
    ) -> list[dict[str, Any]]:
        data_params: dict[str, Any] = {
            "originDestinations": [
                {
                    "departureDate": date_str,
                    "origin": origin,
                    "originType": "1",       # 1 = 城市代码
                    "destination": destination,
                    "destinationType": "1",  # 1 = 城市代码
                }
            ],
            "passenger": "ADT:1",  # ADT = 成人，1 位
            "type": "OW",          # OW = 单程
            "adultCount": "1",
            "childCount": "0",
            "infantCount": "0",
        }
        common = _build_common_params(self._config)
        body = {
            "common": common,
            "data": data_params,
        }
        url = f"{self._config.base_url}{_FLIGHT_SEARCH_PATH}"
        flat_params = {**common, **data_params}
        query: dict[str, str] = {"token": access_token}
        sign = _compute_sign(
            headers, query, flat_params,
            self._config.certificate_hash, self._config.hard_code,
        )
        query["hnairSign"] = sign
        result = await self._post(
            url, body, headers, params=query,
        )
        return _extract_itineraries(result)

    async def _query_member_fares(
        self,
        origin: str,
        destination: str,
        date_str: str,
        headers: dict[str, str],
        access_token: str,
    ) -> list[dict[str, Any]]:
        data_params: dict[str, Any] = {
            "originDestinations": [
                {
                    "departureDate": date_str,
                    "origin": origin,
                    "originType": "1",       # 1 = 城市代码
                    "destination": destination,
                    "destinationType": "1",  # 1 = 城市代码
                }
            ],
            "passenger": "ADT:1",  # ADT = 成人，1 位
            "type": "OW",          # OW = 单程
        }
        common = _build_common_params(self._config)
        body = {
            "common": common,
            "data": data_params,
        }
        url = f"{self._config.base_url}{_MEMBER_PRICE_PATH}"
        flat_params = {**common, **data_params}
        query: dict[str, str] = {"token": access_token}
        sign = _compute_sign(
            headers, query, flat_params,
            self._config.certificate_hash, self._config.hard_code,
        )
        query["hnairSign"] = sign
        result = await self._post(
            url, body, headers, params=query,
        )
        return _extract_itineraries(result)

    async def _post(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """发送 POST 请求并返回业务数据字典。

        对 502/503/504 及 Cloudflare 520-524/530 等瞬态错误自动重试（指数退避）。
        """
        max_retries = self._config.max_retries
        resp = None

        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    resp = await client.post(
                        url, json=payload, headers=headers, params=params,
                    )
                    logger.debug(
                        "POST {} -> HTTP {}（attempt {}/{}）",
                        url, resp.status_code, attempt + 1, max_retries + 1,
                    )
                    if resp.status_code == 401:
                        await self._auth.invalidate()
                        raise FlightSearchError("认证过期（401），请重新运行")
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
                    resp_text = exc.response.text or "(empty)"
                    if len(resp_text) > 500:
                        resp_text = resp_text[:500] + "...(truncated)"
                    logger.error(
                        "请求 {} 返回 HTTP 错误 {}，响应: {}",
                        url, exc.response.status_code, resp_text,
                    )
                    raise FlightSearchError(
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
                    logger.exception(
                        "请求 {} 网络错误（已重试 {} 次）", url, max_retries,
                    )
                    raise FlightSearchError(
                        f"网络错误（已重试 {max_retries} 次）: {exc}"
                    ) from exc
                else:
                    break

        body: dict[str, Any] = resp.json()  # type: ignore[union-attr]
        logger.debug("API 响应 success={}, keys={}", body.get("success"), list(body.keys()))
        if not body.get("success", False):
            code = body.get("errorCode") or "UNKNOWN"
            msg = body.get("errorMessage") or "响应格式异常"
            logger.warning("业务错误 code={}, message={}, body={}", code, msg, body)
            raise FlightSearchError(f"业务错误 {code}: {msg}")
        return body.get("data") or body

    @staticmethod
    def _parse_flights(
        raw: list[dict[str, Any]],
        origin: str,
        destination: str,
        date_str: str,
    ) -> list[FlightOffer]:
        offers: list[FlightOffer] = []
        for item in raw:
            try:
                offer = _itinerary_to_offer(item, origin, destination, date_str, is_member=False)
                if offer is not None:
                    offers.append(offer)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("跳过无效航班数据: {}", exc)
        return offers

    @staticmethod
    def _parse_member_fares(
        raw: list[dict[str, Any]],
        origin: str,
        destination: str,
        date_str: str,
    ) -> list[FlightOffer]:
        offers: list[FlightOffer] = []
        for item in raw:
            try:
                offer = _itinerary_to_offer(item, origin, destination, date_str, is_member=True)
                if offer is not None:
                    offers.append(offer)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("跳过无效会员票数据: {}", exc)
        return offers


def _extract_itineraries(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 API 响应 data 中提取航班行程列表。

    HNA airLowFareSearch 响应格式::

        {
            "originDestinations": [
                {
                    "airItineraries": [ ... ]
                }
            ]
        }
    """
    # 新版 API：originDestinations[0].airItineraries
    ods = result.get("originDestinations") or []
    if ods:
        itineraries: list[dict[str, Any]] = []
        for od in ods:
            items = od.get("airItineraries") or []
            itineraries.extend(items)
        if itineraries:
            logger.debug("从 originDestinations 提取到 {} 条航班", len(itineraries))
            return itineraries

    # 兼容旧版扁平格式
    for key in ("flightList", "flights", "airItineraries"):
        val = result.get(key)
        if isinstance(val, list) and val:
            logger.debug("从 {} 提取到 {} 条航班", key, len(val))
            return val

    logger.warning("API 响应中未找到航班数据，响应 keys: {}", list(result.keys()))
    return []


def _itinerary_to_offer(
    item: dict[str, Any],
    origin: str,
    destination: str,
    date_str: str,
    *,
    is_member: bool,
) -> FlightOffer | None:
    """将一条 airItinerary 转换为 FlightOffer。

    支持两种格式：
    1. 新版嵌套格式（flightSegments + minLowPriceWithTax）
    2. 旧版扁平格式（flightNo + price）
    """
    segments = item.get("flightSegments") or []

    if segments:
        # ---- 新版嵌套格式 ----
        seg = segments[0]
        airline = seg.get("marketingAirlineCode") or ""
        flight_num = seg.get("flightNumber") or ""
        flight_no = f"{airline}{flight_num}" if airline and flight_num else (airline or flight_num)

        dep_airport = seg.get("departureAirportCode") or origin
        arr_airport = seg.get("arrivalAirportCode") or destination
        dep_time = seg.get("departureTime") or ""
        arr_time = seg.get("arrivalTime") or ""
        cabin = seg.get("bookingClass") or seg.get("cabinClass") or "Y"

        # 已售罄跳过
        sold_out = item.get("soldOut")
        if sold_out == "1" or sold_out is True:
            logger.debug("航班 {} 已售罄，跳过", flight_no)
            return None

        price = _extract_price_from_itinerary(item)
        base_price = _extract_base_price_from_itinerary(item)
        if price is None and base_price is None:
            logger.debug("航班 {} 无有效价格，跳过", flight_no)
            return None

        # price = 含税总价, base_price = 不含税基础票价
        if base_price is None:
            base_price = price
        if price is None:
            price = base_price
        tax = max(price - base_price, 0)

        inv = item.get("inventoryQuantity")
        seats = _safe_int(inv if inv is not None else item.get("seatCount"), default=0)

        return FlightOffer(
            flight_no=flight_no,
            origin=dep_airport,
            destination=arr_airport,
            depart_date=seg.get("departureDate") or date_str,
            depart_time=dep_time,
            arrive_time=arr_time,
            cabin_class=cabin,
            price=base_price,
            tax=tax,
            seats_remaining=seats,
            is_member_price=is_member,
        )

    # ---- 旧版扁平格式（兼容） ----
    price = _extract_price(item)
    if price is None:
        return None
    return FlightOffer(
        flight_no=item.get("flightNo") or item.get("flight_no") or "",
        origin=item.get("dptAirport") or item.get("dptCity") or origin,
        destination=item.get("arrAirport") or item.get("arrCity") or destination,
        depart_date=date_str,
        depart_time=item.get("dptTime") or item.get("departTime") or "",
        arrive_time=item.get("arrTime") or item.get("arrivalTime") or "",
        cabin_class=item.get("cabinClass") or item.get("cabin") or "Y",
        price=price,
        seats_remaining=_safe_int(item.get("seatCount") or item.get("seats"), default=0),
        is_member_price=is_member,
    )


def _safe_int(value: Any, default: int = 0) -> int:
    """安全地将值转换为整数，非数值型（如 'A'）返回 default。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _extract_base_price_from_itinerary(item: dict[str, Any]) -> float | None:
    """从 airItinerary 中提取不含税的基础票价。

    优先级：lowestPrice > minLowPrice > lowestPriceY
    """
    for key in ("lowestPrice", "minLowPrice", "lowestPriceY"):
        val = item.get(key)
        if val is not None:
            try:
                p = float(val)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def _extract_price_from_itinerary(item: dict[str, Any]) -> float | None:
    """从 airItinerary 中提取最低含税票价。

    优先级：minLowPriceWithTax > lowestPrice > minLowPrice >
            airItineraryPrices[0].travelerPrices[0].farePrices[0].totalFare
    """
    for key in (
        "minLowPriceWithTax", "lowestPrice", "minLowPrice",
        "minLowPriceWithTaxY", "lowestPriceY",
    ):
        val = item.get(key)
        if val is not None:
            try:
                p = float(val)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue

    # 尝试从 airItineraryPrices 中提取
    prices = item.get("airItineraryPrices") or []
    if prices:
        try:
            tp = prices[0].get("travelerPrices") or []
            if tp:
                fp = tp[0].get("farePrices") or []
                if fp:
                    total = fp[0].get("totalFare")
                    if total is not None:
                        return float(total)
        except (IndexError, KeyError, TypeError, ValueError):
            pass

    return None


def _extract_price(item: dict[str, Any]) -> float | None:
    """从扁平格式数据中提取票价（兼容旧版 API 响应）。"""
    for key in ("price", "salePrice", "lowestPrice", "minPrice", "totalPrice", "fare"):
        val = item.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None
