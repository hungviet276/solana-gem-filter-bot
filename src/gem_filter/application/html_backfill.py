from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackfillMessage:
    message_id: int
    reply_to_message_id: int | None
    event_ts: datetime
    text: str


class TelegramExportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[BackfillMessage] = []
        self._message_depth = 0
        self._message_id: int | None = None
        self._reply_to: int | None = None
        self._date_text: str | None = None
        self._text_parts: list[str] = []
        self._capture_text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "div" and "message" in classes and values.get("id", "").startswith("message"):
            self._message_depth = 1
            self._message_id = _last_int(values["id"])
            self._reply_to = None
            self._date_text = None
            self._text_parts = []
            return
        if self._message_depth:
            if tag == "div":
                self._message_depth += 1
            if "date" in classes and values.get("title"):
                self._date_text = values["title"]
            if tag == "a" and values.get("href", "").startswith("#go_to_message"):
                self._reply_to = _last_int(values["href"])
            if "text" in classes:
                self._capture_text_depth = self._message_depth
            elif self._capture_text_depth and tag in {"br", "p", "div"}:
                self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._message_depth:
            return
        if tag == "div":
            if self._capture_text_depth == self._message_depth:
                self._capture_text_depth = 0
            self._message_depth -= 1
            if self._message_depth == 0:
                self._finish()

    def handle_data(self, data: str) -> None:
        if self._capture_text_depth:
            self._text_parts.append(data)

    def _finish(self) -> None:
        text = re.sub(r"[ \t]+", " ", "".join(self._text_parts))
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if self._message_id is None or not text:
            return
        self.messages.append(
            BackfillMessage(
                message_id=self._message_id,
                reply_to_message_id=self._reply_to,
                event_ts=_parse_export_date(self._date_text),
                text=text,
            )
        )


def _last_int(value: str) -> int | None:
    match = re.search(r"([0-9]+)(?!.*[0-9])", value)
    return int(match.group(1)) if match else None


def _parse_export_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=UTC)
    cleaned = value.strip()
    cleaned = re.sub(r"\s+UTC(?=[+-][0-9]{2}:[0-9]{2}$)", " ", cleaned)
    if cleaned.endswith(" UTC"):
        cleaned = f"{cleaned[:-4]} +00:00"
    for pattern in (None, "%d.%m.%Y %H:%M:%S %z", "%d.%m.%Y %H:%M:%S"):
        try:
            parsed = (
                datetime.fromisoformat(cleaned)
                if pattern is None
                else datetime.strptime(cleaned, pattern)
            )
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        except ValueError:
            continue
    raise ValueError(f"Unsupported Telegram export date: {value}")


def parse_telegram_export(path: Path) -> list[BackfillMessage]:
    parser = TelegramExportParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.messages
