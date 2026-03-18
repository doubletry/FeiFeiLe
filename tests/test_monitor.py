"""tests/test_monitor.py — 监控与订阅模块单元测试"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from feifeile.auth import AuthToken
from feifeile.config import HNAConfig, MonitorConfig, WeComConfig
from feifeile.flight import FlightOffer
from feifeile.monitor import Monitor, Subscription, SubscriptionStore


# ---------------------------------------------------------------------------
# SubscriptionStore 测试
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return SubscriptionStore(str(tmp_path / "subs.json"))


def make_sub(
    sub_id: str = "abc12345",
    origin: str = "HAK",
    destination: str = "PEK",
    days_ahead: int = 10,
    threshold: float = 199.0,
    active: bool = True,
) -> Subscription:
    depart = (date.today() + timedelta(days=days_ahead)).isoformat()
    return Subscription(
        id=sub_id,
        origin=origin,
        destination=destination,
        depart_date=depart,
        price_threshold=threshold,
        active=active,
    )


class TestSubscriptionStore:
    def test_add_and_list(self, store):
        sub = make_sub()
        store.add(sub)
        assert len(store.list_active()) == 1

    def test_remove_existing(self, store):
        sub = make_sub()
        store.add(sub)
        result = store.remove(sub.id)
        assert result is True
        assert store.list_active() == []

    def test_remove_nonexistent(self, store):
        result = store.remove("nonexistent")
        assert result is False

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "subs.json")
        store1 = SubscriptionStore(path)
        sub = make_sub()
        store1.add(sub)

        store2 = SubscriptionStore(path)
        subs = store2.list_all()
        assert len(subs) == 1
        assert subs[0].id == sub.id

    def test_expired_subscription_not_active(self, store):
        sub = make_sub(days_ahead=-1)  # 昨天，已过期
        store.add(sub)
        assert store.list_active() == []

    def test_deactivate_expired(self, store):
        sub = make_sub(days_ahead=-1)
        store.add(sub)
        count = store.deactivate_expired()
        assert count == 1
        assert all(not s.active for s in store.list_all() if s.id == sub.id)

    def test_inactive_not_listed(self, store):
        sub = make_sub(active=False)
        store.add(sub)
        assert store.list_active() == []

    def test_corrupted_file_resets(self, tmp_path):
        path = str(tmp_path / "subs.json")
        with open(path, "w") as f:
            f.write("{invalid json")
        store = SubscriptionStore(path)
        assert store.list_all() == []

    def test_multiple_subscriptions(self, store):
        for i in range(5):
            store.add(make_sub(sub_id=f"id{i}"))
        assert len(store.list_active()) == 5


class TestSubscription:
    def test_is_expired_past(self):
        sub = make_sub(days_ahead=-1)
        assert sub.is_expired()

    def test_is_not_expired_future(self):
        sub = make_sub(days_ahead=5)
        assert not sub.is_expired()

    def test_to_dict_roundtrip(self):
        sub = make_sub()
        d = sub.to_dict()
        sub2 = Subscription.from_dict(d)
        assert sub2.id == sub.id
        assert sub2.origin == sub.origin
        assert sub2.price_threshold == sub.price_threshold


# ---------------------------------------------------------------------------
# Monitor 测试
# ---------------------------------------------------------------------------


@pytest.fixture
def configs(monkeypatch):
    monkeypatch.setenv("HNA_USERNAME", "13800000000")
    monkeypatch.setenv("HNA_PASSWORD", "test_password")
    monkeypatch.setenv("WECOM_CORP_ID", "ww_test")
    monkeypatch.setenv("WECOM_SECRET", "test_secret")
    monkeypatch.setenv("WECOM_AGENT_ID", "1000002")
    return HNAConfig(), WeComConfig(), MonitorConfig()


@pytest.fixture
def monitor_with_store(configs, tmp_path):
    hna, wecom, mon = configs
    store = SubscriptionStore(str(tmp_path / "subs.json"))
    monitor = Monitor(hna, wecom, mon, store)
    # Mock auth to prevent real login during tests
    monitor._auth.get_token = AsyncMock(
        return_value=AuthToken(
            access_token="test_token",
            refresh_token="test_ref",
            expires_at=time.time() + 7200,
        )
    )
    return monitor, store


class TestMonitor:
    @pytest.mark.asyncio
    async def test_run_once_no_subscriptions(self, monitor_with_store):
        monitor, _ = monitor_with_store
        results = await monitor.run_once()
        assert results == {}

    @pytest.mark.asyncio
    async def test_run_once_with_matching_flight(self, monitor_with_store):
        monitor, store = monitor_with_store
        store.add(make_sub())

        matching_offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date=(date.today() + timedelta(days=10)).isoformat(),
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
            is_member_price=True,
        )

        with (
            patch.object(monitor._search, "search", new=AsyncMock(return_value=[matching_offer])),
            patch.object(monitor._notifier, "send_flight_alerts", new=AsyncMock()) as mock_notify,
        ):
            results = await monitor.run_once()

        assert len(results) == 1
        offers = list(results.values())[0]
        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        mock_notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_once_no_matching_flight(self, monitor_with_store):
        monitor, store = monitor_with_store
        store.add(make_sub())

        with (
            patch.object(monitor._search, "search", new=AsyncMock(return_value=[])),
            patch.object(monitor._notifier, "send_flight_alerts", new=AsyncMock()) as mock_notify,
        ):
            results = await monitor.run_once()

        assert list(results.values())[0] == []
        # 无符合条件的航班时，不应触发通知
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_once_search_error_handled(self, monitor_with_store):
        """单个订阅查询失败时，结果应记录为空列表，不影响其他订阅。"""
        monitor, store = monitor_with_store
        store.add(make_sub(sub_id="err_sub"))

        with patch.object(
            monitor._search,
            "search",
            new=AsyncMock(side_effect=Exception("网络超时")),
        ):
            results = await monitor.run_once()

        assert results["err_sub"] == []

    @pytest.mark.asyncio
    async def test_run_once_deactivates_expired(self, monitor_with_store):
        monitor, store = monitor_with_store
        # 添加一个过期订阅
        store.add(make_sub(sub_id="exp_sub", days_ahead=-1))

        with patch.object(monitor._search, "search", new=AsyncMock(return_value=[])):
            results = await monitor.run_once()

        # 过期订阅不应被处理
        assert results == {}

    @pytest.mark.asyncio
    async def test_run_once_dry_run_skips_notification(self, configs, tmp_path):
        """dry_run 模式下应跳过发送通知。"""
        hna, wecom, mon = configs
        store = SubscriptionStore(str(tmp_path / "dry_subs.json"))
        monitor = Monitor(hna, wecom, mon, store, dry_run=True)
        monitor._auth.get_token = AsyncMock(
            return_value=AuthToken(
                access_token="test_token",
                refresh_token="test_ref",
                expires_at=time.time() + 7200,
            )
        )
        store.add(make_sub())

        matching_offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date=(date.today() + timedelta(days=10)).isoformat(),
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
            is_member_price=True,
        )

        # dry_run 时 _notifier 为 None，不应抛出
        with patch.object(monitor._search, "search", new=AsyncMock(return_value=[matching_offer])):
            results = await monitor.run_once()

        assert len(results) == 1
        offers = list(results.values())[0]
        assert len(offers) == 1
        assert offers[0].flight_no == "HU7822"
        # notifier 应为 None
        assert monitor._notifier is None

    @pytest.mark.asyncio
    async def test_run_once_dry_run_no_wecom_config(self, tmp_path, monkeypatch):
        """dry_run 模式下不提供 WeComConfig 也应正常工作。"""
        monkeypatch.setenv("HNA_USERNAME", "13800000000")
        monkeypatch.setenv("HNA_PASSWORD", "test_password")
        hna = HNAConfig()
        mon = MonitorConfig()
        store = SubscriptionStore(str(tmp_path / "dry_subs2.json"))
        # wecom_config=None
        monitor = Monitor(hna, None, mon, store, dry_run=True)
        monitor._auth.get_token = AsyncMock(
            return_value=AuthToken(
                access_token="test_token",
                refresh_token="test_ref",
                expires_at=time.time() + 7200,
            )
        )
        store.add(make_sub())

        matching_offer = FlightOffer(
            flight_no="HU7822",
            origin="HAK",
            destination="PEK",
            depart_date=(date.today() + timedelta(days=10)).isoformat(),
            depart_time="08:00",
            arrive_time="12:00",
            cabin_class="Y",
            price=199.0,
            is_member_price=True,
        )

        with patch.object(monitor._search, "search", new=AsyncMock(return_value=[matching_offer])):
            results = await monitor.run_once()

        assert len(results) == 1
        assert monitor._notifier is None

    @pytest.mark.asyncio
    async def test_run_once_login_called_once_for_multiple_subs(self, monitor_with_store):
        """多个订阅只应登录一次（get_token 在循环外调用一次）。"""
        monitor, store = monitor_with_store
        store.add(make_sub(sub_id="sub1", origin="HAK", destination="PEK"))
        store.add(make_sub(sub_id="sub2", origin="SZX", destination="TNA"))
        store.add(make_sub(sub_id="sub3", origin="PEK", destination="HAK"))

        with patch.object(monitor._search, "search", new=AsyncMock(return_value=[])):
            results = await monitor.run_once()

        assert len(results) == 3
        # get_token 仅在循环前调用一次
        assert monitor._auth.get_token.await_count == 1
