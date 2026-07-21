from __future__ import annotations

import html
from decimal import Decimal

from .early_candidate import EarlyCandidateEvaluation
from .models import (
    MarketSnapshot,
    ScoreEvaluation,
    TokenCall,
    WhaleEvent,
    deduplicate_whale_events,
)

DISCLAIMER = "Phân tích rủi ro cao, không phải khuyến nghị tài chính; không bảo đảm kết quả."
EARLY_DISCLAIMER = (
    "Cảnh báo sớm chỉ để nghiên cứu/paper tracking; không phải tín hiệu mua và không bảo đảm x2."
)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    if value >= Decimal("1000000"):
        return f"${value / Decimal('1000000'):.2f}M"
    if value >= Decimal("1000"):
        return f"${value / Decimal('1000'):.1f}K"
    return f"${value:.2f}"


def render_early_candidate_x2(
    token: TokenCall,
    evaluation: EarlyCandidateEvaluation,
) -> str:
    ticker = html.escape(token.ticker, quote=False)
    token_name = html.escape(token.token_name, quote=False) if token.token_name else None
    contract = html.escape(token.contract, quote=False)
    age = _duration(token.age_seconds)
    name_suffix = f" ({token_name})" if token_name else ""
    lines = [
        "🟠 EARLY CANDIDATE X2 — CALL ĐẦU",
        f"${ticker}{name_suffix} | Solana",
        f"Contract: <code>{contract}</code>",
        "",
        "⚠️ Chưa có xác nhận whale — đánh giá độc lập từ bài call đầu tiên.",
        "",
        "INITIAL CALL",
        f"• MC: {_money(token.call_mc_usd)} | Age: {age} | Holders: {_value(token.holders)}",
        f"• Top10: {_pct(token.top10_pct)} | Jeeters: {_pct(token.jeeters_pct)} "
        f"| Poor: {_pct(token.poor_pct)}",
        f"• Bundled: {_pct(token.bundled_pct)} | Snipers: {_pct(token.snipers_pct)} "
        f"| Dev: {_pct(token.dev_pct)}",
        "",
        "RULE MATCH",
    ]
    labels = {
        "JEETERS_10_20": "Jeeters 10–20%",
        "POOR_MAX_15": "Poor ≤15%",
        "TOP10_20_30": "Top10 20–30%",
        "DEV_BELOW_10": "Dev dưới 10%",
        "BUNDLED_BELOW_25": "Bundled dưới 25%",
        "SNIPERS_BELOW_15": "Snipers dưới 15%",
    }
    lines.extend(f"✓ {labels[check.code]}" for check in evaluation.checks if check.passed)
    lines.extend(
        [
            "",
            "Backtest tham khảo: 31/56 call đạt x2 (55,36%); mẫu nhỏ, kết quả có thể thay đổi.",
            EARLY_DISCLAIMER,
        ]
    )
    return "\n".join(lines)


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "N/A"
    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m{remaining_seconds}s" if remaining_seconds else f"{minutes}m"
    return f"{remaining_seconds}s"


def _value(value: object | None) -> str:
    return str(value) if value is not None else "N/A"


def _pct(value: Decimal | None) -> str:
    return f"{value}%" if value is not None else "N/A"


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
