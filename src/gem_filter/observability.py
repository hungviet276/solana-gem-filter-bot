from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")

TELEGRAM_EVENTS = Counter("telegram_events_total", "Telegram events", ["type", "status"])
ORPHAN_EVENTS = Gauge("orphan_events_total", "Unresolved orphan events")
MARKET_LATENCY = Histogram(
    "market_provider_latency_seconds", "Market provider latency", ["provider"]
)
MARKET_ERRORS = Counter(
    "market_provider_errors_total", "Market provider errors", ["provider", "code"]
)
SCORE_EVALUATIONS = Counter(
    "score_evaluations_total", "Score evaluations", ["decision", "rule_version"]
)
ALERTS = Counter("alerts_total", "Alerts", ["level", "status"])
ALERT_LATENCY = Histogram("alert_latency_seconds", "Alert latency")
DUPLICATE_PREVENTED = Counter(
    "duplicate_prevented_total", "Duplicate business events prevented", ["kind"]
)
INGESTION_LAG = Histogram("telegram_ingestion_lag_seconds", "Telegram event ingestion lag")


class JsonFormatter(logging.Formatter):
    _redacted = {"token", "password", "secret", "authorization", "api_hash", "phone", "session"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        for key in ("token_call_id", "rule_version", "event_id", "source_message_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        for key in tuple(payload):
            if key.casefold() in self._redacted:
                payload[key] = "[REDACTED]"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs complete request URLs. Telegram embeds the bot token in its URL,
    # so INFO request logging would disclose credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
