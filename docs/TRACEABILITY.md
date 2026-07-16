# Ma trận truy vết SRS v1.0

Trạng thái `Implemented` nghĩa là có code path + test/operational control trong deliverable. `Opt-in` là chức năng SRS mức Could/Should cần credential/dữ liệu ngoài để chạy live.

| Requirement | Trạng thái | Hiện thực/kiểm chứng |
|---|---|---|
| FR-ING-001 | Implemented | Source CRUD, `TelegramSource`, bootstrap env, audit |
| FR-ING-002 | Implemented | Bot API/MTProto reader, raw envelope new/edit |
| FR-ING-003 | Implemented | Bot offset PostgreSQL, Telethon session + reconnect, raw idempotency |
| FR-ING-004 | Implemented | `html_backfill.py`, `gem-filter-import-html` report |
| FR-ING-005 | Implemented | Reader adapter không có send/edit/delete/reaction method |
| FR-PAR-001 | Implemented | `classify`: CALL/WHALE_BUY/MULTIPLE_RESULT/SERVICE/UNKNOWN |
| FR-PAR-002 | Implemented | Call fields + per-field missing/invalid warning; không fill 0 |
| FR-PAR-003 | Implemented | Whale label/SOL/MC + normalization |
| FR-PAR-004 | Implemented | x2..x99, peak, elapsed |
| FR-PAR-005 | Implemented | Correlation source + reply-to; không dùng ticker mặc định |
| FR-PAR-006 | Implemented | Nullable token link, orphan reason, scheduler retry + metric |
| FR-PAR-007 | Implemented | Parser version trên parsed record, raw revision và reparse audit |
| FR-STA-001 | Implemented | Contract lookup cho active token; source message cho event link |
| FR-STA-002 | Implemented | Event-time 5/15/30/60 window và scheduled snapshots |
| FR-STA-003 | Implemented | Distinct wallet cluster hoặc normalized label |
| FR-STA-004 | Implemented | Earliest x2 timestamp; future outcomes invisible |
| FR-STA-005 | Implemented | Domain transition guard + state history reason |
| FR-STA-006 | Implemented | Rule expiry + scheduler block trạng thái EXPIRED |
| FR-MKT-001 | Implemented | DEX Screener token-pairs by Solana contract |
| FR-MKT-002 | Implemented | Allowed quote, highest liquidity, DEX priority tie-break, reason |
| FR-MKT-003 | Implemented | Channel/provider MC cùng snapshot; discrepancy config sẵn sàng |
| FR-MKT-004 | Implemented | CALL/WHALE/ALERT_GATE/T5/T15/T30/T60 immutable snapshots |
| FR-MKT-005 | Implemented | Bounded exponential retry; primary/fallback provider chain có provenance |
| FR-MKT-006 | Implemented | Configurable liquidity hard check |
| FR-WHL-001 | Implemented | Whale profile CRUD, aliases, wallets, cluster, sample/trust |
| FR-WHL-002 | Opt-in | Helius authenticated webhook, signature correlation, enhanced SWAP verification |
| FR-WHL-003 | Opt-in | Token-in + native-out balance rule; reject transfer/airdrop/self-transfer |
| FR-WHL-004 | Implemented | Trust/sample fields; weight mặc định không bật dưới 30 mẫu |
| FR-RUL-001 | Implemented | Fixed rule version on call; immutable published lifecycle |
| FR-RUL-002 | Implemented | Genesis và Traction độc lập, reason/structure list |
| FR-RUL-003 | Implemented | Contract/stale/dev/poor/liquidity/anti-chase hard checks |
| FR-RUL-004 | Implemented | Distinct, elapsed, before-x2, SOL, event-time components |
| FR-RUL-005 | Implemented | >1.5x -10; >2x hard block, config versioned |
| FR-RUL-006 | Implemented | Components, hard checks, structures, evidence, input hash |
| FR-RUL-007 | Implemented | Rule simulate API không tạo alert |
| FR-RUL-008 | Implemented | Shadow rule được evaluate và lưu riêng; tuyệt đối không tạo alert |
| FR-ALT-001 | Implemented | WATCH/EARLY/STRONG/CONFIRMED/EXPIRED/REJECTED state/template policy |
| FR-ALT-002 | Implemented | Unique alert level/destination + outbox idempotency |
| FR-ALT-003 | Implemented | Token/contract/MC/ratio/metrics/whales/components/flags |
| FR-ALT-004 | Implemented | CopyText, DEX chart, detail URL |
| FR-ALT-005 | Implemented | `revalidate_alert`: fresh fetch + recalc + downgrade/block |
| FR-ALT-006 | Implemented | PostgreSQL atomic rate limit; event delay, không drop Strong |
| FR-ALT-007 | Implemented | Subscription levels, min score, quiet hours, sources per chat |
| FR-ALT-008 | Implemented | Disclaimer constant trong mọi alert template |
| FR-BKT-001 | Implemented | Multiple event/peak/elapsed; metrics theo token/alert cohort |
| FR-BKT-002 | Implemented | Shared pure evaluator, snapshot/whale/outcome <= evaluation time |
| FR-BKT-003 | Implemented | IN_SAMPLE/VALIDATION/OUT_OF_SAMPLE field và filter |
| FR-BKT-004 | Implemented | Coverage, precision x2/x5/x10, median time, sample warning |
| FR-BKT-005 | Implemented | JSON CLI/API data; filter run rule/split/time |
| FR-ADM-001 | Implemented | Scrypt login, signed bearer, server-side role check |
| FR-ADM-002 | Implemented | Draft, validate, simulate, publish, rollback, no edit published |
| FR-ADM-003 | Implemented | Responsive dashboard; refresh ≤10s theo thao tác/endpoint |
| FR-ADM-004 | Implemented | Performance API: sample, coverage, x2/x5/x10, confidence warning |
| FR-ADM-005 | Implemented | Append-only API surface cho audit logs; không có delete/update route |
| FR-ADM-006 | Implemented | Close/reparse và contract blacklist CRUD kèm actor/reason/audit |

## NFR và acceptance gates

| Nhóm | Control |
|---|---|
| Performance | Raw commit/outbox ngắn; async I/O; k6 30 msg/s; alert send gate metric |
| Availability | Health probes, restart policy, reconnect, durable DB/outbox |
| Reliability | At-least-once ingest + business idempotency; ambiguous delivery manual queue |
| RPO/RTO | Backup/restore scripts; production cần WAL/PITR để đạt RPO 15 phút |
| Security | Secret separation, RBAC, Helius auth/replay, no-trading CI gate |
| Observability | Structured JSON log, correlation ID, Prometheus/Grafana |
| Localization | UI/alert tiếng Việt; internal reason codes tiếng Anh |
| AC-01 | Soak gate: cần chạy thật 7 ngày và đối soát source |
| AC-02..10,12 | Automated/operational controls nằm trong tests, CI, dashboard, runbook |
| AC-11 | Paper gate: cần tối thiểu 200 call out-of-sample; không thể giả lập là pass |
