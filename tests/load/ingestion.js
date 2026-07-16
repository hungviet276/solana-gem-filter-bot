import http from "k6/http";
import { check } from "k6";

export const options = {
  scenarios: {
    burst_30_per_second: {
      executor: "constant-arrival-rate",
      rate: 30,
      timeUnit: "1s",
      duration: "60s",
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<5000"],
    http_req_failed: ["rate<0.005"],
  },
};

export default function () {
  const id = (__VU * 100000000) + __ITER;
  const body = JSON.stringify({
    source_channel_id: Number(__ENV.SOURCE_CHANNEL_ID || 0),
    source_message_id: id,
    event_ts: new Date().toISOString(),
    raw_text: `$LOAD${id}\n${"A".repeat(44)}\nMC: $40K\nHolders: 150`,
    raw_payload: { load_test: true },
  });
  const response = http.post(`${__ENV.BASE_URL || "http://localhost:8080"}/api/v1/ingestion/telegram`, body, {
    headers: { "Content-Type": "application/json", "X-Internal-Token": __ENV.INTERNAL_API_TOKEN },
  });
  check(response, { accepted: r => r.status === 202 });
}
