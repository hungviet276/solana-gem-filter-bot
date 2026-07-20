from __future__ import annotations

from decimal import Decimal

from .models import (
    MarketSnapshot,
    ScoreEvaluation,
    TokenCall,
    WhaleEvent,
    deduplicate_whale_events,
)

DISCLAIMER = "Phân tích rủi ro cao, không phải khuyến nghị tài chính; không bảo đảm kết quả."


def _money(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    if value >= Decimal("1000000"):
        return f"${value / Decimal('1000000'):.2f}M"
    if value >= Decimal("1000"):
        return f"${value / Decimal('1000'):.1f}K"
    return f"${value:.2f}"


def render_alert(
    token: TokenCall,
    market: MarketSnapshot,
    whales: tuple[WhaleEvent, ...],
    evaluation: ScoreEvaluation,
) -> str:
    current_mc = market.market_cap_usd
    ratio = current_mc / token.call_mc_usd if current_mc and token.call_mc_usd else None
    positive = [component for component in evaluation.components if component.value > 0]
    negative = [component for component in evaluation.components if component.value < 0]
    failed = [check for check in evaluation.hard_checks if not check.passed]
    emoji = (
        "🔴"
        if evaluation.hard_reject
        else {"STRONG": "🟢", "EARLY": "🟡", "WATCH": "🔵"}.get(evaluation.decision.value, "⚪")
    )
    title = f"{emoji} {evaluation.decision.value} — Score {evaluation.score}/100"
    ratio_text = f"{ratio:.2f}x" if ratio else "N/A"
    holders = token.holders if token.holders is not None else "N/A"
    top10 = token.top10_pct if token.top10_pct is not None else "N/A"
    bundled = token.bundled_pct if token.bundled_pct is not None else "N/A"
    dev = token.dev_pct if token.dev_pct is not None else "N/A"
    lines = [
        title,
        f"${token.ticker} | Solana",
        f"Contract: <code>{token.contract}</code>",
        "",
        "CALL",
        f"• MC call: {_money(token.call_mc_usd)} | Current: {_money(current_mc)} "
        f"| Ratio: {ratio_text}",
        f"• Holders: {holders} | Top10: {top10}% | Bundled: {bundled}% | Dev: {dev}%",
    ]
    unique_whales = deduplicate_whale_events(whales)
    if unique_whales:
        distinct_whales = {whale.distinct_key() for whale in unique_whales}
        whale_heading = "WHALE CONVERGENCE" if len(distinct_whales) >= 2 else "WHALE FLOW"
        lines.extend(["", whale_heading])
        for whale in unique_whales[:5]:
            elapsed = int((whale.event_ts - token.call_ts).total_seconds() // 60)
            lines.append(
                f"• {whale.normalized_label.upper()}: {whale.sol_amount or 'N/A'} SOL "
                f"@ {_money(whale.whale_mc_usd)} — +{elapsed}m"
            )
        flow = next(
            (
                component
                for component in evaluation.components
                if component.code == "V2_WHALE_FLOW_GATE"
            ),
            None,
        )
        if flow:
            window_minutes = int(flow.evidence["window_seconds"]) // 60
            lines.append(
                f"• Total flow: {flow.evidence['sol_amount']} SOL/{window_minutes}m "
                f"| Distinct: {flow.evidence['distinct_count']}"
            )
    if positive or negative or failed:
        lines.extend(["", "WHY"])
        lines.extend(f"+ {item.code} ({item.value:+d})" for item in positive)
        lines.extend(f"- {item.code} ({item.value:+d})" for item in negative)
        lines.extend(f"! {item.code}" for item in failed)
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)
