from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from gem_filter.application.html_backfill import parse_telegram_export
from gem_filter.config import get_settings


async def import_file(
    path: Path, source_channel_id: int, api_url: str, internal_token: str
) -> dict[str, object]:
    messages = parse_telegram_export(path)
    accepted = 0
    duplicates = 0
    failures: list[dict[str, object]] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for message in messages:
            try:
                response = await client.post(
                    api_url,
                    headers={"X-Internal-Token": internal_token},
                    json={
                        "source_channel_id": source_channel_id,
                        "source_message_id": message.message_id,
                        "reply_to_message_id": message.reply_to_message_id,
                        "event_ts": message.event_ts.isoformat(),
                        "raw_text": message.text,
                        "edited": False,
                        "revision": 0,
                        "raw_payload": {"backfill": True, "source_file": path.name},
                    },
                )
                response.raise_for_status()
                result = response.json()
                if result.get("status") == "duplicate":
                    duplicates += 1
                else:
                    accepted += 1
            except Exception as exc:
                failures.append(
                    {"message_id": message.message_id, "error": f"{type(exc).__name__}: {exc}"}
                )
    return {
        "file": str(path),
        "found": len(messages),
        "accepted": accepted,
        "duplicates": duplicates,
        "failed": len(failures),
        "failures": failures[:100],
    }


def run() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Import a Telegram messages.html export")
    parser.add_argument("path", type=Path)
    parser.add_argument("--source-channel-id", required=True, type=int)
    parser.add_argument("--api-url", default=settings.ingestion_api_url)
    args = parser.parse_args()
    result = asyncio.run(
        import_file(
            args.path,
            args.source_channel_id,
            args.api_url,
            settings.internal_api_token.get_secret_value(),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
