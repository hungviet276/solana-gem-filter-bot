from __future__ import annotations

import os

VARS = (
    "PYTHON_BASE_IMAGE",
    "POSTGRES_IMAGE",
    "PROMETHEUS_IMAGE",
    "GRAFANA_IMAGE",
    "CADDY_IMAGE",
    "APP_IMAGE",
)


def main() -> None:
    if os.getenv("APP_ENV") != "prod":
        print("Image digest gate skipped outside prod.")
        return
    missing = [name for name in VARS if "@sha256:" not in os.getenv(name, "")]
    if missing:
        raise SystemExit(f"Production images must be digest-pinned: {', '.join(missing)}")
    print("Production image digest gate passed.")


if __name__ == "__main__":
    main()
