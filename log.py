"""Structured logger for BlenderAIAgent.

Provides a preconfigured logger with timestamp, level, and message format.
All project modules import 'logger' from this file to ensure
consistent output on console and (optionally) to file.

Usage is intentionally simple: `from log import logger` everywhere,
instead of configuring separate loggers for each module.
"""

import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "BlenderAIAgent",
    level: int = logging.INFO,
    log_file: str = None,
) -> logging.Logger:
    """Create and return a logger configured with console output.

    Useful for having a single logging configuration point throughout
    the project. If the logger already exists (handlers present), it returns it
    without reconfiguring it, avoiding duplicates.

    Args:
        name: Name of the logger (used by logging.getLogger).
        level: Minimum log level (default: INFO).
        log_file: If specified, also writes to file with DEBUG level.

    Returns:
        Logger configured with format "[timestamp] [LEVEL] message".
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)
    logger.addHandler(console)

    if log_file:
        fh = logging.FileHandler(Path(log_file), encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)

    return logger


logger = setup_logger()
