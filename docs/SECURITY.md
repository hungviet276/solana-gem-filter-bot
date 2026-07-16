# Bảo mật và ranh giới an toàn

## Các invariant bắt buộc

- Không nhập, lưu, log hoặc truyền private key/seed phrase.
- Không có Solana keypair, signer, RPC `sendTransaction`, DEX swap hoặc trade executor.
- Reader nguồn chỉ nhận message; alert bot là credential riêng và chỉ gửi tới destination đã cấu hình.
- Secret chỉ đi qua environment/secret file, không nằm trong bảng nghiệp vụ hoặc response API.

CI chạy `ops/check_no_trading.py` để chặn module/import/symbol nguy hiểm. Đây là safety gate bổ sung, không thay thế code review.

## Secret inventory

| Secret | Consumer | Kiểm soát |
|---|---|---|
| Telegram source token/session | reader | process riêng, volume riêng, 2FA cho MTProto |
| Alert bot token | worker | không cấp cho reader/API |
| Internal API token | reader/importer/API | constant-time compare, rotate định kỳ |
| Auth signing secret | API | tối thiểu 32 random bytes |
| PostgreSQL/Grafana password | container secret | file permission, không commit |
| Helius webhook secret | API | exact Authorization compare, dedupe, skew check |

## RBAC

- `Admin`: source, rule publish/rollback, whale profile, subscriptions, manual control.
- `Analyst`: xem/simulate/backtest và manual review được giới hạn.
- `Viewer`: token/dashboard/metrics.
- `Auditor`: dữ liệu read-only và audit log.

Token truy cập là HMAC-SHA256, có expiry và role được đối chiếu lại với database mỗi request. Password dùng `scrypt` với salt riêng.

## Threat controls

- Format giả/đổi: parser version, warning, unknown/manual queue, market verification.
- Provider stale: timestamp gate và block alert.
- Duplicate: unique key + outbox + alert state machine.
- Rule sai: immutable published version, draft/simulate/publish/rollback và audit.
- Backtest leakage: filter mọi whale/outcome/snapshot theo evaluation time.
- Webhook replay: Helius auth header + signature/event hash TTL trong PostgreSQL `runtime_state`.
- XSS: dashboard escape dynamic text; Telegram content dùng HTML có kiểm soát.
- Supply chain: pin dependency lock và container digest ở release; scan với `pip-audit`/Trivy trong CI release.

## Rotate/incident

Khi nghi lộ: bật `ALERTS_KILL_SWITCH=true`, rotate credential tại provider, cập nhật secret store, restart đúng service, xem audit/outbox, rồi chỉ bật alert sau smoke test. Không xóa raw event hoặc audit log.
