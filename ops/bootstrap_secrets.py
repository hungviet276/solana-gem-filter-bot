from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def random_value(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def main() -> None:
    secrets_dir = ROOT / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    postgres_password = random_value(24)
    grafana_password = random_value(18)
    files = {
        secrets_dir / "postgres_password.txt": postgres_password,
        secrets_dir / "grafana_password.txt": grafana_password,
    }
    for path, value in files.items():
        if not path.exists():
            path.write_text(value, encoding="utf-8")
    env_path = ROOT / ".env"
    if not env_path.exists():
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        content = content.replace("CHANGE_ME@postgres", f"{postgres_password}@postgres")
        content = content.replace("CHANGE_ME_32_BYTES_OR_MORE", random_value(), 1)
        content = content.replace("CHANGE_ME_32_BYTES_OR_MORE", random_value(), 1)
        content = content.replace("CHANGE_ME_NOW", random_value(18))
        env_path.write_text(content, encoding="utf-8")
    print("Created missing local secret files and .env. Existing values were not overwritten.")


if __name__ == "__main__":
    main()
