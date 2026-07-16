# Biên bản xác minh bản bàn giao 1.0.0

Ngày kiểm tra: 2026-07-16.

## Kết quả tự động

- 42/42 test đạt.
- Coverage cho domain engine và event-time backtest: 97,51% (gate: 90%).
- Ruff lint/format: đạt trên `src`, `tests`, `ops`, `migrations`.
- Mypy strict: đạt trên domain và backtest engine.
- Safety gate: không phát hiện code path ký giao dịch, đặt lệnh hoặc mua/bán.
- OpenAPI: sinh thành công 25 path.
- PostgreSQL DDL: biên dịch thành công; runtime state thay Redis được quản lý bằng migration `0002`.
- Docker Compose validation và application image build: đạt.

## Smoke-test Docker Compose

Stack tạm gồm PostgreSQL, migration, API, worker và scheduler đã được dựng từ
image bàn giao. Kết quả:

1. Migration `0001` chạy và thoát mã 0.
2. API và PostgreSQL healthy; worker/scheduler giữ trạng thái running.
3. `/health/live`, `/health/ready`, đăng nhập RBAC và danh sách token trả về thành công.
4. Event Telegram thử nghiệm được nhận; gửi lại cùng revision trả về `duplicate`.
5. Worker đọc transactional outbox và ghi parsed record `UNKNOWN:REVIEW`.
6. Stack, volume và secret thử nghiệm đã được dọn sau kiểm tra.

## Gates cần dữ liệu/thời gian thực

- AC-01 cần soak test liên tục 7 ngày với channel và credential thật.
- AC-11 cần tối thiểu 200 call mới thuộc tập out-of-sample.
- Telegram, DEX Screener fallback và Helius live cần credential/quota production của đơn vị vận hành.

Các gate này không được tuyên bố đạt bằng dữ liệu giả. Runbook và dashboard đã có control để
theo dõi, đối soát và nghiệm thu khi môi trường thật sẵn sàng.
