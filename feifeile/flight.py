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

# 遇到以下 HTTP 状态码时自动重试（网关/上游瞬态故障）
_RETRYABLE_STATUS_CODES = {502, 503, 504}
# 首次重试等待秒数，后续指数退避 (2s → 4s → 8s ...)
_RETRY_BASE_DELAY = 2.0

# 标准请求头（与 auth 模块一致）
_DEFAULT_HEADERS: dict[str, str] = {
    "Content-Type": "application/json",
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
    price: float            # 最低含税票价（元）
    currency: str = "CNY"
    seats_remaining: int = 0  # 剩余座位数（0 表示未知）
    is_member_price: bool = False  # 是否为会员专属价

    def __str__(self) -> str:
        tag = "【会员特价】" if self.is_member_price else ""
        return (
            f"{tag}{self.flight_no} "
            f"{self.origin}→{self.destination} "
            f"{self.depart_date} {self.depart_time}-{self.arrive_time} "
            f"¥{self.price:.0f}"
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
            logger.warning("普通航班查询失败: {}", exc)

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
            logger.warning("会员特价查询失败: {}", exc)

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
            "dptCity": origin,
            "arrCity": destination,
            "dptDate": date_str,
            "tripType": "1",
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
        flights: list[dict[str, Any]] = (
            result.get("flightList")
            or result.get("flights")
            or result.get("data")
            or []
        )
        return flights

    async def _query_member_fares(
        self,
        origin: str,
        destination: str,
        date_str: str,
        headers: dict[str, str],
        access_token: str,
    ) -> list[dict[str, Any]]:
        data_params: dict[str, Any] = {
            "dptCity": origin,
            "arrCity": destination,
            "dptDate": date_str,
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
        fares: list[dict[str, Any]] = (
            result.get("fareList")
            or result.get("fares")
            or result.get("data")
            or []
        )
        return fares

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
        resp = None

        async with httpx.AsyncClient(timeout=self._config.timeout) as client:
            for attempt in range(max_retries + 1):
                try:
                    resp = await client.post(
                        url, json=payload, headers=headers, params=params,
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
                    raise FlightSearchError(
                        f"网络错误（已重试 {max_retries} 次）: {exc}"
                    ) from exc
                else:
                    break

        body: dict[str, Any] = resp.json()  # type: ignore[union-attr]
        if not body.get("success", False):
            raise FlightSearchError(
                f"业务错误 {body.get('errorCode')}: "
                f"{body.get('errorMessage')}"
            )
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
                price = _extract_price(item)
                if price is None:
                    continue
                offers.append(
                    FlightOffer(
                        flight_no=item.get("flightNo") or item.get("flight_no") or "",
                        origin=item.get("dptAirport") or item.get("dptCity") or origin,
                        destination=item.get("arrAirport") or item.get("arrCity") or destination,
                        depart_date=date_str,
                        depart_time=item.get("dptTime") or item.get("departTime") or "",
                        arrive_time=item.get("arrTime") or item.get("arrivalTime") or "",
                        cabin_class=item.get("cabinClass") or item.get("cabin") or "Y",
                        price=price,
                        seats_remaining=int(item.get("seatCount") or item.get("seats") or 0),
                        is_member_price=False,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("跳过无效航班数据 {}: {}", item, exc)
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
                price = _extract_price(item)
                if price is None:
                    continue
                offers.append(
                    FlightOffer(
                        flight_no=item.get("flightNo") or item.get("flight_no") or "",
                        origin=item.get("dptAirport") or item.get("dptCity") or origin,
                        destination=item.get("arrAirport") or item.get("arrCity") or destination,
                        depart_date=date_str,
                        depart_time=item.get("dptTime") or item.get("departTime") or "",
                        arrive_time=item.get("arrTime") or item.get("arrivalTime") or "",
                        cabin_class=item.get("cabinClass") or item.get("cabin") or "Y",
                        price=price,
                        seats_remaining=int(item.get("seatCount") or item.get("seats") or 0),
                        is_member_price=True,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("跳过无效会员票数据 {}: {}", item, exc)
        return offers


def _extract_price(item: dict[str, Any]) -> float | None:
    """从不同字段名中提取最低票价（元）。"""
    for key in ("price", "salePrice", "lowestPrice", "minPrice", "totalPrice", "fare"):
        val = item.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None
