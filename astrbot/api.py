"""Minimal AstrBot compatibility layer for OpenClaw runtime."""

import logging
import os
from pathlib import Path


def _init_logger() -> logging.Logger:
    obj = logging.getLogger("video_analyzer")
    if obj.handlers:
        return obj

    obj.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    obj.addHandler(sh)

    log_dir = Path(os.environ.get("VIDEO_ANALYZER_LOG_DIR", str(Path(__file__).resolve().parents[1] / "logs")))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "video_analyzer.log", encoding="utf-8")
        fh.setFormatter(fmt)
        obj.addHandler(fh)
    except Exception:
        pass

    return obj


logger = _init_logger()

