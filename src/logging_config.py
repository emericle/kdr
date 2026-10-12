"""The one logging configuration, installed by the entrypoints.

Every module in ``src/`` takes its logger with :func:`logging.getLogger` and
never configures handlers: an import that reconfigures global logging silently
replaces whatever the caller had installed, and a module that configures it at
import writes to a file before the process knows whether it is the pipeline, a
test run or a notebook. Only :mod:`src.logging_config` builds handlers, and the
entrypoints (:mod:`src.scraper`, :mod:`src.backtest`) call
:func:`configure_logging` once, from their ``main``.

The file handler rotates rather than opening a fresh log per run: the pipeline
runs for days at a time, and a per-run file has been measured at 225 MB.
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import TextIO

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LOG_FILENAME = os.path.join(LOG_DIR, "kdr.log")
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

_debug_enabled = False


def set_debug(verbose: bool) -> None:
    """Remember whether the entrypoint asked for DEBUG level."""
    global _debug_enabled
    _debug_enabled = verbose


def is_debug() -> bool:
    """Whether the entrypoint asked for DEBUG level.

    A function rather than a module constant: the pipeline's threads read it
    while running, and a name bound at import would not see the entrypoint's
    later call.
    """
    return _debug_enabled


def configure_logging(
    verbose: bool = False,
    log_filename: str = LOG_FILENAME,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Install console and rotating-file handlers on the root logger.

    ``force=True`` replaces whatever handlers a caller had already installed
    rather than adding to them, so an entrypoint that configures logging twice
    (directly, and through a library that did it first) does not double every
    log line.

    Args:
        verbose: Log at DEBUG instead of INFO.
        log_filename: Where to write; defaults to ``logs/kdr.log``.
        stream: Console stream; defaults to the process's stdout.
    Returns:
        The root logger, so the caller can set per-module levels on it.
    """
    os.makedirs(os.path.dirname(log_filename), exist_ok=True)
    set_debug(verbose)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout if stream is None else stream),
            RotatingFileHandler(
                log_filename,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            ),
        ],
        force=True,
    )
    return logging.getLogger()
