# Solana Gem Filter Bot

Hệ thống read-only để thu thập call token Solana từ Telegram, chuẩn hóa/correlation message, xác minh market data, theo dõi whale convergence theo event-time, chấm điểm theo rule version, paper tracking, backtest và phát cảnh báo Telegram có chọn lọc.

> Ranh giới an toàn: dự án không có chức năng ký giao dịch, quản lý ví, lưu private key, đặt lệnh, mua/bán, copy trade hay thao túng volume. Alert là dữ liệu phân tích rủi ro cao, không phải khuyến nghị tài chính.

## Phạm vi đã triển khai

- Telegram Bot API long polling và MTProto read-only; new/edited message, offset/reconnect, import `messages.html`.
- Raw event bất biến được commit trước khi parse; versioned parser, warning cho field thiếu, orphan retry.
- State machine theo contract/source message; cửa sổ 5/15/30/60 phút, distinct whale, before-x2 và expiry.
- DEX Screener pool selection theo quote-token + liquidity; snapshot tại call/whale/alert gate/mốc thời gian.
- Rule mặc định `GEM_FILTER_V2_FLOW_8`: bắt buộc cấu trúc Top10/Genesis, tổng whale
  flow tối thiểu 8 SOL trong 15 phút và hard anti-chase 2x.
- Rule `GEM_FILTER_V1` vẫn được giữ bất biến để rollback; mọi evaluation có
  explainability + input hash.
- Transactional outbox, alert idempotency, fresh send gate, rate limit, kill switch và disclaimer.
- Paper outcome x2/x3/x5/x10, event-time backtest không leakage, dataset split và confidence warning.
- FastAPI, RBAC, dashboard, rule draft/simulate/publish/rollback, source/subscription/whale profile/audit APIs.
- PostgreSQL runtime state/outbox, Alembic, Docker Compose, Prometheus/Grafana, backup/restore runbook và CI gates.

## Khởi động nhanh

Yêu cầu: Docker Engine + Docker Compose v2.

```powershell
python ops/bootstrap_secrets.py
docker compose up --build -d
docker compose ps
```

Mở:

- Dashboard/API: `http://localhost:8080`
- OpenAPI: `http://localhost:8080/docs`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

Production TLS dùng Caddy override sau khi đặt `PUBLIC_DOMAIN` và pin mọi image bằng digest:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

### Chế độ bot-only tiết kiệm tài nguyên

Nếu chỉ cần đọc Telegram, xử lý rule và gửi cảnh báo, dùng override bot-only:

```powershell
docker compose -f docker-compose.yml -f deploy/docker-compose.bot.yml up --build -d
```

Chế độ này không mở dashboard/API ra host, không chạy Prometheus, Grafana hoặc Caddy,
và không gắn nút lịch sử nội bộ vào cảnh báo. PostgreSQL, API nội bộ, worker,
scheduler và Telegram reader vẫn được giữ vì cần cho offset, chống trùng, outbox,
state machine và đánh giá rule. Muốn bật quan sát sau này, thêm
`--profile observability`.

Tài khoản dashboard ban đầu nằm trong `.env`. Đổi ngay `BOOTSTRAP_ADMIN_PASSWORD` sau lần đăng nhập đầu tiên. Không commit `.env`, thư mục `secrets/` hoặc file Telegram session.

### Chế độ nguồn Telegram

`TELEGRAM_SOURCE_MODE` nhận một trong ba giá trị:

- `mock`: nhận từng JSON envelope từ stdin, phù hợp DEV.
- `bot_api`: chỉ gọi `getUpdates`, nhận `channel_post` và `edited_channel_post`.
- `mtproto`: Telethon `NewMessage`/`MessageEdited`; reader không có code path gửi/sửa/xóa/reaction.

Khai báo channel ID trong `TELEGRAM_SOURCE_IDS`. Với Bot API, bot phải được cấp quyền hợp pháp để nhìn thấy channel post. Với MTProto, dùng tài khoản reader riêng, bật 2FA và bảo vệ volume chứa session.

Lần đầu đăng nhập MTProto, Compose chạy `telegram-session-init` một lần để cấp quyền
ghi volume session cho user không đặc quyền `app`. Không chạy `docker compose down -v`
nếu muốn giữ phiên đăng nhập Telegram và dữ liệu PostgreSQL.

### Cảnh báo EARLY CANDIDATE X2 độc lập

Luồng này chỉ dùng dữ liệu xuất hiện trong bài call đầu tiên, không đọc whale, market snapshot
hoặc kết quả x2 để quyết định gửi. Bật và chọn channel đích riêng trong `.env`:

```dotenv
EARLY_CANDIDATE_X2_ENABLED=true
EARLY_CANDIDATE_X2_DESTINATION_CHAT_IDS=-1001234567890
```

Rule `EARLY_CANDIDATE_X2_V1` yêu cầu đồng thời: Jeeters 10–20%, Poor ≤15%,
Top10 20–30%, Dev <10%, Bundled <25% và Snipers <15%. Cảnh báo dùng level, destination,
outbox topic, revalidation và rate-limit namespace riêng; luồng `EARLY`/`STRONG` cũ không bị
thay đổi. Tin nhắn có nút Search X/Twitter và Check Mevx. `ALERTS_KILL_SWITCH=true` vẫn dừng
cả hai luồng trong tình huống khẩn cấp.

## Import lịch sử

```powershell
.venv\Scripts\gem-filter-import-html.exe messages.html --source-channel-id -1001234567890 --api-url http://localhost:8080/api/v1/ingestion/telegram
```

CLI báo số message tìm thấy, accepted, duplicate và lỗi. Raw event được lưu bất biến và worker xử lý lại theo cùng pipeline live.

## Chạy kiểm thử

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\pytest --cov=gem_filter.domain --cov-fail-under=90
.venv\Scripts\ruff check src tests
.venv\Scripts\python ops/check_no_trading.py
docker compose config
docker compose build
```

Load test 30 message/giây dùng k6:

```powershell
k6 run -e INTERNAL_API_TOKEN=... -e SOURCE_CHANNEL_ID=0 tests/load/ingestion.js
```

## Cấu trúc

```text
src/gem_filter/
  domain/          pure parser, scoring, state machine, alert rendering
  application/     processing pipeline, HTML backfill, event-time backtest
  infrastructure/ PostgreSQL runtime state/DEX/Telegram adapters
  api/             FastAPI, RBAC và dashboard
  services/        reader, worker, scheduler
  cli/             import và backtest ngoại tuyến
migrations/        schema versioned
deploy/            Prometheus/Grafana
docs/              architecture, security, test, runbook, traceability
ops/               bootstrap secrets, backup/restore và safety gates
```

## Tài liệu vận hành

- [Kiến trúc](docs/ARCHITECTURE.md)
- [Bảo mật](docs/SECURITY.md)
- [Runbook](docs/RUNBOOK.md)
- [Backtest và paper tracking](docs/BACKTEST.md)
- [Chiến lược kiểm thử](docs/TESTING.md)
- [Biên bản xác minh bản bàn giao](docs/VERIFICATION.md)
- [Ma trận truy vết SRS](docs/TRACEABILITY.md)

Adapter được đối chiếu với tài liệu chính thức: [DEX Screener API](https://docs.dexscreener.com/api/reference), [Telegram Bot API](https://core.telegram.org/bots/api), [Helius Webhooks](https://www.helius.dev/docs/webhooks).
