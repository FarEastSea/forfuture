"""事件总线：采集进度、登录态变化等实时事件的发布订阅。

优先走 Redis pub/sub，这样 API 进程和采集 worker 进程之间能互通；Redis 不可用时自动
退化成进程内广播，单进程部署照样能用。旧实现把采集状态放在模块级字典里，重启即丢，
且多 worker 下互相看不见。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.events")

CHANNEL = "airr:events"


@dataclass(slots=True)
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Event":
        data = json.loads(raw)
        return cls(type=data["type"], payload=data.get("payload", {}), at=data.get("at", ""))


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._redis: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._redis_failed = False

    async def _ensure_redis(self) -> Any | None:
        if not settings.redis_enabled or self._redis_failed:
            return None
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as redis_asyncio

            client = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
        except Exception as exc:  # Redis 缺失不应该让整个服务起不来
            logger.warning("Redis 不可用，事件总线退化为进程内广播: %s", exc)
            self._redis_failed = True
            return None
        self._redis = client
        return client

    async def start(self) -> None:
        client = await self._ensure_redis()
        if client is None or self._reader_task is not None:
            return
        self._reader_task = asyncio.create_task(self._read_loop(client), name="event-bus-reader")

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def _read_loop(self, client: Any) -> None:
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event = Event.from_json(message["data"])
                except (ValueError, KeyError):
                    continue
                self._fanout(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("事件总线订阅中断: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.aclose()

    def _fanout(self, event: Event) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者不该拖垮发布方，丢掉最旧的一条再放新的
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    async def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = Event(type=event_type, payload=payload or {})
        client = await self._ensure_redis()
        if client is not None:
            try:
                await client.publish(CHANNEL, event.to_json())
                return  # Redis 会把消息回传给本进程的订阅循环，不用再本地扇出
            except Exception as exc:
                logger.warning("事件发布到 Redis 失败，改为本地广播: %s", exc)
                self._redis_failed = True
                self._redis = None
        self._fanout(event)

    @contextlib.asynccontextmanager
    async def subscribe(self, maxsize: int = 256) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


event_bus = EventBus()


class EventType:
    CRAWL_JOB_QUEUED = "crawl.job.queued"
    CRAWL_JOB_STARTED = "crawl.job.started"
    CRAWL_JOB_PROGRESS = "crawl.job.progress"
    CRAWL_JOB_FINISHED = "crawl.job.finished"
    CREDENTIAL_STATUS_CHANGED = "credential.status.changed"
    LOGIN_QR_UPDATED = "login.qr.updated"
    LOGIN_SUCCEEDED = "login.succeeded"
    INDEX_PROGRESS = "index.progress"
    NAPCAT_STATUS = "napcat.status"
    TASK_RUN_STARTED = "task.run.started"
    TASK_RUN_FINISHED = "task.run.finished"
