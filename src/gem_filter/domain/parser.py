from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import EventType, ParsedEvent
from .rules import SOLANA_ADDRESS_RE

PARSER_VERSION = "gemtools-parser-1.0.0"

_WHALE_RE = re.compile(
    r"(?:🐋\s*)?\$?(?P<label>[\w .'-]+?)\s+whale\s+bought\s+"
    r"(?P<sol>[0-9][0-9.,]*)\s*SOL\s*@\s*\$(?P<mc>[0-9][0-9.,]*\s*[KMB]?)\s*MC",
    re.IGNORECASE,
)
_MULTIPLE_RE = re.compile(
    r"(?:just\s+did\s+|(?:^|\s)x)\s*x?(?P<multiple>[2-9][0-9]?)\b", re.IGNORECASE
)
_PEAK_RE = re.compile(r"peak\s*\$?\s*(?P<value>[0-9][0-9.,]*\s*[KMB]?)", re.IGNORECASE)
_CONTRACT_LABEL_RE = re.compile(
    r"(?:contract|ca)\s*[:：]\s*(?P<contract>[1-9A-HJ-NP-Za-km-z]{32,64})",
    re.IGNORECASE,
)
_CALL_HEADER_RE = re.compile(
    r"(?:🚀\s*)?\$?(?P<ticker>[A-Za-z0-9_]{1,32})\s*(?:\((?P<name>[^)]+)\))?",
    re.IGNORECASE,
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "call_mc_usd": ("mc", "market cap", "mcap"),
    "age_seconds": ("age",),
    "holders": ("holders", "holder"),
    "top10_pct": ("top10", "top 10"),
    "bundled_pct": ("bundled", "bundle"),
    "first50_pct": ("first50", "first 50"),
    "jeeters_pct": ("jeeters", "jeeter"),
    "quickflip_pct": ("quickflip", "quick flip"),
    "snipers_pct": ("snipers", "sniper"),
    "insiders_pct": ("insiders", "insider"),
    "dev_pct": ("dev", "developer"),
    "safe_pct": ("safe",),
    "poor_pct": ("poor",),
}


def normalize_text(raw: str) -> str:
    value = html.unescape(raw).replace("\r\n", "\n").replace("\r", "\n")
    value = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_whale_label(label: str) -> str:
    value = unicodedata.normalize("NFKC", label).replace("$", " ").casefold()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def parse_decimal(raw: str) -> Decimal:
    value = raw.strip().upper().replace("$", "").replace(" ", "")
    multiplier = Decimal("1")
    if value.endswith("K"):
        multiplier, value = Decimal("1000"), value[:-1]
    elif value.endswith("M"):
        multiplier, value = Decimal("1000000"), value[:-1]
    elif value.endswith("B"):
        multiplier, value = Decimal("1000000000"), value[:-1]
    if "," in value and "." in value:
        value = value.replace(",", "")
    elif "," in value:
        pieces = value.split(",")
        value = "".join(pieces) if len(pieces[-1]) == 3 else ".".join(pieces)
    try:
        return Decimal(value) * multiplier
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric value: {raw}") from exc


def parse_age_seconds(raw: str) -> int:
    value = raw.strip().casefold()
    total = timedelta()
    matched = False
    for number, unit in re.findall(
        r"([0-9]+(?:[.,][0-9]+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days)\b", value
    ):
        amount = float(number.replace(",", "."))
        matched = True
        if unit.startswith("s"):
            total += timedelta(seconds=amount)
        elif unit.startswith("m"):
            total += timedelta(minutes=amount)
        elif unit.startswith("h"):
            total += timedelta(hours=amount)
        else:
            total += timedelta(days=amount)
    if not matched:
        raise ValueError(f"invalid age value: {raw}")
    return int(total.total_seconds())


def classify(text: str) -> EventType:
    normalized = normalize_text(text)
    if _WHALE_RE.search(normalized):
        return EventType.WHALE_BUY
    if _MULTIPLE_RE.search(normalized):
        return EventType.MULTIPLE_RESULT
    if _extract_contract(normalized) and (
        "MC" in normalized.upper() or "holders" in normalized.casefold()
    ):
        return EventType.CALL
    if not normalized or any(
        marker in normalized.casefold()
        for marker in ("joined the channel", "pinned a message", "service message")
    ):
        return EventType.SERVICE
    return EventType.UNKNOWN


def _extract_contract(text: str) -> str | None:
    labelled = _CONTRACT_LABEL_RE.search(text)
    if labelled:
        return labelled.group("contract")
    for line in text.splitlines():
        candidate = line.strip().strip("`[]()")
        if SOLANA_ADDRESS_RE.fullmatch(candidate):
            return candidate
    return None


def _find_field(text: str, aliases: tuple[str, ...]) -> str | None:
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    match = re.search(
        rf"(?:^|[\n·|])\s*(?:{alias_pattern})\s*[:：]\s*([^\n·|]+)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _parse_call(text: str) -> tuple[dict[str, Any], list[str]]:
    payload: dict[str, Any] = {}
    warnings: list[str] = []
    contract = _extract_contract(text)
    if contract:
        payload["contract"] = contract
    else:
        warnings.append("MISSING_CONTRACT")

    first_line = text.splitlines()[0] if text.splitlines() else ""
    header = _CALL_HEADER_RE.search(first_line)
    if header:
        payload["ticker"] = header.group("ticker").upper()
        payload["token_name"] = header.group("name")
    else:
        warnings.append("MISSING_CALL_HEADER")

    for field, aliases in _FIELD_ALIASES.items():
        raw = _find_field(text, aliases)
        if raw is None:
            warnings.append(f"MISSING_{field.upper()}")
            continue
        try:
            if field == "age_seconds":
                payload[field] = parse_age_seconds(raw)
            elif field == "holders":
                payload[field] = int(parse_decimal(raw))
            elif field == "call_mc_usd":
                payload[field] = str(parse_decimal(raw))
            else:
                percent_match = re.search(r"[-+]?[0-9][0-9.,]*", raw)
                if not percent_match:
                    raise ValueError(raw)
                value = parse_decimal(percent_match.group())
                if not Decimal("0") <= value <= Decimal("100"):
                    raise ValueError(raw)
                payload[field] = str(value)
        except (ValueError, InvalidOperation):
            warnings.append(f"INVALID_{field.upper()}")
    return payload, warnings


def _parse_whale(text: str) -> tuple[dict[str, Any], list[str]]:
    match = _WHALE_RE.search(text)
    if not match:
        return {}, ["INVALID_WHALE_FORMAT"]
    label = match.group("label").strip()
    return {
        "whale_label": label,
        "normalized_label": normalize_whale_label(label),
        "sol_amount": str(parse_decimal(match.group("sol"))),
        "whale_mc_usd": str(parse_decimal(match.group("mc"))),
    }, []


def _parse_result(text: str) -> tuple[dict[str, Any], list[str]]:
    multiple = _MULTIPLE_RE.search(text)
    if not multiple:
        return {}, ["INVALID_MULTIPLE_FORMAT"]
    payload: dict[str, Any] = {"multiple": int(multiple.group("multiple"))}
    peak = _PEAK_RE.search(text)
    if peak:
        payload["peak_mc_usd"] = str(parse_decimal(peak.group("value")))
    elapsed = re.search(r"(?:in|after)\s+([^\n·|]+)", text, re.IGNORECASE)
    if elapsed:
        try:
            payload["elapsed_seconds"] = parse_age_seconds(elapsed.group(1))
        except ValueError:
            pass
    return payload, []


def parse_message(raw_text: str) -> ParsedEvent:
    text = normalize_text(raw_text)
    event_type = classify(text)
    payload: dict[str, Any] = {}
    warnings: list[str] = []
    if event_type is EventType.CALL:
        payload, warnings = _parse_call(text)
    elif event_type is EventType.WHALE_BUY:
        payload, warnings = _parse_whale(text)
    elif event_type is EventType.MULTIPLE_RESULT:
        payload, warnings = _parse_result(text)
    elif event_type is EventType.UNKNOWN:
        warnings.append("UNKNOWN_FORMAT")
    return ParsedEvent(
        event_type=event_type,
        parser_version=PARSER_VERSION,
        payload=payload,
        warnings=tuple(warnings),
        raw_text_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )
