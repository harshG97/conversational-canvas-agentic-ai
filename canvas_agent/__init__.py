"""Canvas LMS conversational AI agent package."""

from __future__ import annotations

import logging
import os
import sys


def setup_logging() -> None:
    """Configure logging for the canvas_agent package.

    Reads LOG_LEVEL from the environment (default: DEBUG).
    Logs to both stderr and logs/agent.log.
    """
    level_name = os.environ.get("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    fmt = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
    datefmt = "%H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Console handler (stderr so it doesn't mix with CLI output)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)

    # File handler
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "agent.log"), encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # Configure the package-level logger
    logger = logging.getLogger("canvas_agent")
    logger.setLevel(level)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # Prevent logs from propagating to the root logger
    logger.propagate = False
