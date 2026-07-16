from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse


def request(url: str, method: str = "GET", body: dict | None = None, headers: dict | None = None):
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("Smoke test only permits HTTP(S) URLs")
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(  # noqa: S310 - scheme allowlisted above
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
        return response.status, json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    for path in ("/health/live", "/health/ready"):
        status, payload = request(args.base_url + path)
        assert status == 200 and payload["status"] in {"alive", "ready"}
    _, auth = request(
        args.base_url + "/api/v1/auth/login",
        "POST",
        {"email": args.email, "password": args.password},
    )
    _, tokens = request(
        args.base_url + "/api/v1/tokens?limit=1",
        headers={"Authorization": f"Bearer {auth['access_token']}"},
    )
    assert "items" in tokens
    print("Smoke test passed.")


if __name__ == "__main__":
    main()
