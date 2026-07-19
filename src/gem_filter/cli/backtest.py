from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from gem_filter.application.backtest import ReplayCase, run_backtest
from gem_filter.domain.models import MarketSnapshot, MultipleEvent, TokenCall, WhaleEvent
from gem_filter.domain.rules import DEFAULT_RULE, RuleConfig


def _dt(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Backtest timestamps must include timezone")
    return result


def _dec(value):
    return Decimal(str(value)) if value is not None else None


def load_cases(path: Path) -> list[ReplayCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[ReplayCase] = []
    for item in payload:
        token_data = dict(item["token"])
        token_data["call_ts"] = _dt(token_data["call_ts"])
        for key in [name for name in token_data if name.endswith("_pct") or name == "call_mc_usd"]:
            token_data[key] = _dec(token_data[key])
        snapshots = []
        for source in item.get("snapshots", []):
            values = dict(source)
            values["captured_at"] = _dt(values["captured_at"])
            values["provider_ts"] = _dt(values["provider_ts"])
            for key in (
                "market_cap_usd",
                "fdv_usd",
                "liquidity_usd",
                "price_usd",
                "volume_24h_usd",
            ):
                values[key] = _dec(values.get(key))
            snapshots.append(MarketSnapshot(**values))
        whales = []
        for source in item.get("whales", []):
            values = dict(source)
            values["event_ts"] = _dt(values["event_ts"])
            values["sol_amount"] = _dec(values.get("sol_amount"))
            values["whale_mc_usd"] = _dec(values.get("whale_mc_usd"))
            whales.append(WhaleEvent(**values))
        outcomes = []
        for source in item.get("outcomes", []):
            values = dict(source)
            values["event_ts"] = _dt(values["event_ts"])
            values["peak_mc_usd"] = _dec(values.get("peak_mc_usd"))
            outcomes.append(MultipleEvent(**values))
        cases.append(
            ReplayCase(TokenCall(**token_data), tuple(snapshots), tuple(whales), tuple(outcomes))
        )
    return cases


def run() -> None:
    parser = argparse.ArgumentParser(description="Leakage-safe event-time backtest")
    parser.add_argument("input", type=Path, help="Replay cases JSON")
    parser.add_argument("--rule", type=Path, help="Optional RuleConfig JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rule = DEFAULT_RULE
    if args.rule:
        values = json.loads(args.rule.read_text(encoding="utf-8"))
        decimal_fields = {
            "mc_discrepancy_warn_pct",
            "min_liquidity_usd",
            "dev_hard_limit_pct",
            "poor_hard_limit_pct",
            "soft_chase_ratio",
            "hard_chase_ratio",
            "min_whale_flow_sol",
            "quality_top10_min_pct",
            "quality_top10_max_pct",
            "genesis_first50_min_pct",
        }
        for key in decimal_fields & values.keys():
            values[key] = _dec(values[key])
        rule = RuleConfig(**values)
    result = asdict(run_backtest(load_cases(args.input), rule))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    run()
