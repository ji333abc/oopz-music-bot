"""Synchronous facade around the asynchronous OOPZ SDK."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class OopzRuntime:
    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._voice_started = False

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._startup_error is None and self.bot is not None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._thread_main,
            name="oopz-sdk-runtime",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(self.timeout):
            raise TimeoutError("OOPZ SDK 初始化超时")
        if self._startup_error is not None:
            raise RuntimeError(f"OOPZ SDK 初始化失败: {self._startup_error}") from self._startup_error

    def _thread_main(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def bootstrap() -> None:
            from oopz_sdk import OopzBot, OopzConfig

            executable = str(os.getenv("OOPZ_VOICE_BROWSER_EXECUTABLE_PATH") or "").strip()
            overrides = {"voice_browser_executable_path": executable} if executable else {}
            config = await OopzConfig.from_env_async(**overrides)
            self.bot = OopzBot(config)
            await self.bot.rest.start()

        try:
            self.loop.run_until_complete(bootstrap())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self.loop.close()
            return

        self._ready.set()
        self.loop.run_forever()

        async def shutdown() -> None:
            if self.bot is None:
                return
            if self._voice_started:
                try:
                    await self.bot.voice.close()
                except Exception:
                    logger.debug("关闭 OOPZ 语音服务失败", exc_info=True)
            await self.bot.rest.close()

        try:
            self.loop.run_until_complete(shutdown())
        finally:
            self.loop.close()

    def call(self, operation: Callable[[Any], Awaitable[Any]], timeout: float | None = None) -> Any:
        if not self.ready or self.loop is None:
            raise RuntimeError("OOPZ SDK 尚未就绪")
        future = asyncio.run_coroutine_threadsafe(operation(self.bot), self.loop)
        return future.result(timeout=timeout or self.timeout)

    def join_voice(self, area: str, channel: str) -> None:
        async def operation(bot) -> None:
            if not self._voice_started:
                await bot.voice.start()
                self._voice_started = True
            await bot.voice.join(area=area, channel=channel)

        self.call(operation, timeout=max(self.timeout, 180.0))

    def leave_voice(self) -> None:
        if self._voice_started:
            self.call(lambda bot: bot.voice.leave())

    def close(self) -> None:
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread:
            self._thread.join(timeout=15)

    def send_message(self, text: str, area: str, channel: str) -> Any:
        return self.call(lambda bot: bot.send(text=text, area=area, channel=channel))

    def get_area_channels(self, area: str) -> list[dict]:
        async def operation(bot) -> list[dict]:
            groups = await bot.areas.get_area_channels(area)
            return [group.model_dump(by_alias=True) for group in groups]

        return self.call(operation)

    def get_joined_areas(self) -> list[dict]:
        async def operation(bot) -> list[dict]:
            areas = await bot.areas.get_joined_areas()
            return [area.model_dump(by_alias=True) for area in areas]

        return self.call(operation)

    def get_voice_channel_members(self, area: str) -> dict[str, list[dict]]:
        async def operation(bot) -> dict[str, list[dict]]:
            result = await bot.channels.get_voice_channel_members(area=area)
            return {
                channel: [member.model_dump(by_alias=True) for member in members]
                for channel, members in result.channel_members.items()
            }

        return self.call(operation)

    def user_name(self, uid: str) -> str:
        async def operation(bot) -> str:
            user = await bot.person.get_person_info(uid)
            return str(getattr(user, "name", "") or "")

        try:
            return self.call(operation)
        except Exception:
            return ""


class SenderFacade:
    def __init__(self, runtime: OopzRuntime):
        self.runtime = runtime

    def send_message(self, *, text: str, channel: str, area: str, **_ignored) -> Any:
        return self.runtime.send_message(text, area, channel)

    def get_area_channels(self, *, area: str, **_ignored) -> list[dict]:
        return self.runtime.get_area_channels(area)

    def get_voice_channel_members(self, *, area: str, **_ignored) -> dict[str, list[dict]]:
        return self.runtime.get_voice_channel_members(area)


class NameFacade:
    def __init__(self, runtime: OopzRuntime):
        self.runtime = runtime

    def user(self, uid: str) -> str:
        return self.runtime.user_name(uid)


class VoiceFacade:
    def __init__(self, runtime: OopzRuntime):
        self.runtime = runtime

    @property
    def available(self) -> bool:
        return self.runtime.ready

    @property
    def is_playing(self) -> bool:
        try:
            return self.get_state() == "playing"
        except Exception:
            return False

    def play_audio(self, url: str, **_ignored) -> Any:
        return self.runtime.call(lambda bot: bot.voice.play_url(url))

    def stop_audio(self) -> None:
        self.runtime.call(lambda bot: bot.voice.stop())

    def pause_audio(self) -> bool:
        return bool(self.runtime.call(lambda bot: bot.voice.pause()))

    def resume_audio(self) -> bool:
        return bool(self.runtime.call(lambda bot: bot.voice.resume()))

    def get_state(self) -> str:
        return str(self.runtime.call(lambda bot: bot.voice.get_state()) or "")

    def get_current_time(self) -> float:
        return float(self.runtime.call(lambda bot: bot.voice.get_current_time()) or 0)
