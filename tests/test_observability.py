from __future__ import annotations

import io
import logging
import os
import unittest
from unittest.mock import patch

from oopzbot.logging_config import LOG_FORMAT
from oopzbot.observability import (
    command_context,
    ensure_command_id,
    install_log_record_factory,
    is_valid_command_id,
    new_command_id,
    redact_secrets,
)


class ObservabilityTests(unittest.TestCase):
    def test_command_ids_are_unique_and_validate_input(self) -> None:
        first = new_command_id()
        second = ensure_command_id("panel-request_123")

        self.assertNotEqual(first, second)
        self.assertTrue(is_valid_command_id(first))
        self.assertTrue(is_valid_command_id(second))
        self.assertTrue(is_valid_command_id(ensure_command_id("not valid!")))
        self.assertFalse(is_valid_command_id("short"))

    def test_redaction_removes_credentials_private_key_and_controls(self) -> None:
        private_key = "-----BEGIN RSA PRIVATE KEY-----\nsecret-key\n-----END RSA PRIVATE KEY-----"
        with patch.dict(
            os.environ,
            {
                "QQBOT_APP_SECRET": "qq-app-secret-value",
                "QQBOT_BRIDGE_TOKEN": "bridge-token-value",
                "QQ_MUSIC_COOKIE": "uin=secret-cookie",
            },
            clear=False,
        ):
            message = redact_secrets(
                "QQBOT_APP_SECRET=qq-app-secret-value\n"
                "Authorization: Bearer bridge-token-value\n"
                "Cookie: uin=secret-cookie\n"
                + private_key,
                extra_secrets=("jm-password-value",),
            )

        self.assertNotIn("qq-app-secret-value", message)
        self.assertNotIn("bridge-token-value", message)
        self.assertNotIn("secret-cookie", message)
        self.assertNotIn("secret-key", message)
        self.assertNotRegex(message, r"[\r\n\t]")
        self.assertLessEqual(len(message), 500)

    def test_log_line_contains_context_id_and_not_secret(self) -> None:
        install_log_record_factory()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger = logging.getLogger("observability-test")
        logger.handlers[:] = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            with patch.dict(os.environ, {"QQBOT_APP_SECRET": "app-secret-value"}, clear=False):
                with command_context("command-1234"):
                    logger.info("收到命令 secret=%s", "app-secret-value")
            output = stream.getvalue()
        finally:
            logger.handlers.clear()
            handler.close()

        self.assertIn("command_id=command-1234", output)
        self.assertNotIn("app-secret-value", output)
        self.assertIn("***", output)


if __name__ == "__main__":
    unittest.main()
