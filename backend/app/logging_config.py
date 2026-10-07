"""Structured JSON logging: one JSON object per line, like incident-demo."""

import json
import logging
import sys

LOGGER_NAME = "incident-backend"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": LOGGER_NAME,
            "msg": record.getMessage(),
        }
        # Extra structured fields: log.info("msg", extra={"fields": {...}}).
        # They must not reuse the base keys above, or they would overwrite them.
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    log = logging.getLogger(LOGGER_NAME)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    return log
