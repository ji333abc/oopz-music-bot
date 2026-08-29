"""Typed command application boundary shared by QQ and Panel."""

from __future__ import annotations

import logging
from collections.abc import Callable

from oopzbot.domain.compat import command_result_from_legacy
from oopzbot.domain.contracts import CommandRequest, CommandResult
from oopzbot.observability import command_context, ensure_command_id

CommandExecutor = Callable[[CommandRequest], dict]


class CommandService:
    """Execute one command through the current behavior-compatible backend.

    The backend remains injectable while command families are migrated.  This
    keeps the transport boundary stable and prevents QQ or Panel from calling
    concrete music, queue, Redis, or OOPZ implementations.
    """

    def __init__(
        self,
        executor: CommandExecutor,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._executor = executor
        self._logger = logger or logging.getLogger(__name__)

    def execute(self, request: CommandRequest) -> CommandResult:
        command_id = ensure_command_id(request.command_id)
        with command_context(command_id):
            self._logger.info("开始处理命令 command=%r", request.command)
            try:
                raw = self._executor(request)
            except Exception:
                self._logger.exception("命令处理失败 command=%r", request.command)
                raise
            self._logger.info("命令处理完成 ok=%s", bool(raw.get("ok")))
        return command_result_from_legacy(raw, command_id=command_id)
