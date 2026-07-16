# Paper tracking và backtest

## Không leakage

Mỗi evaluation nhận `evaluation_time`. Hàm scoring loại bỏ:

- whale có `event_ts > evaluation_time`;
- outcome xN chưa xảy ra;
- snapshot có `provider_ts > evaluation_time`.

Snapshot mới nhất phải còn freshness. Vì vậy backtest chỉ tái hiện được tín hiệu nếu dataset có snapshot tại call/whale/mốc thời gian; không được dùng giá hiện tại để lấp dữ liệu quá khứ.

## Dataset split

- `IN_SAMPLE`: dữ liệu đã dùng xây GEM_FILTER_V1.
- `VALIDATION`: tuning có kiểm soát, không publish theo một metric đơn lẻ.
- `OUT_OF_SAMPLE`: paper call mới, dùng để quyết định hiệu quả thật.

Dashboard/API luôn trả sample size. Dưới 50 Strong đủ quan sát, hệ thống hiện confidence warning. SRS yêu cầu tối thiểu 200 call mới trước đánh giá rule.

## CLI offline

```bash
gem-filter-backtest replay_cases.json --rule config/rules/GEM_FILTER_V1.json --output result.json
```

Input là danh sách `{token, snapshots, whales, outcomes}` với timestamp có timezone. Output gồm coverage, precision x2/x5/x10, median time-to-x2, warning và input hash từng row.

## Rule lifecycle

1. Tạo draft qua `POST /api/v1/rules`.
2. Chạy `simulate` trên fixture/call lịch sử.
3. Đặt shadow để đo song song mà không alert.
4. Kiểm tra out-of-sample + sample size.
5. Admin publish kèm lý do; call cũ giữ rule cũ.
6. Khi KPI xuống dưới guardrail, rollback rule đã biết tốt và audit lý do.

