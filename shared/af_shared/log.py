"""Structured JSON logger. Uses Lambda Powertools when the layer is present, plain JSON logging otherwise."""

from __future__ import annotations

import json
import logging
import os
from typing import Any


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "service": os.environ.get("POWERTOOLS_SERVICE_NAME", record.name),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _Fallback:
    def __init__(self, service: str) -> None:
        self._log = logging.getLogger(service)
        if not self._log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(_JsonFormatter())
            self._log.addHandler(handler)
            self._log.propagate = False
        self._log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        self._keys: dict[str, Any] = {}

    def append_keys(self, **keys: Any) -> None:
        self._keys.update({k: v for k, v in keys.items() if v is not None})

    def _emit(self, level: int, msg: str, exc_info: bool = False, **fields: Any) -> None:
        self._log.log(level, msg, exc_info=exc_info, extra={"extra_fields": {**self._keys, **fields}})

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, **fields)

    def exception(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, exc_info=True, **fields)


def get_logger(service: str) -> Any:
    try:
        from aws_lambda_powertools import Logger  # type: ignore[import-not-found]

        return Logger(service=service)
    except ImportError:
        return _Fallback(service)
