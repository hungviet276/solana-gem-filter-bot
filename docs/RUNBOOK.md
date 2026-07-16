# Runbook vận hành

## Kiểm tra nhanh

```bash
docker compose ps
docker compose logs --since=15m api worker scheduler telegram-reader
curl -fsS http://localhost:8080/health/live
curl -fsS http://localhost:8080/health/ready
```

## Kill switch alert

Đặt `ALERTS_KILL_SWITCH=true` trong secret/config runtime và restart worker. Reader/API vẫn lưu raw event. Kiểm tra `outbox_events`, provider freshness và parser health trước khi bật lại.

## Reader mất kết nối

1. Kiểm tra network và log reader.
2. Bot API: kiểm tra webhook không đồng thời bật; offset nằm tại key `reader:bot_api:offset` trong bảng `runtime_state` của PostgreSQL.
3. MTProto: kiểm tra session volume/permission và 2FA; không copy session vào log/ticket.
4. Restart chỉ `telegram-reader`.
5. Backfill khoảng message ID/thời gian thiếu; đối soát unique raw events.

## Provider rate limit/outage

1. Xem `market_provider_errors_total` và P95 latency.
2. Hệ thống tự retry backoff; stale gate chặn Strong.
3. Nếu kéo dài, giữ kill switch hoặc cấu hình fallback adapter đã được contract-test.
4. Không hạ `max_snapshot_age_seconds` chỉ để ép alert.

## Parser format đổi

1. Khi success <95%, bật kill switch Strong.
2. Export raw examples, tạo fixture không chứa secret.
3. Tăng `PARSER_VERSION`, chạy labeled dataset ≥98%.
4. Deploy và enqueue reparse; raw event không bị sửa.

## Rollback rule

Gọi `POST /api/v1/rules/{known-good-id}/rollback?reason=...` bằng Admin. Kiểm tra audit log, active config và chỉ call mới dùng rule rollback. Call đang mở giữ phiên bản gắn lúc tạo.

## Orphan/failed event

Scheduler retry orphan theo `(source_id, reply_to_message_id)`. Xem whale/multiple có `token_call_id IS NULL`; backfill call gốc rồi chờ scheduler. Outbox `DEAD` cần đọc `last_error`; alert `MANUAL_REVIEW` không được resend mù.

## Backup/restore

```bash
./ops/backup.sh backups
./ops/restore.sh backups/<file>.dump
```

Backup dùng custom format và checksum. Restore phải diễn tập ở staging hàng tháng; RPO mục tiêu 15 phút cần WAL/PITR từ managed PostgreSQL hoặc archive pipeline ngoài script snapshot này. RTO mục tiêu 2 giờ.

## Rotate secret

1. Bật kill switch nếu credential alert/source bị nghi lộ.
2. Rotate tại Telegram/Helius/database.
3. Cập nhật secret store, restart đúng service.
4. Smoke test health + ingestion mock + alert test channel.
5. Ghi incident/audit; không đưa giá trị secret vào ticket.
