"""Root logging configuration shared by the CLI and standalone components."""

from __future__ import annotations

import logging

from .observability import RedactionFilter, install_log_record_factory


LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s command_id=%(command_id)s: %(message)s"


def is_console_handler(handler: logging.Handler) -> bool:
    """Return true for stderr/stdout handlers, excluding file handlers."""
    return isinstance(handler, logging.StreamHandler) and not isinstance(
        handler,
        logging.FileHandler,
    )


def configure_logging(level: str | int = logging.INFO) -> logging.Handler:
    """Configure exactly one root console handler and return it.

    The embedded legacy core may lower the root logger level so its rotating
    file can retain debug records. Setting the console handler level explicitly
    keeps that from making Docker's console unexpectedly verbose.
    """
    if isinstance(level, int):
        numeric_level = level
    else:
        numeric_level = logging.getLevelNamesMapping().get(
            str(level).upper(),
            logging.INFO,
        )

    install_log_record_factory()
    root = logging.getLogger()
    console = next(
        (handler for handler in root.handlers if is_console_handler(handler)),
        None,
    )
    if console is None:
        console = logging.StreamHandler()
        root.addHandler(console)

    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    if not any(isinstance(item, RedactionFilter) for item in console.filters):
        console.addFilter(RedactionFilter())
    root.setLevel(numeric_level)
    return console
