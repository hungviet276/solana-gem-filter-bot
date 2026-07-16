from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
BANNED_IMPORTS = {
    "solders.keypair",
    "solana.keypair",
    "jupiter_python_sdk",
}
BANNED_SYMBOLS = {
    "send_transaction",
    "send_raw_transaction",
    "sign_transaction",
    "execute_trade",
    "place_order",
    "private_key",
    "seed_phrase",
}


def main() -> None:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(
                        alias.name == banned or alias.name.startswith(f"{banned}.")
                        for banned in BANNED_IMPORTS
                    ):
                        violations.append(f"{path}: banned import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if any(
                    module == banned or module.startswith(f"{banned}.") for banned in BANNED_IMPORTS
                ):
                    violations.append(f"{path}: banned import {module}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.casefold() in BANNED_SYMBOLS:
                    violations.append(f"{path}:{node.lineno}: banned symbol {node.name}")
    if violations:
        raise SystemExit("\n".join(violations))
    print("Safety gate passed: no transaction signing/trading implementation detected.")


if __name__ == "__main__":
    main()
