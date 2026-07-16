from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import cast

from gem_filter.domain.models import Decision, MarketSnapshot, MultipleEvent, TokenCall, WhaleEvent
from gem_filter.domain.rules import DEFAULT_RULE, RuleConfig, evaluate


@dataclass(frozen=True, slots=True)
class ReplayCase:
    token: TokenCall
    snapshots: tuple[MarketSnapshot, ...]
    whales: tuple[WhaleEvent, ...]
    outcomes: tuple[MultipleEvent, ...]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    samples: int
    alerted: int
    coverage: float
    precision_x2: float | None
    precision_x5: float | None
    precision_x10: float | None
    median_time_to_x2_seconds: float | None
    warning: str | None
    rows: tuple[dict[str, object], ...]


def _latest_snapshot(snapshots: Iterable[MarketSnapshot], at: datetime) -> MarketSnapshot | None:
    visible = [snapshot for snapshot in snapshots if snapshot.provider_ts <= at]
    return max(visible, key=lambda snapshot: snapshot.provider_ts) if visible else None


def run_backtest(cases: Iterable[ReplayCase], rule: RuleConfig = DEFAULT_RULE) -> BacktestResult:
    rows: list[dict[str, object]] = []
    time_to_x2: list[float] = []
    for case in cases:
        event_times = sorted(
            {case.token.call_ts}
            | {event.event_ts for event in case.whales}
            | {snapshot.provider_ts for snapshot in case.snapshots}
        )
        best = None
        for at in event_times:
            snapshot = _latest_snapshot(case.snapshots, at)
            result = evaluate(
                case.token, snapshot, case.whales, case.outcomes, rule, evaluation_time=at
            )
            if best is None or result.score > best.score:
                best = result
        if best is None:
            continue
        max_multiple = max((event.multiple for event in case.outcomes), default=1)
        x2 = next(
            (
                event
                for event in sorted(case.outcomes, key=lambda item: item.event_ts)
                if event.multiple >= 2
            ),
            None,
        )
        if x2:
            time_to_x2.append((x2.event_ts - case.token.call_ts).total_seconds())
        alerted = best.decision in {Decision.EARLY, Decision.STRONG}
        rows.append(
            {
                "contract": case.token.contract,
                "ticker": case.token.ticker,
                "dataset_split": case.token.dataset_split.value,
                "score": best.score,
                "decision": best.decision.value,
                "alerted": alerted,
                "max_multiple": max_multiple,
                "input_hash": best.input_hash,
            }
        )
    samples = len(rows)
    alerted_rows = [row for row in rows if row["alerted"]]
    alerted_count = len(alerted_rows)

    def precision(target: int) -> float | None:
        if not alerted_rows:
            return None
        return (
            sum(1 for row in alerted_rows if cast(int, row["max_multiple"]) >= target)
            / alerted_count
        )

    return BacktestResult(
        samples=samples,
        alerted=alerted_count,
        coverage=alerted_count / samples if samples else 0.0,
        precision_x2=precision(2),
        precision_x5=precision(5),
        precision_x10=precision(10),
        median_time_to_x2_seconds=median(time_to_x2) if time_to_x2 else None,
        warning="Mẫu nhỏ; không dùng precision để publish rule." if samples < 50 else None,
        rows=tuple(rows),
    )
