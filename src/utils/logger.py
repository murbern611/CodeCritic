"""
CodeCritic — 日志配置
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"


def setup_logger(
    name: str = "codecritic",
    level: str = "INFO",
    verbose: bool = False,
    log_file: str | None = None,
) -> logging.Logger:
    """配置并返回 logger"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(console)

    # 文件输出（默认 data/logs/codecritic.log）
    log_path = _LOG_DIR / (log_file or "codecritic.log")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    return logger


# 全局 logger 实例
logger = setup_logger()
