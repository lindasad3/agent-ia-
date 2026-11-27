"""Journalisation : console + fichier quotidien UTF-8 (logs/agent.log), secrets masqués."""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_PATTERNS = [
    (re.compile(r"bot\d{5,}:[A-Za-z0-9_-]{20,}"), "bot***"),          # token Telegram dans une URL
    (re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}\b"), "***:***"),         # token Telegram seul
    (re.compile(r"(?i)(password|passwd|token|secret)=([^&\s]+)"), r"\1=***"),
]


def redact(text: str) -> str:
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def setup(log_dir: str | Path = "logs", level: int = logging.INFO) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        handlers.append(TimedRotatingFileHandler(Path(log_dir) / "agent.log", when="midnight", backupCount=30,
                                                 encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        root.addHandler(h)
    # Les bibliothèques HTTP journalisent les URL complètes (le token Telegram en fait partie)
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
