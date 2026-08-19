import json
import logging
import logging.config
import os
import sys
from contextvars import ContextVar
from pathlib import Path

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_configured = False


class ContextFilter(logging.Filter):

    def __init__(self, service: str):
        super().__init__()
        self.service = service
        self.pid = os.getpid()

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.service = self.service
        record.pid_ = self.pid
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", "?"),
            "pid": getattr(record, "pid_", 0),
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    service: str,
    *,
    level: str = "INFO",
    fmt: str = "text",
    log_dir: Path | None = None,
) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    handlers: dict = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": fmt,
            "filters": ["context"],
        }
    }
    root_handlers = ["console"]

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(
                f"WARNING: log directory {log_dir} is not writable ({exc}) "
                "— logging to console only",
                file=sys.stderr,
            )
            log_dir = None

    if log_dir is not None:
        handlers["ai_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(log_dir / "ai.log"),
            "maxBytes": 5_000_000,
            "backupCount": 2,
            "formatter": fmt,
            "filters": ["context"],
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"context": {"()": ContextFilter, "service": service}},
            "formatters": {
                "text": {
                    "format": (
                        "%(asctime)s %(levelname)-7s %(service)s[%(pid_)d] "
                        "%(name)s [%(request_id)s] %(message)s"
                    ),
                    "datefmt": "%H:%M:%S",
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": handlers,
            "loggers": {
                "app.services.ai": {
                    "level": "INFO",
                    "handlers": ["ai_file"] if log_dir is not None else [],
                    "propagate": True,
                },
                "uvicorn.access": {"level": "WARNING", "propagate": False, "handlers": []},
            },
            "root": {"level": level.upper(), "handlers": root_handlers},
        }
    )
