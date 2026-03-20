"""订阅管理与监控执行模块

负责：
- 订阅（航线 + 日期 + 价格阈值）的增删查
- 单次轮询：对所有有效订阅执行查询并发送通知
- 订阅持久化到 JSON 文件
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from feifeile.auth import CaptchaRequiredError, HNAAuth
from feifeile.config import HNAConfig, MonitorConfig, WeComConfig
from feifeile.flight import FlightOffer, FlightSearchClient
from feifeile.notifier import WeComNotifier


@dataclass
class Subscription:
    """一条订阅记录"""

    id: str                 # 唯一标识，由调用方生成（如 UUID 短串）
    origin: str             # 出发机场三字码
    destination: str        # 到达机场三字码
    depart_date: str        # 出发日期，YYYY-MM-DD
    price_threshold: float  # 触发通知的价格上限（元）
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    active: bool = True     # False 时跳过此订阅

    @property
    def depart_date_obj(self) -> date:
        return date.fromisoformat(self.depart_date)

    def is_expired(self) -> bool:
        """出发日期已过则视为过期。"""
        return self.depart_date_obj < date.today()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Subscription":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})  # type: ignore[attr-defined]


class SubscriptionStore:
    """基于 JSON 文件的订阅持久化存储"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._subscriptions: list[Subscription] = []
        self._load()

    # ------------------------------------------------------------------

    def add(self, sub: Subscription) -> None:
        self._subscriptions.append(sub)
        self._save()

    def remove(self, sub_id: str) -> bool:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.id != sub_id]
        changed = len(self._subscriptions) < before
        if changed:
            self._save()
        return changed

    def list_active(self) -> list[Subscription]:
        return [s for s in self._subscriptions if s.active and not s.is_expired()]

    def list_all(self) -> list[Subscription]:
        return list(self._subscriptions)

    def deactivate_expired(self) -> int:
        """将已过期的订阅标记为不活跃，返回处理数量。"""
        count = 0
        for sub in self._subscriptions:
            if sub.active and sub.is_expired():
                sub.active = False
                count += 1
        if count:
            self._save()
        return count

    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(self._path):
            self._subscriptions = []
            return
        with open(self._path, encoding="utf-8") as f:
            try:
                raw: list[dict[str, Any]] = json.load(f)
                self._subscriptions = [Subscription.from_dict(r) for r in raw]
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("订阅文件解析失败，重置: {}", exc)
                self._subscriptions = []

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._subscriptions],
                f,
                ensure_ascii=False,
                indent=2,
            )


class Monitor:
    """监控执行器

    协调认证、查询、通知三个子系统，
    对所有活跃订阅执行一次完整的查询 → 通知流程。
    """

    def __init__(
        self,
        hna_config: HNAConfig,
        wecom_config: WeComConfig | None,
        monitor_config: MonitorConfig,
        store: SubscriptionStore,
        *,
        dry_run: bool = False,
        token_file: str | Path | None = None,
    ) -> None:
        self._hna_config = hna_config
        self._monitor_config = monitor_config
        self._store = store
        self._dry_run = dry_run
        kwargs = {}
        if token_file is not None:
            kwargs["token_file"] = token_file
        self._auth = HNAAuth(hna_config, **kwargs)
        self._search = FlightSearchClient(hna_config, self._auth)
        self._notifier = WeComNotifier(wecom_config) if wecom_config and not dry_run else None

    async def run_once(self) -> dict[str, list[FlightOffer]]:
        """执行一次轮询，返回各订阅 ID -> 命中航班列表的映射。"""
        expired = self._store.deactivate_expired()
        if expired:
            logger.info("已清理 {} 条过期订阅", expired)

        active = self._store.list_active()
        if not active:
            logger.info("当前没有活跃的订阅")
            return {}

        # 预先登录一次，后续所有订阅复用同一 Token
        try:
            await self._auth.get_token()
        except CaptchaRequiredError:
            logger.warning("海航登录触发 CAPTCHA 验证（E000167），本次查询跳过")
            await self._notify_captcha_required()
            return {}

        results: dict[str, list[FlightOffer]] = {}
        async with self._search:
            for sub in active:
                logger.info("检查订阅 [{}] {}->{} {}", sub.id, sub.origin, sub.destination, sub.depart_date)
                try:
                    offers = await self._search.search(
                        origin=sub.origin,
                        destination=sub.destination,
                        depart_date=sub.depart_date_obj,
                        threshold=sub.price_threshold,
                    )
                    results[sub.id] = offers
                    if offers:
                        if self._dry_run:
                            logger.info(
                                "[dry-run] 订阅 [{}] 找到 {} 个符合条件的航班（跳过发送）",
                                sub.id,
                                len(offers),
                            )
                            for o in offers:
                                logger.info("[dry-run]   {}", o)
                        elif self._notifier is not None:
                            await self._notifier.send_flight_alerts(offers, sub.price_threshold)
                except Exception as exc:
                    logger.exception("订阅 [{}] 查询失败: {}", sub.id, exc)
                    results[sub.id] = []

        return results

    async def _notify_captcha_required(self) -> None:
        """通过企业微信通知用户需要完成 CAPTCHA 验证。"""
        msg = (
            "⚠️ 海航登录触发 CAPTCHA 验证（E000167）\n"
            "请按以下步骤导入 Token：\n"
            "1. 在手机/电脑浏览器打开 https://m.hnair.com 并登录\n"
            "2. F12 开发者工具 → Network → 找到 login 请求\n"
            "3. 复制该请求的完整 Response 内容\n"
            "4. 在服务器执行：\n"
            'feifeile token import \'{"success":true,...}\''
        )
        if self._notifier is not None:
            try:
                await self._notifier.send_text(msg)
                logger.info("已通过企业微信发送 CAPTCHA 验证通知")
            except Exception as exc:
                logger.warning("发送 CAPTCHA 验证通知失败: {}", exc)
        else:
            logger.warning(
                "未配置企业微信，无法发送 CAPTCHA 通知。\n{}", msg
            )
