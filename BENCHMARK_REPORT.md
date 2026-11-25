# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ file report trong [`reports/`](reports/) (JSON do `load_test.py --out` và `run_eval.py --out` ghi ra),
> [`benchmark-results/`](benchmark-results/) (CSV của Locust) và [`reports/alert_experiments.txt`](reports/alert_experiments.txt) (log theo dõi alert).
>
> **Lưu ý về môi trường mục 4–5:** máy không có Docker/WSL nên stack observability chạy bằng **binary Windows cùng version với `docker-compose.yml`**:
> Prometheus 3.16.0, Grafana 13.2.4, Jaeger 2.23.0, Nginx 1.28.0, Locust 2.47.0.
> - Agent chạy bằng **đúng code `production/app`** (mỗi instance là 1 process `uvicorn` ở cổng 8101–8103, `HOSTNAME=agent-N`, `--no-access-log`, 1 worker).
> - Config dùng bản sao của `prometheus.yml`, `nginx.conf` và Grafana provisioning, chỉ đổi hostname Docker (`agent`, `prometheus`, `jaeger`) thành `127.0.0.1`. Phần service discovery đổi từ `dns_sd` sang `file_sd`, và Nginx `server agent:8000 resolve` đổi sang liệt kê port.
> - Alert rules và dashboard dùng **nguyên file trong repo**.
> - `docker pause` được thay bằng `psutil.Process.suspend()` (treo process, giống pause container).
> - Khác biệt quan trọng: không có giới hạn `cpus: "0.5"` / `memory: 256M` như trong compose.

**Họ tên:** Dương Thị Huệ — 2A202603430
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Windows 11, Intel Core Ultra 5 125H (18 luồng), 15.4 GB RAM. Python 3.12 venv, không Docker. Agent và load generator chạy trên cùng một máy. Mục 2–3: agent develop là 1 process uvicorn. Mục 4–5: stack native như ghi chú ở trên.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** | Mock LLM mất ~120–180 ms mỗi call, p50 đo được ~200 ms. 500 ms chừa ~2.5× p50 cho jitter và retrieval mà người dùng chat vẫn thấy nhanh. Dùng p95 thay vì avg để không giấu được nhóm user bị chậm (xem 2.2). Đây cũng là ngưỡng mặc định của `load_test.py` |
| Error rate `/ask` | < **2 %** | Upstream mock có `MOCK_ERROR_RATE=0.01` (1%), ngưỡng 2% chừa gấp đôi mức nền. Khi vượt là có sự cố thật chứ không còn là nhiễu (alert production dùng 5% kèm `for: 1m` để tránh báo nhầm) |
| Accuracy trên golden dataset | ≥ **85 %** | v1 4.3% là không dùng được, v2 91.3% đã chấp nhận được. 85% chặn được regression lớn mà không fail vì 1–2 câu. Job CI dùng `--min-accuracy 0.85` kèm `--baseline` để bắt mọi câu bị regression |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, `--requests 300`, `MOCK_SLOW_RATE=0.03` mặc định)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.26 | 201.5 | 236.8 | 1698.5 | 1.67% (5×503) | ✅ PASS |
| 10 | 36.89 | 199.5 | 300.4 | 1671.1 | 1.33% (4×503) | ✅ PASS |
| 50 | 96.8 | 208.8 | 505.6 | 1737.0 | 0.33% (1×503) | ❌ FAIL (p95 > 500) |
| 100 | 45.39 | 608.2 | 4291.8 | 4717.0 | 1.67% (5×503) | ❌ FAIL (p95 > 500) |

File: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

**Nhận xét:** RPS **không tăng tuyến tính** theo concurrency.

- **c = 1 → 10:** gần tuyến tính. Theo định luật Little, RPS ≈ concurrency / latency: 1/0.237 s ≈ 4.2 và 10/0.234 s ≈ 43 (đo được 36.9). p50 gần như không đổi (~200 ms) vì mock LLM là `asyncio.sleep`, event loop phục vụ song song được.
- **c = 50:** RPS vẫn tăng lên 96.8 nhưng p95 bắt đầu vượt 500 ms. Request đã phải xếp hàng, đây là điểm "gối" của đường cong.
- **c = 100:** **RPS giảm còn 45.4**, p50 lên 608 ms và p95 4.3 s. Agent chỉ có 1 process Python. Event loop phải parse JSON, chạy middleware và ghi log cho 100 kết nối cùng lúc, lại tranh CPU với load generator chạy trên cùng máy, nên thời gian xếp hàng lớn hơn cả thời gian gọi LLM. Server đã bão hoà: tăng concurrency chỉ làm tăng latency, không tăng throughput.

Muốn tăng tiếp phải scale ngang (nhiều worker hoặc instance sau load balancer, mục 5), không phải tăng concurrency. Error rate 0.3–1.7% ở mọi mức là lỗi 503 ngẫu nhiên của mock upstream (`MOCK_ERROR_RATE=0.01`), không phải do quá tải.

### 2.2 Tail latency (`--requests 300 --concurrency 20`)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 207.9 | 453.5 | 480.9 |
| 0.03 (mặc định) | 243.6 | 473.5 | 1717.0 |
| 0.1 | 324.9 | 1699.5 | 1715.7 |

File: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

**Nhận xét:**

- **0 → 0.03:** avg chỉ tăng **+35.7 ms** (+17%) nhưng **p99 nhảy từ 481 ms lên 1717 ms** (×3.6). Khoảng 3% request bị cộng thêm 1500 ms, rơi đúng vào nhóm 1% chậm nhất nên p99 bị kéo lên ~1.7 s, còn p95 gần như không đổi.
- **0.03 → 0.1:** 10% request chậm, vượt quá 5% nên **p95 cũng nhảy lên 1699 ms**. avg chỉ lên 325 ms, nhìn vẫn có vẻ "ổn".
- **p99 là chỉ số nhạy nhất**, rồi tới p95. Average nhạy kém nhất vì 1500 ms chậm thêm bị chia đều cho 97% request nhanh.
- **Vì sao SLO không đặt theo average:** average che giấu nhóm user bị chậm. Ở `MOCK_SLOW_RATE=0.1`, cứ 10 user thì 1 người chờ ~1.7 s, vậy mà avg 325 ms vẫn "đạt" ngưỡng 500 ms. SLO theo p95/p99 cam kết trực tiếp với trải nghiệm của X% user và phát hiện tail latency ngay khi nó xuất hiện.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100% (23/23)** |
| Avg tokens / câu | 57.0 | 109.0 | 114.1 |
| Tổng cost (USD) | 0.000402 | 0.000760 | 0.000794 |
| Cost / câu đúng (USD) | 0.00040215 | 0.00003619 | 0.00003453 |
| Latency p50 / p95 (ms) | 181.7 / 1475.6 | 215.6 / 1556.3 | 202.7 / 1574.7 |
| Quality gate | ❌ FAIL (accuracy < 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (`--min-accuracy 1.0`, 0 regression so với v2) |

File: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02` (JWT khác gì API key?) | `token`, `hết hạn`, `chữ ký` | Thêm entry `(["jwt", "json web token"], "API key là chuỗi bí mật cố định, không tự hết hạn. JWT là token có chữ ký số ... thời điểm hết hạn (exp) ...")` vào `KNOWLEDGE_BASE` |
| `scaling-03` (Kubernetes HPA scale dựa trên chỉ số nào?) | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "horizontal pod autoscaler", "autoscal"], "Kubernetes HPA ... dựa trên CPU, memory hoặc custom metrics như RPS ...")` |

Nguyên nhân: không entry nào trong KB khớp từ khoá của 2 câu này, retrieval trả rỗng nên v2 rơi về câu trả lời chung chung. Trước khi thêm, tôi grep `golden_dataset.jsonl` để chắc `jwt`, `hpa`, `kubernetes`, `autoscal` không xuất hiện ở câu khác, tránh "cướp" retrieval của câu đang đúng. Entry mới đặt **cuối** danh sách, nên khi số keyword khớp bằng nhau thì entry cũ vẫn được ưu tiên.

Sau khi sửa, khởi động lại v2 và chạy `run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json`: **23/23, cải thiện 2 câu, regression 0, Quality gate ✅ PASS**. Phần sửa cũng đã chép sang `production/utils/mock_llm.py` cho Docker stack.

**Quality gate trong CI:** [`.github/workflows/eval.yml`](.github/workflows/eval.yml) chạy trên mỗi pull request:

1. Bật agent v2 (`MOCK_ERROR_RATE=0`).
2. Chạy `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`.

`run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có câu bị regression so với baseline. Step đó fail kéo theo job fail, và khi bật *Require status checks to pass* cho nhánh `main` thì PR không merge được. Kiểm chứng: chạy eval v1 với `--baseline reports/eval_v2.json` cho **exit code = 1**, báo `Câu bị regression : 20`, `Quality gate ❌ FAIL`.

**Trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 có bước retrieval (RAG) nên accuracy tăng từ 4.3% lên 91.3% (và 100% sau khi sửa KB).
- **Chi phí mỗi câu:** prompt của v2 chứa thêm tài liệu nên tốn **~1.9× token/câu** (109 so với 57) và **~1.9× tổng cost** (0.000760 so với 0.000402).
- **Chi phí trên câu đúng:** v2 **rẻ hơn ~11 lần** (0.0000362 so với 0.000402 USD), vì v1 tốn tiền cho 22 câu trả lời sai. Đây là lý do "cost / câu đúng" là chỉ số đúng để so hai phiên bản: rẻ mỗi request mà sai thì vẫn đắt.
- **Latency:** v2 chậm hơn một chút, p50 +34 ms do bước retrieval ~20 ms và prompt dài hơn (latency mock tăng theo số input token). p95 của cả hai đều bị tail latency của mock chi phối.

Kết luận: v2 đáng triển khai. Cái giá phải trả là token và vài chục ms, phần thu về là chất lượng.

---

## 4. Observability stack (production)

Tải nền: Locust headless **30 user, ramp 5/s** (`locustfile.py` của repo) bắn vào Nginx `:8080`, đi tới 2 instance agent v2.

Kiểm tra nhanh qua Nginx: response có `x-trace-id` và `X-Served-By` luân phiên giữa `127.0.0.1:8101` và `127.0.0.1:8102`. `curl -i http://localhost:8080/metrics` trả **`HTTP/1.1 404 Not Found`**. Prometheus thấy target `127.0.0.1:8101` và `127.0.0.1:8102` đều `up`.

**promtool:** `promtool test rules alert_rules_test.yml` cho kết quả **`SUCCESS`** (promtool 3.16.0 bản Windows).

### Exercise 7.1 (develop): metric và cardinality

- **Counter:** `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{type="input"} 91.0`, `llm_cost_usd_total`.
- **Gauge:** `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0`.
- **Histogram:** `http_request_duration_seconds_bucket{le=...}` / `_count` / `_sum`, `llm_request_duration_seconds_*`, và metric tôi thêm là `agent_question_length_chars_*` (`count 1.0`, `sum 40.0` sau một câu hỏi 40 ký tự).
- **Nếu dùng `request.url.path` làm label:** bot quét `/wp-admin`, `/.env`, `/a1b2c3`... thì mỗi URL lạ tạo thêm một time series cho counter và ~13 series cho mỗi histogram. Cardinality bùng nổ, Prometheus hết RAM, query chậm. Dùng route template (`/ask`) và gom phần còn lại vào `unmatched` thì số series có hạn. Khi chạy thật, request lạ được ghi là `route="unmatched",status="404"`.
- `agent_question_length_chars` là **Histogram** với bucket cố định (20…2000), không dùng label `length=...`, cũng vì lý do cardinality.

### Exercise 7.4: câu hỏi (số đọc từ Prometheus/Grafana lúc 20:05–20:11, 30 user, 2 instance)

1. **p95 và avg:** p95 = **293–295 ms** (PromQL `histogram_quantile(0.95, …[1m])`, panel Latency p95 hiện 295.16 ms), avg = **226 ms** (`rate(_sum)/rate(_count)`). Panel p50/p95/p99 cho thấy p99 ≈ **1.7–1.8 s**.
   - p95 chỉ cao hơn avg ~70 ms, nghĩa là 95% request khá đều.
   - Khoảng cách lớn nằm giữa p95 và p99. Đó là ~3% request bị mock cộng thêm 1.5 s (`MOCK_SLOW_RATE=0.03`). avg hầu như không thấy nhóm này, chỉ percentile cao mới lộ ra.
2. **Requests/s theo instance:** `127.0.0.1:8102` 9.42 req/s, `127.0.0.1:8101` 9.16 req/s (tổng 18.6). Nginx round-robin chia **gần đều**, chênh ~3%.
   - Ở benchmark 3 instance, số request đếm bằng `increase([2m])` là 2383 / 2386 / 2401.
3. **Vì sao input tokens/s > output tokens/s:** input **1430 token/s** so với output **747 token/s** (~1.9:1). Prompt = system prompt + tài liệu retrieve được (RAG context) + câu hỏi, còn câu trả lời chỉ là một đoạn ngắn.
   - Bằng chứng: ở thí nghiệm E (v1, không retrieval) input giảm còn 681 token/s.
4. **`curl http://localhost:8080/metrics`** trả **404**, vì Nginx có `location = /metrics { return 404; }`. `/metrics` lộ thông tin nội bộ (traffic, cost, version, model) nên không public. Prometheus scrape thẳng từng instance trong mạng nội bộ, không đi qua Nginx.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Traffic 18.6 req/s, Error rate 0.39%, p95 295 ms, 2 instance up, req/s theo instance, tokens/s, cost burn rate.
- [x] Trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png).
- [x] Trace lỗi trong Jaeger, có event `exception`: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png).
- [x] Trang Alerts của Prometheus, **2 alert cùng FIRING** (`AgentHighErrorRate` và `LLMCostBurnRateHigh`): [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png).

### 4.2 Thí nghiệm "phá hệ thống"

Mỗi thí nghiệm khởi động lại 2 agent với cấu hình mới (giữ tải 30 user). `t0` là lúc cả 2 agent trả `/health` 200. Thời gian đo bằng script poll `GET /api/v1/alerts` mỗi 2 giây (log đầy đủ trong [`reports/alert_experiments.txt`](reports/alert_experiments.txt)).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (giá trị 19.2%) | pending **+14 s**, firing **+74 s** | Error rate, Requests/s theo status code (503) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (p95 = 1.91 s) | pending **+6 s**, firing **+127 s** | Latency p50/p95/p99: p95 1.91 s, p99 1.98 s nhưng avg chỉ 0.62 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (cost 5m = $15.96/giờ) | pending **+8 s**, firing **+127 s** | Cost burn rate: cửa sổ 1m là **$34.19/giờ** (gấp ~15 lần mức $2.2–2.4/giờ của baseline). LLM p95 theo model 497 ms (gấp ~2.5 lần), `/ask` p95 722 ms < 1 s nên không có alert latency |
| D | Treo 1 instance (`psutil.suspend`, thay cho `docker pause`) | `AgentInstanceDown` (`up{instance="127.0.0.1:8102"} = 0`) | pending **+12 s**, firing **+40 s** | Instances up 2 → 1. Error rate của agent vẫn **0.51%** (xanh) trong khi Nginx trả **30 lỗi 504** |
| E | `AGENT_VERSION=v1` | **Không có alert mới** trong 240 s | — | Retrieval hit ratio: không còn dữ liệu. Tokens/s input 1430 → 681. Bảng version đổi sang `v1`. p95 230 ms, error 0.58%, cost $1.16/giờ đều "đẹp" hơn |

**Thời gian tới lúc firing** ≈ thời gian để biểu thức vượt ngưỡng (vài giây, do `scrape_interval`/`evaluation_interval` = 5 s và `rate([1m])` phản ứng nhanh khi lỗi tăng mạnh) **cộng với `for`**: A 60 s, B và C 120 s, D 30 s. Số đo khớp: 14 + 60 ≈ 74 s, 6 + 120 ≈ 127 s, 8 + 120 ≈ 127 s, 12 + 30 ≈ 40 s.

**Phát hiện thêm:** ngay ở cấu hình mặc định, 30 user (~18.6 req/s) đã làm `LLMCostBurnRateHigh` **firing** ($2.38/giờ > $2, activeAt 20:07:17, khoảng 4 phút sau khi bật tải). Ngưỡng $2/giờ được đặt cho stack Docker giới hạn `cpus: 0.5`, nơi throughput thấp hơn. Trên máy không giới hạn CPU, cùng 30 user sinh nhiều request hơn nên chạm ngưỡng. Ngưỡng cost nên đặt theo budget thực tế và theo tải dự kiến, không phải con số cố định. Vì vậy ở ảnh `prometheus-alert-firing.png`, `LLMCostBurnRateHigh` đã firing 10 phút trước khi thí nghiệm A làm `AgentHighErrorRate` firing.

**Thí nghiệm D: vì sao 504 không hiện trên dashboard?** Request bị treo ở instance đã suspend. Nginx chờ hết `proxy_read_timeout 10s` rồi trả 504 cho client. Agent chưa bao giờ xử lý xong request đó nên không đếm vào `http_requests_total`, và error rate của agent vẫn xanh. Metric chỉ phản ánh những gì chính service đó nhìn thấy. Production cần thêm metric ở tầng load balancer (Nginx/ingress) và synthetic check từ bên ngoài. `AgentInstanceDown` (`up == 0`) là tín hiệu duy nhất bắt được sự cố này.

**Thí nghiệm E không làm alert nào firing. Phát hiện loại sự cố này trong production bằng cách nào?** v1 trả lời *nhanh hơn, rẻ hơn, không lỗi*, nên mọi Golden Signal đều xanh, trong khi chất lượng giảm từ 91% xuống 4% (mục 3). Metric hạ tầng không đo được "đúng hay sai". Cần giám sát chất lượng:

1. **Quality gate trước khi deploy:** eval golden dataset trong CI, fail thì không deploy (đã làm ở mục 3, [`.github/workflows/eval.yml`](.github/workflows/eval.yml)).
2. **Online eval sau khi deploy:** định kỳ chạy một tập canary nhỏ vào production, export Gauge `agent_eval_accuracy` và đặt alert khi < 85%.
3. **Proxy metric chất lượng:** retrieval hit ratio. Ở thí nghiệm E panel này mất dữ liệu ngay, nên có thể đặt alert `absent()` hoặc khi hit ratio < 80%. Thêm feedback 👍/👎 và LLM-as-judge trên mẫu ngẫu nhiên.
4. **Alert khi version hoặc model đổi** (`changes(agent_info[10m]) > 0`) để người trực kiểm tra.

### 4.3 Debug bằng trace

Trace chậm nhất trong 30 phút (Jaeger API v3, lọc `duration_min=1s`, 268 trace > 1 s):

- `trace_id`: **`f4e07cda78d3081fd7d9667b5ecede7d`** (instance `agent-1`)
- Tổng thời gian: **1748.0 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` 1687.5 ms (96.5%)**, `gen_ai.usage.input_tokens=81`, `output_tokens=46`. Các span còn lại: `agent.retrieve_context` 23.3 ms (1.3%), `agent.run` 1745.9 ms (span cha).
- Kết luận: chậm là do LLM upstream (mock cộng thêm 1.5 s), không phải code agent hay retrieval.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-25T13:03:07.211805+00:00", "level": "INFO", "service": "observable-agent", "instance": "agent-1", "event": "llm_call", "trace_id": "f4e07cda78d3081fd7d9667b5ecede7d", "span_id": "372f60d9d5c73601", "request_id": "76c691de4b06", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 81, "output_tokens": 46, "cost_usd": 4e-05, "llm_ms": 1675.6}
{"ts": "2026-11-25T13:03:07.212805+00:00", "level": "INFO", "service": "observable-agent", "instance": "agent-1", "event": "http_request", "trace_id": "f4e07cda78d3081fd7d9667b5ecede7d", "span_id": "372f60d9d5c73601", "request_id": "76c691de4b06", "method": "POST", "route": "/ask", "status": 200, "ms": 1747.6}
```

Trace lỗi: **`03de052c5fa611e25db894aeac17d96a`** (112.3 ms, HTTP 503). Span `chat mock-llm-small` (80.8 ms) có `error.type=upstream_unavailable` và event `exception` (`exception.type=utils.mock_llm.LLMError`, `exception.message=Upstream LLM error: 503 Service Unavailable (mock)`). Log cùng `trace_id`:

```json
{"ts": "2026-11-25T13:12:05.614716+00:00", "level": "INFO", "service": "observable-agent", "instance": "agent-1", "event": "llm_error", "trace_id": "03de052c5fa611e25db894aeac17d96a", "span_id": "17ed535541c0a91d", "request_id": "634adb81c766", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

Đầy đủ trong [`reports/traces_and_logs.txt`](reports/traces_and_logs.txt).

---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

Số liệu dòng `POST /ask` trong [`benchmark-results/scale_1_stats.csv`](benchmark-results/scale_1_stats.csv) và [`scale_3_stats.csv`](benchmark-results/scale_3_stats.csv):

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.75 | 190 | 240 | 1700 | 72 / 7158 (1.01%) |
| 3 | 59.62 | 200 | 240 | 1700 | 58 / 7144 (0.81%) |

(Aggregated, gồm cả `/health`: 65.99 và 65.83 req/s. Lỗi đều là 503 ngẫu nhiên của mock upstream.)

**Giải thích kết quả:** scale từ 1 lên 3 instance **không cải thiện** RPS hay p95. Đây là kết quả đúng với cách đo:

- **Tải là vòng kín (closed-loop):** 100 user, mỗi user chờ phản hồi rồi nghỉ `between(0.5, 2)` s (trung bình 1.25 s). Lưu lượng tối đa ≈ 100 / (1.25 s + ~0.24 s latency) ≈ **67 req/s**, khớp với 66 req/s đo được. RPS bị giới hạn bởi *số user và think time*, không phải bởi server.
- **1 instance chưa bão hoà:** mock LLM là `asyncio.sleep`, một event loop phục vụ được hàng chục request đồng thời. Stack native không giới hạn `cpus: 0.5` như compose nên 1 process đủ sức (p50 190 ms gần bằng latency LLM thuần ~120–180 ms). Thêm instance không có gì để cải thiện.
- **Nút thắt là latency của LLM upstream:** p50 ≈ latency mock LLM. p99 = 1.7 s do 3% request bị chậm thêm 1.5 s ở bước `chat mock-llm-small` (mục 4.3). Scale ngang không rút ngắn được thời gian chờ LLM bên ngoài.
- Muốn thấy scale có tác dụng thì phải tạo tải vượt khả năng 1 instance (nhiều user hơn hoặc `wait_time` ngắn hơn) hoặc giới hạn CPU mỗi instance như compose (`cpus: 0.5`). Đó là lý do bản Docker dễ thấy khác biệt hơn.

Tóm lại: scale ngang giúp tăng throughput tối đa và chịu được khi một instance chết (thí nghiệm D), nhưng không làm từng request nhanh hơn khi nút thắt là LLM.

---

## 6. Kết luận

- 3 điều học được về benchmark và observability:
  1. **Average nói dối.** Ở `MOCK_SLOW_RATE=0.03` avg chỉ tăng 36 ms nhưng p99 tăng gấp 3.6 lần (481 → 1717 ms). SLO phải đặt theo percentile và đo bằng Histogram.
  2. **Nhanh và rẻ chưa chắc là tốt.** v1 rẻ hơn mỗi request, nhanh hơn, không lỗi, nhưng chỉ đúng 4.3%. Metric hạ tầng không thấy điều này. Cần eval golden dataset làm quality gate (exit code chặn merge) và chỉ số "cost / câu đúng".
  3. **Throughput có trần.** Một process chỉ chịu được tới một mức concurrency nhất định. Vượt "điểm gối" (c ≈ 50 trên máy này) thì RPS còn giảm, latency tăng vọt. Tăng tải tiếp không giúp gì, phải scale ngang, và muốn scale ngang thì app phải stateless (Part 5, 6).
- Nếu đưa agent lên production thật, tôi sẽ thêm:
  - Gauge `agent_eval_accuracy` từ online eval định kỳ, alert khi < 85%.
  - Alert retrieval miss rate (`rate(agent_retrieval_total{result="miss"}[5m]) / rate(agent_retrieval_total[5m]) > 0.2`).
  - Alert budget theo ngày (% `DAILY_BUDGET_USD` đã dùng > 80%) và đếm số response 402/429.
  - Alert saturation (`agent_in_flight_requests` cao kéo dài).
  - Theo dõi error budget theo cửa sổ 30 ngày (multi-window burn rate).
  - SLO riêng cho Time-To-First-Token khi chuyển sang streaming.
