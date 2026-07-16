# Chiến lược kiểm thử

## Test gates

- Unit: parser, numeric/unit normalization, scoring, distinct whale, before-x2, anti-chase, state transition, alert rendering.
- Contract: mock DEX Screener, Telegram và Helius payload/schema.
- Integration: PostgreSQL + API + worker outbox/runtime state bằng Docker Compose.
- Replay: cùng event + rule phải cho cùng `input_hash` và result.
- Load: k6 constant arrival 30 message/giây trong 60 giây, P95 ingestion dưới 5 giây.
- Resilience: duplicate, worker restart, provider stale, source reconnect, ambiguous Telegram delivery.
- Security: RBAC, webhook auth/replay, secret scan, dependency/image scan và no-trading gate.

## Mapping test nghiệp vụ SRS

`tests/test_rules.py` bao phủ TC-001..TC-006, TC-008 và TC-011; parser/state/outbox integration bao phủ phần còn lại. `TC-009` được bảo vệ bằng unique alert/outbox key; `TC-010` bằng rule gắn cố định trên call; `TC-012` bằng raw revision và audit reparse.

## Lệnh CI

```bash
pytest --cov=gem_filter.domain --cov-fail-under=90
ruff check src tests
mypy src/gem_filter/domain src/gem_filter/application/backtest.py
python ops/check_no_trading.py
docker compose config
docker compose build
```

Nghiệm thu AC-01 và AC-11 là soak/paper gates theo thời gian thực (7 ngày và 200 call mới), không thể được tuyên bố pass chỉ bằng unit test.
