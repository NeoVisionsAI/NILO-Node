"""MQTT broker integration for remote commands and events."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from nilo_node.config.models import AppConfig, MqttConfig
from nilo_node.util.node_id import node_short_id

logger = logging.getLogger(__name__)

CommandHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class MqttStatus(BaseModel):
    enabled: bool
    connected: bool = False
    mock: bool = False
    subscribe_topic: str | None = None
    events_topic: str | None = None
    last_error: str | None = None
    messages_received: int = 0


@dataclass
class MqttService:
    config: AppConfig
    node_id: str
    _handlers: dict[str, CommandHandler] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None
    _connected: bool = False
    _mock: bool = False
    _last_error: str | None = None
    _messages_received: int = 0
    _client: Any = None

    @property
    def mqtt(self) -> MqttConfig:
        return self.config.mqtt

    def subscribe_topic(self) -> str:
        short = node_short_id(self.node_id)
        return self.mqtt.topic_template.format(
            node_id=self.node_id,
            node_short_id=short,
        )

    def events_topic(self) -> str:
        short = node_short_id(self.node_id)
        return self.mqtt.events_topic_template.format(
            node_id=self.node_id,
            node_short_id=short,
        )

    def register_handler(self, action: str, handler: CommandHandler) -> None:
        self._handlers[action] = handler

    def get_status(self) -> MqttStatus:
        return MqttStatus(
            enabled=self.mqtt.enabled,
            connected=self._connected,
            mock=self._mock,
            subscribe_topic=self.subscribe_topic() if self.mqtt.enabled else None,
            events_topic=self.events_topic() if self.mqtt.enabled else None,
            last_error=self._last_error,
            messages_received=self._messages_received,
        )

    async def start(self) -> None:
        if not self.mqtt.enabled or self._task is not None:
            return

        if not self.mqtt.username or not self.mqtt.password:
            if self.mqtt.mock_when_unavailable:
                self._mock = True
                self._connected = True
                logger.warning("MQTT mock mode — set mqtt.username and mqtt.password")
                return
            logger.error("MQTT enabled but credentials missing")
            return

        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._connected = False
        self._client = None

    async def publish_event(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if not self.mqtt.enabled:
            return
        body = {"event": event, "node_id": self.node_id, "payload": payload or {}}
        if self._mock:
            logger.debug("MQTT mock publish %s: %s", event, body)
            return
        if self._client is None:
            return
        try:
            import aiomqtt

            await self._client.publish(
                self.events_topic(),
                json.dumps(body).encode("utf-8"),
                qos=self.mqtt.qos,
            )
        except Exception as exc:
            logger.warning("MQTT publish failed: %s", exc)

    def _validate_message_token(self, data: dict[str, Any]) -> bool:
        if not self.mqtt.require_message_token:
            return True
        expected = self.config.local_api.auth_token
        if not expected:
            return True
        token = data.get("token") or data.get("auth_token")
        return token == expected

    async def _dispatch(self, raw: bytes) -> None:
        self._messages_received += 1
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            await self.publish_event("error", {"detail": "invalid_json"})
            return

        if not self._validate_message_token(data):
            await self.publish_event("error", {"detail": "unauthorized", "request_id": data.get("request_id")})
            return

        action = str(data.get("action") or "").strip()
        request_id = data.get("request_id")
        handler = self._handlers.get(action)
        if handler is None:
            await self.publish_event(
                "error",
                {"detail": f"unknown_action:{action}", "request_id": request_id},
            )
            return

        try:
            result = await handler(data.get("payload") or {})
            await self.publish_event(
                "response",
                {"action": action, "request_id": request_id, "result": result},
            )
        except Exception as exc:
            logger.exception("MQTT handler %s failed", action)
            await self.publish_event(
                "error",
                {"action": action, "request_id": request_id, "detail": str(exc)},
            )

    async def _run_loop(self) -> None:
        try:
            import aiomqtt
        except ImportError:
            if self.mqtt.mock_when_unavailable:
                self._mock = True
                self._connected = True
                logger.warning("aiomqtt not installed — MQTT mock mode")
                return
            self._last_error = "aiomqtt not installed"
            logger.error(self._last_error)
            return

        tls_context = ssl.create_default_context() if self.mqtt.use_tls else None
        topic = self.subscribe_topic()

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self.mqtt.broker_host,
                    port=self.mqtt.broker_port,
                    username=self.mqtt.username,
                    password=self.mqtt.password,
                    tls_context=tls_context,
                ) as client:
                    self._client = client
                    self._connected = True
                    self._last_error = None
                    await client.subscribe(topic, qos=self.mqtt.qos)
                    logger.info("MQTT connected — subscribed to %s", topic)
                    await self.publish_event("online", {"topic": topic})

                    async for message in client.messages:
                        await self._dispatch(message.payload)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._client = None
                self._last_error = str(exc)
                logger.warning("MQTT disconnected (%s) — retry in %ss", exc, self.mqtt.reconnect_interval_sec)
                await asyncio.sleep(self.mqtt.reconnect_interval_sec)
