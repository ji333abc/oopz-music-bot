from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oopzbot.logging_config import configure_logging, is_console_handler

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = ROOT / "legacy_oopzbot" / "src"
sys.path.insert(0, str(LEGACY_SOURCE))
try:
    from core import logger_config as legacy_logging
finally:
    sys.path.remove(str(LEGACY_SOURCE))


class LoggingConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root_logger = logging.getLogger()
        self.original_handlers = list(self.root_logger.handlers)
        self.original_level = self.root_logger.level
        self.original_legacy_initialized = legacy_logging._initialized
        self.root_logger.handlers.clear()
        legacy_logging._initialized = False

    def tearDown(self) -> None:
        for handler in self.root_logger.handlers:
            if handler not in self.original_handlers:
                handler.close()
        self.root_logger.handlers[:] = self.original_handlers
        self.root_logger.setLevel(self.original_level)
        legacy_logging._initialized = self.original_legacy_initialized

    def test_repeated_configuration_reuses_one_console_handler(self) -> None:
        first = configure_logging("INFO")
        second = configure_logging("WARNING")

        console_handlers = [
            handler
            for handler in self.root_logger.handlers
            if is_console_handler(handler)
        ]
        self.assertIs(first, second)
        self.assertEqual(console_handlers, [first])
        self.assertEqual(first.level, logging.WARNING)
        self.assertEqual(self.root_logger.level, logging.WARNING)

    def test_embedded_legacy_logger_adds_file_but_not_second_console(self) -> None:
        console = configure_logging("INFO")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        log_file = Path(temporary.name) / "oopz.log"
        with (
            patch.object(legacy_logging, "LOG_DIR", temporary.name),
            patch.object(legacy_logging, "LOG_FILE", str(log_file)),
        ):
            legacy_logging.setup_logger("embedded-legacy-test")

        console_handlers = [
            handler
            for handler in self.root_logger.handlers
            if is_console_handler(handler)
        ]
        file_handlers = [
            handler
            for handler in self.root_logger.handlers
            if isinstance(handler, logging.FileHandler)
        ]
        self.assertEqual(console_handlers, [console])
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(console.level, logging.INFO)
