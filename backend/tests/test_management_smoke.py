from __future__ import annotations

import json
import sys
import unittest
import base64
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import DeclarativeBase
from starlette.websockets import WebSocketDisconnect


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


fake_database = ModuleType("app.database")


class _TestBase(DeclarativeBase):
    pass


async def _unused_get_db():
    raise RuntimeError("tests should not use app.database.get_db")
    yield


def _unused_async_session():
    raise RuntimeError("tests should patch app.database.async_session")


fake_database.Base = _TestBase
fake_database.get_db = _unused_get_db
fake_database.async_session = _unused_async_session
sys.modules.setdefault("app.database", fake_database)


from app import security
from app.config import settings
from app.models.account import Account
from app.models.system_config import SystemConfig
from app.routers import ai_chat, ai_tasks, system, ws
from app.schemas import AITaskCreate, ChatMessageCreate
from app.services.account_risk_service import (
    DEGRADED_STATUS,
    RELOGIN_PENDING_STATUS,
    RiskControlConfig,
    get_preferred_login_account,
    is_account_in_cooldown,
    mark_account_verified,
    mark_account_failure,
)
from app.services.qq_crawler import QQCrawler
from app.services.task_scheduler import TaskSchedulerService
from app.services.xhs_crawler import XHSCrawler


def _encode_ws_token(token: str) -> str:
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")


class _FakeScalarCollection:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class _FakeQueryResult:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return _FakeScalarCollection(self._many)


class _FakeChatDb:
    def __init__(self):
        self.session = SimpleNamespace(id=1, title="新对话")
        self.messages = []
        self.commit_count = 0

    async def execute(self, query):
        entity = query.column_descriptions[0].get("entity")
        if entity is ai_chat.ChatSession:
            return _FakeQueryResult(one=self.session)
        if entity is ai_chat.ChatMessage:
            return _FakeQueryResult(many=self.messages)
        raise AssertionError(f"unexpected entity: {entity}")

    def add(self, model):
        if isinstance(model, ai_chat.ChatMessage):
            self.messages.append(model)

    async def commit(self):
        self.commit_count += 1


class _FakeSaveDb:
    def __init__(self, saved_messages):
        self.saved_messages = saved_messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add(self, model):
        self.saved_messages.append(model)

    async def commit(self):
        return None


class _FakeLookupDb:
    async def execute(self, query):
        return _FakeQueryResult(one=None)


class _FakeSchedulerDb:
    def __init__(self, accounts=None, configs=None):
        self.accounts = {account.id: account for account in (accounts or [])}
        self.configs = {config.key: config for config in (configs or [])}
        self.commit_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query):
        entity = query.column_descriptions[0].get("entity")
        if entity is Account:
            items = [account for account in self.accounts.values() if self._matches_all(account, query._where_criteria)]
            return _FakeQueryResult(one=items[0] if len(items) == 1 else None, many=items)
        if entity is SystemConfig:
            items = [config for config in self.configs.values() if self._matches_all(config, query._where_criteria)]
            return _FakeQueryResult(one=items[0] if len(items) == 1 else None, many=items)
        raise AssertionError(f"unexpected entity: {entity}")

    async def get(self, model, object_id):
        if model is Account:
            return self.accounts.get(object_id)
        raise AssertionError(f"unexpected get model: {model}")

    async def commit(self):
        self.commit_count += 1

    def _matches_all(self, item, criteria):
        return all(self._matches(item, criterion) for criterion in criteria)

    def _matches(self, item, criterion):
        left = getattr(criterion, "left", None)
        right = getattr(criterion, "right", None)
        operator_name = getattr(getattr(criterion, "operator", None), "__name__", "")
        if left is None:
            return True

        expected = getattr(right, "value", right)
        actual = getattr(item, left.key)
        if operator_name == "eq":
            return actual == expected
        if operator_name == "in_op":
            return actual in expected
        return True


class AdminBoundarySmokeTests(unittest.TestCase):
    def setUp(self):
        self._original_admin_token = settings.admin_api_token
        self._original_napcat_token = settings.napcat_token
        self._original_secret_key = settings.secret_key
        self._original_security_env_file = security.ENV_FILE
        self._temp_dir = TemporaryDirectory()
        security.ENV_FILE = Path(self._temp_dir.name) / ".env"
        settings.admin_api_token = "admin-secret"
        settings.napcat_token = "napcat-secret"
        settings.secret_key = "bootstrap-secret"

        app = FastAPI()
        app.include_router(system.router, prefix="/api/system")
        app.include_router(ws.router)
        self.client = TestClient(app)

    def tearDown(self):
        settings.admin_api_token = self._original_admin_token
        settings.napcat_token = self._original_napcat_token
        settings.secret_key = self._original_secret_key
        security.ENV_FILE = self._original_security_env_file
        self._temp_dir.cleanup()
        self.client.close()

    def test_system_route_requires_admin_token(self):
        unauthorized = self.client.get("/api/system/napcat-status")
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.get(
            "/api/system/napcat-status",
            headers={"Authorization": "Bearer admin-secret"},
        )
        self.assertEqual(authorized.status_code, 200)

    def test_system_route_falls_back_to_secret_key_when_admin_token_missing(self):
        settings.admin_api_token = ""
        response = self.client.get(
            "/api/system/napcat-status",
            headers={"Authorization": "Bearer bootstrap-secret"},
        )
        self.assertEqual(response.status_code, 200)

    def test_client_websocket_requires_admin_token(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/client"):
                pass

        with self.client.websocket_connect(
            "/ws/client",
            subprotocols=["airr-admin", f"airr-admin-token.{_encode_ws_token('admin-secret')}"]
        ) as websocket:
            first_message = json.loads(websocket.receive_text())
            self.assertIn(first_message["type"], {"napcat_connected", "napcat_disconnected"})
            websocket.send_text(json.dumps({"type": "ping"}))
            while True:
                payload = json.loads(websocket.receive_text())
                if payload["type"] == "pong":
                    break

    def test_napcat_websocket_requires_plugin_token(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/napcat"):
                pass

        with self.client.websocket_connect(
            "/ws/napcat",
            headers={"X-NapCat-Token": "napcat-secret"},
        ) as websocket:
            welcome = json.loads(websocket.receive_text())
            self.assertEqual(welcome["type"], "welcome")
            websocket.send_text(json.dumps({"type": "auth", "qq": "10001"}))

    def test_napcat_websocket_falls_back_to_secret_key_when_token_missing(self):
        settings.napcat_token = ""
        settings.admin_api_token = ""
        with self.client.websocket_connect(
            "/ws/napcat",
            headers={"X-NapCat-Token": "bootstrap-secret"},
        ) as websocket:
            welcome = json.loads(websocket.receive_text())
            self.assertEqual(welcome["type"], "welcome")


class RegressionSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_stream_persists_assistant_message(self):
        fake_db = _FakeChatDb()
        saved_messages = []

        async def fake_chat_stream(content, history, session_id):
            self.assertEqual(content, "你好")
            self.assertEqual(session_id, 1)
            self.assertEqual(history[-1].role, "user")
            yield {"sources": [{"id": 1, "snippet": "context"}], "context_stats": {"hits": 1}}
            yield "回"
            yield "答"

        with patch("app.services.ai_service.ai_service.chat_stream", new=fake_chat_stream):
            with patch.object(ai_chat, "async_session", new=lambda: _FakeSaveDb(saved_messages)):
                response = await ai_chat.send_message(1, ChatMessageCreate(content="你好"), fake_db)

                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)

        body = "".join(chunks)
        self.assertIn("data: 回", body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(fake_db.session.title, "你好")
        self.assertEqual(len(saved_messages), 1)
        self.assertEqual(saved_messages[0].role, "assistant")
        self.assertEqual(saved_messages[0].content, "回答")
        self.assertEqual(saved_messages[0].sources, [{"id": 1, "snippet": "context"}])

    async def test_task_update_switches_to_interval_without_residual_cron(self):
        request = AITaskCreate(
            name="巡检任务",
            description="每隔十五分钟检查一次",
            interval_minutes=15,
        )
        task = SimpleNamespace(task_type="recurring", ai_suggested_interval=120)

        with patch(
            "app.services.ai_service.ai_service.parse_task_type_and_interval",
            return_value={"task_type": "recurring", "suggested_interval_minutes": 45},
        ):
            resolved = await ai_tasks._resolve_updated_task_config(request, task, _FakeLookupDb())

        self.assertIsNone(resolved["cron_expr"])
        self.assertEqual(resolved["interval_minutes"], 15)
        self.assertEqual(resolved["task_type"], "recurring")
        self.assertEqual(resolved["target_qq"], "admin")

    def test_system_env_sync_round_trip_uses_canonical_env_keys(self):
        original_env_file = system.ENV_FILE

        with TemporaryDirectory() as temp_dir:
            temp_env_file = Path(temp_dir) / ".env"
            temp_env_file.write_text(
                'database_host=127.0.0.1\nnapcat_token="old token"\nEXTRA_FLAG=keep\n',
                encoding="utf-8",
            )
            system.ENV_FILE = temp_env_file
            try:
                system._sync_env_settings(
                    {
                        "database_host": "10.10.10.10",
                        "database_password": "space value",
                        "napcat_token": "new token",
                    }
                )

                rewritten = temp_env_file.read_text(encoding="utf-8")
                loaded = system._load_env_settings()
            finally:
                system.ENV_FILE = original_env_file

        self.assertIn("DATABASE_HOST=10.10.10.10", rewritten)
        self.assertIn('DATABASE_PASSWORD="space value"', rewritten)
        self.assertIn('NAPCAT_TOKEN="new token"', rewritten)
        self.assertIn("EXTRA_FLAG=keep", rewritten)
        self.assertEqual(loaded["database_host"], "10.10.10.10")
        self.assertEqual(loaded["database_password"], "space value")
        self.assertEqual(loaded["napcat_token"], "new token")


class AccountRiskControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_preferred_login_account_skips_cooldown_and_degraded_by_default(self):
        cooldown_account = Account(
            id=1,
            platform="qq",
            account_id="login-cooldown",
            is_target=0,
            status="active",
            cookies="[]",
            risk_cooldown_until=datetime.now() + timedelta(minutes=10),
        )
        degraded_account = Account(
            id=2,
            platform="qq",
            account_id="login-degraded",
            is_target=0,
            status="degraded",
            cookies="[]",
        )
        healthy_account = Account(
            id=3,
            platform="qq",
            account_id="login-healthy",
            is_target=0,
            status="active",
            cookies="[]",
        )
        db = _FakeSchedulerDb(accounts=[cooldown_account, degraded_account, healthy_account])

        selected = await get_preferred_login_account(
            db,
            "qq",
            require_cookies=True,
            allow_degraded=False,
        )

        self.assertEqual(selected.id, healthy_account.id)

    async def test_mark_account_failure_escalates_to_relogin_pending_after_threshold(self):
        account = Account(
            id=1,
            platform="qq",
            account_id="login-risk",
            is_target=0,
            status="active",
        )
        config = RiskControlConfig(cookie_failure_threshold=2, risk_cooldown_minutes=15)

        first_status = mark_account_failure(account, reason="qq_cookie_refresh_failed", config=config)
        second_status = mark_account_failure(account, reason="qq_cookie_refresh_failed", config=config)

        self.assertEqual(first_status, DEGRADED_STATUS)
        self.assertEqual(second_status, RELOGIN_PENDING_STATUS)
        self.assertEqual(account.failure_count, 2)
        self.assertTrue(is_account_in_cooldown(account))

    async def test_mark_account_verified_clears_failure_state(self):
        account = Account(
            id=4,
            platform="qq",
            account_id="login-verified",
            is_target=0,
            status="degraded",
            failure_count=3,
            last_failure_reason="qq_cookie_refresh_failed",
            risk_cooldown_until=datetime.now() + timedelta(minutes=30),
        )

        mark_account_verified(account, refreshed=True)

        self.assertEqual(account.status, "active")
        self.assertEqual(account.failure_count, 0)
        self.assertIsNone(account.last_failure_reason)
        self.assertIsNone(account.risk_cooldown_until)
        self.assertIsNotNone(account.cookie_last_validated_at)
        self.assertIsNotNone(account.last_cookie_refresh_at)

    async def test_save_login_cookies_updates_selected_account_row(self):
        selected_account = Account(
            id=5,
            platform="qq",
            account_id="selected-login",
            is_target=0,
            status="degraded",
            cookies='[{"name":"uin","value":"o5"}]',
            failure_count=2,
            last_failure_reason="qq_cookie_refresh_failed",
            risk_cooldown_until=datetime.now() + timedelta(minutes=20),
            last_login=datetime.now() - timedelta(days=1),
        )
        newer_account = Account(
            id=6,
            platform="qq",
            account_id="newer-login",
            is_target=0,
            status="active",
            cookies='[{"name":"uin","value":"o6"}]',
            last_login=datetime.now(),
        )
        db = _FakeSchedulerDb(accounts=[selected_account, newer_account])
        crawler = QQCrawler()
        cookies = [
            {"name": "uin", "value": "o123456"},
            {"name": "p_skey", "value": "token-value"},
        ]

        with patch("app.database.async_session", new=lambda: db):
            await crawler._save_login_cookies(cookies, login_account_id=selected_account.id)

        self.assertIn("token-value", selected_account.cookies)
        self.assertEqual(selected_account.account_id, "123456")
        self.assertEqual(selected_account.status, "active")
        self.assertEqual(selected_account.failure_count, 0)
        self.assertIsNone(selected_account.last_failure_reason)
        self.assertIsNone(selected_account.risk_cooldown_until)
        self.assertNotIn("token-value", newer_account.cookies)

    async def test_notify_cookie_expired_still_alerts_when_login_is_relogin_pending(self):
        crawler = QQCrawler()
        login_account = Account(
            id=7,
            platform="qq",
            account_id="login-notify",
            is_target=0,
            status="relogin_pending",
        )
        db = _FakeSchedulerDb(
            accounts=[login_account],
            configs=[
                SystemConfig(key="login_notify_enabled", value="true"),
                SystemConfig(key="admin_qq", value="10001"),
            ],
        )
        napcat_client = SimpleNamespace(send_message=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch("app.services.napcat_client.napcat_client", new=napcat_client):
                await crawler._notify_cookie_expired("QQ空间", "需要重新登录")

        napcat_client.send_message.assert_awaited_once()

    async def test_xhs_mark_login_account_expired_updates_only_selected_row(self):
        crawler = XHSCrawler()
        selected_account = Account(
            id=8,
            platform="xhs",
            account_id="login-xhs-selected",
            is_target=0,
            status="active",
            cookies="[]",
            failure_count=0,
        )
        other_account = Account(
            id=9,
            platform="xhs",
            account_id="login-xhs-other",
            is_target=0,
            status="active",
            cookies="[]",
            failure_count=0,
        )
        db = _FakeSchedulerDb(accounts=[selected_account, other_account])

        with patch("app.database.async_session", new=lambda: db):
            await crawler._mark_login_account_expired(
                selected_account.id,
                reason="xhs_cookie_detail_auth_failed",
            )

        self.assertEqual(selected_account.status, "expired")
        self.assertEqual(selected_account.failure_count, 1)
        self.assertEqual(selected_account.last_failure_reason, "xhs_cookie_detail_auth_failed")
        self.assertEqual(other_account.status, "active")
        self.assertEqual(other_account.failure_count, 0)

    async def test_xhs_notify_cookie_expired_still_alerts_when_login_is_relogin_pending(self):
        crawler = XHSCrawler()
        login_account = Account(
            id=70,
            platform="xhs",
            account_id="login-xhs-notify",
            is_target=0,
            status="relogin_pending",
        )
        db = _FakeSchedulerDb(
            accounts=[login_account],
            configs=[
                SystemConfig(key="login_notify_enabled", value="true"),
                SystemConfig(key="admin_qq", value="10001"),
            ],
        )
        napcat_client = SimpleNamespace(send_message=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch("app.services.napcat_client.napcat_client", new=napcat_client):
                await crawler._notify_cookie_expired("小红书", "需要重新登录")

        napcat_client.send_message.assert_awaited_once()


class TaskSchedulerRiskControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_auto_crawl_skips_qq_crawl_when_refresh_fails(self):
        qq_target = Account(id=10, platform="qq", account_id="target-qq", is_target=1)
        qq_login = Account(
            id=20,
            platform="qq",
            account_id="login-qq",
            is_target=0,
            status="active",
            cookies="[]",
        )
        config = SystemConfig(key="skip_crawl_when_login_degraded", value="true")
        db = _FakeSchedulerDb(accounts=[qq_target, qq_login], configs=[config])
        service = TaskSchedulerService()
        qq_crawler = SimpleNamespace(
            refresh_qq_cookies=AsyncMock(return_value=False),
            start_crawl=AsyncMock(),
        )
        xhs_crawler = SimpleNamespace(start_crawl=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch.object(service, "_mark_login_account_failure", new=AsyncMock()) as failure_mock:
                with patch.object(service, "_mark_login_account_verified", new=AsyncMock()) as verified_mock:
                    with patch("app.services.qq_crawler.qq_crawler", new=qq_crawler):
                        with patch("app.services.xhs_crawler.xhs_crawler", new=xhs_crawler):
                            await service.execute_auto_crawl()

        qq_crawler.refresh_qq_cookies.assert_awaited_once_with(
            login_account_id=qq_login.id,
            allow_degraded=False,
        )
        failure_mock.assert_awaited_once_with(qq_login.id, reason="qq_cookie_refresh_failed")
        verified_mock.assert_not_awaited()
        qq_crawler.start_crawl.assert_not_awaited()

    async def test_execute_auto_crawl_passes_selected_login_account_to_qq_crawler(self):
        qq_target = Account(id=12, platform="qq", account_id="target-qq-success", is_target=1)
        qq_login = Account(
            id=22,
            platform="qq",
            account_id="login-qq-success",
            is_target=0,
            status="active",
            cookies="[]",
        )
        config = SystemConfig(key="skip_crawl_when_login_degraded", value="true")
        db = _FakeSchedulerDb(accounts=[qq_target, qq_login], configs=[config])
        service = TaskSchedulerService()
        qq_crawler = SimpleNamespace(
            refresh_qq_cookies=AsyncMock(return_value=True),
            start_crawl=AsyncMock(),
        )
        xhs_crawler = SimpleNamespace(start_crawl=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch.object(service, "_mark_login_account_failure", new=AsyncMock()) as failure_mock:
                with patch.object(service, "_mark_login_account_verified", new=AsyncMock()) as verified_mock:
                    with patch("app.services.qq_crawler.qq_crawler", new=qq_crawler):
                        with patch("app.services.xhs_crawler.xhs_crawler", new=xhs_crawler):
                            await service.execute_auto_crawl()

        failure_mock.assert_not_awaited()
        verified_mock.assert_not_awaited()
        qq_crawler.refresh_qq_cookies.assert_awaited_once_with(
            login_account_id=qq_login.id,
            allow_degraded=False,
        )
        qq_crawler.start_crawl.assert_awaited_once_with(
            [qq_target.account_id],
            login_account_id=qq_login.id,
            allow_degraded_login=False,
        )

    async def test_execute_auto_crawl_skips_xhs_without_healthy_login(self):
        xhs_target = Account(id=11, platform="xhs", account_id="target-xhs", is_target=1)
        degraded_login = Account(
            id=21,
            platform="xhs",
            account_id="login-xhs",
            is_target=0,
            status="degraded",
            cookies="[]",
        )
        config = SystemConfig(key="skip_crawl_when_login_degraded", value="true")
        db = _FakeSchedulerDb(accounts=[xhs_target, degraded_login], configs=[config])
        service = TaskSchedulerService()
        qq_crawler = SimpleNamespace(
            refresh_qq_cookies=AsyncMock(return_value=True),
            start_crawl=AsyncMock(),
        )
        xhs_crawler = SimpleNamespace(start_crawl=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch.object(service, "_mark_login_account_failure", new=AsyncMock()):
                with patch.object(service, "_mark_login_account_verified", new=AsyncMock()):
                    with patch("app.services.qq_crawler.qq_crawler", new=qq_crawler):
                        with patch("app.services.xhs_crawler.xhs_crawler", new=xhs_crawler):
                            await service.execute_auto_crawl()

        xhs_crawler.start_crawl.assert_not_awaited()

    async def test_execute_auto_crawl_passes_selected_login_account_to_xhs_crawler(self):
        xhs_target = Account(id=13, platform="xhs", account_id="target-xhs-success", is_target=1)
        xhs_login = Account(
            id=23,
            platform="xhs",
            account_id="login-xhs-success",
            is_target=0,
            status="active",
            cookies="[]",
        )
        config = SystemConfig(key="skip_crawl_when_login_degraded", value="true")
        db = _FakeSchedulerDb(accounts=[xhs_target, xhs_login], configs=[config])
        service = TaskSchedulerService()
        qq_crawler = SimpleNamespace(
            refresh_qq_cookies=AsyncMock(return_value=True),
            start_crawl=AsyncMock(),
        )
        xhs_crawler = SimpleNamespace(start_crawl=AsyncMock())

        with patch("app.database.async_session", new=lambda: db):
            with patch.object(service, "_mark_login_account_failure", new=AsyncMock()):
                with patch.object(service, "_mark_login_account_verified", new=AsyncMock()):
                    with patch("app.services.qq_crawler.qq_crawler", new=qq_crawler):
                        with patch("app.services.xhs_crawler.xhs_crawler", new=xhs_crawler):
                            await service.execute_auto_crawl()

        xhs_crawler.start_crawl.assert_awaited_once_with(
            [xhs_target.account_id],
            login_account_id=xhs_login.id,
        )


if __name__ == "__main__":
    unittest.main()