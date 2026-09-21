"""MONTA — Logging Configuration."""

import logging
import sys


def setup_logging(level: str = "INFO"):
    """Configure logging for MONTA services."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_logger(name: str) -> logging.Logger:
    """Get a named logger."""
    return logging.getLogger(f"monta.{name}")
