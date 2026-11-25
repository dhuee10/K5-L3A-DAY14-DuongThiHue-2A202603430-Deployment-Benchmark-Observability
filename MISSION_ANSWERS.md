# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11, Python 3.12 (venv), Redis 5.0.14 bản Windows (tporadowski/redis) chạy local ở `127.0.0.1:6379`.
> Máy **chưa có Docker và WSL**, nên các mục cần Docker / SIGTERM được đánh dấu **⏳ CHỜ DOCKER** bên dưới
> và sẽ bổ sung output thật sau khi chạy trên máy có Docker (không điền số ước lượng).
> Mọi output dưới đây là output thật của lệnh đã chạy.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Hardcode secret** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key và mật khẩu DB, và key vẫn nằm trong lịch sử git kể cả khi xoá file sau đó.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Ai đọc được log cũng có key. Khi chạy thử, log in ra đúng `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`.
3. **Dùng `print()` thay cho logging có cấu trúc** (dòng 33, 38): không có level, timestamp hay request_id. Log aggregator không parse hay lọc được, và log còn chứa nguyên nội dung câu hỏi lẫn câu trả lời của user.
4. **Không có health check** (dòng 42): `GET /health` trả **404**. Platform không biết agent còn sống không để restart, load balancer cũng không biết có nên route traffic vào hay không.
5. **Port cố định và bind `localhost`** (dòng 51–52): `host="localhost"` khiến app không nhận kết nối từ ngoài container. `port=8000` cứng nên không chạy được trên Railway/Render, vì các nền tảng này cấp `PORT` qua biến môi trường.
6. **`reload=True` và `DEBUG = True` cứng** (dòng 21, 53): bật reload/debug cả khi chạy production, vừa tốn tài nguyên (watch file) vừa lộ thông tin.
7. **Không có config management** (dòng 20–22): `DEBUG`, `MAX_TOKENS` nằm cứng trong code. Muốn đổi giữa dev/staging/prod là phải sửa code và build lại.
8. **Không xử lý shutdown**: không có lifespan hay xử lý SIGTERM. Platform tắt container là request đang chạy bị cắt ngang.
9. **Không validate input**: `question: str` nằm ở query string, không giới hạn độ dài. Gửi JSON body thì bị 422.

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Log server (lộ secret):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [27412] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
```

Bản develop chạy được nhưng chưa production-ready: chỉ nghe trên localhost, không có `/health`, và ghi secret ra log.

Bản production:

```text
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":0.4,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:41:56.041500+00:00"}
$ curl http://localhost:8000/ready
{"ready":true}
```

### Exercise 1.3: So sánh develop và production

| Feature | Develop | Production | Tại sao quan trọng? |
|---------|---------|------------|---------------------|
| Config | Hardcode trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | `config.py` đọc env (`os.getenv`) qua dataclass `Settings`, có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Một image chạy được ở dev/staging/prod, chỉ đổi biến môi trường. Không phải sửa code để đổi cấu hình |
| Secrets | `sk-...` và mật khẩu DB nằm trong code, còn bị in ra log | Đọc từ env, `.env` nằm trong `.gitignore`, chỉ commit `.env.example` | Secret trong git coi như đã lộ, không xoá được khỏi lịch sử commit |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env | Container và cloud cần bind `0.0.0.0`, còn PORT do platform cấp |
| Health check | Không có (`/health` → 404) | `/health` (liveness, có uptime/version) và `/ready` (readiness, 503 khi chưa sẵn sàng) | Platform restart khi liveness fail. Load balancer chỉ route traffic khi ready |
| Logging | `print()`, log cả secret | Log dạng JSON, chỉ ghi `question_length` và `client_ip`, không ghi secret | Log aggregator (Loki, Datadog) parse và lọc được. Không lộ dữ liệu nhạy cảm |
| Shutdown | Đột ngột, không có hook | `lifespan` (startup/shutdown), đặt `is_ready = False` khi tắt, có handler SIGTERM | Request đang chạy được làm xong, connection được đóng sạch, không mất dữ liệu |
| Reload / Debug | `reload=True` luôn bật | `reload=settings.debug`, chỉ bật khi `DEBUG=true` | Production không watch file, không lộ debug |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt |

Một quan sát khi chạy: `logging.basicConfig` trong bản production không có tác dụng khi chạy bằng `python app.py`, vì lúc đó uvicorn đã cấu hình logging trước. Dòng log JSON `agent_request` vì vậy không hiện ra, chỉ còn access log của uvicorn. Muốn chắc chắn có log JSON thì phải gắn handler riêng cho logger của app. Tôi đã làm theo cách này ở Part 6 (`JsonFormatter` gắn vào logger `agent`, `propagate=False`).

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Debian đầy đủ có Python 3.11, gcc và nhiều tool build (~1 GB).
2. **Working directory:** `/app` (`WORKDIR /app`). Các lệnh `COPY`, `RUN`, `CMD` sau đó đều chạy tương đối với thư mục này.
3. **Vì sao `COPY requirements.txt` trước `COPY` code:** Docker cache theo layer. Layer `pip install` chỉ build lại khi `requirements.txt` đổi. Sửa code chỉ làm mất cache từ bước `COPY app.py` trở đi, nên build lại mất vài giây thay vì cài lại toàn bộ dependencies. Ví dụ Go trong `02-docker/layer-order/` (`Dockerfile.deps-first` và `Dockerfile.source-first`) minh hoạ đúng điều này.
4. **CMD và ENTRYPOINT:** `CMD` là lệnh hoặc tham số mặc định, bị ghi đè hoàn toàn khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình cố định của container, tham số của `docker run` được nối thêm vào sau nó (chỉ đổi được bằng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` cùng `CMD ["app:app", "--port", "8000"]`. Dạng exec (mảng JSON) giúp process chính là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build và run bản develop

⏳ **CHỜ DOCKER.** Lệnh sẽ chạy (từ gốc repo):

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run -p 8000:8000 my-agent:develop
curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker"
docker images my-agent:develop
```

### Exercise 2.3: Multi-stage build (`02-docker/production/Dockerfile`)

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc` và `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này có compiler và apt cache nhưng **không được deploy**.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (site-packages đã cài xong) cùng `main.py` và `utils/`. Có `HEALTHCHECK`, chạy bằng `USER appuser`, `CMD uvicorn ... --workers 2`.
- **Vì sao image nhỏ hơn:**
  - Base `slim` thay cho bản full (bỏ bớt ~800 MB tool và thư viện hệ thống).
  - gcc, header và apt cache chỉ nằm ở stage builder, image cuối không chứa.
  - `--no-cache-dir` nên không giữ cache pip.
  - Ít package hơn thì bề mặt tấn công cũng nhỏ hơn.

Kích thước đo bằng `docker images`: ⏳ **CHỜ DOCKER.** Sẽ điền số MB thật của `my-agent:develop` và `my-agent:advanced` cùng % chênh lệch.

| Image | Size (MB) |
|---|---|
| `my-agent:develop` | ⏳ |
| `my-agent:advanced` | ⏳ |
| Chênh lệch | ⏳ % |

### Exercise 2.4: Docker Compose stack (`02-docker/production/docker-compose.yml`)

Các service: `agent` (build từ Dockerfile, target `runtime`), `redis` (`redis:7-alpine`), `qdrant` (`qdrant/qdrant:v1.9.0`), `nginx` (`nginx:alpine`). Tất cả chung network bridge `internal`. Chỉ Nginx publish cổng ra host (80/443). Agent không expose port, chỉ đi qua Nginx.

```text
                 ┌──────────────────────── network "internal" (bridge) ─────────────────────┐
 Client ──HTTP──►│ nginx :80  ──proxy_pass──►  agent :8000  ──redis://redis:6379──►  redis   │
 (host :80/443)  │ (rate limit 10r/s,            (uvicorn, 2 workers,      (session, rate   │
                 │  security headers)             non-root)                 limit, LRU 256MB)│
                 │                                   │                                       │
                 │                                   └──http://qdrant:6333──►  qdrant        │
                 │                                                         (vector DB/RAG)  │
                 └──────────────────────────────────────────────────────────────────────────┘
 depends_on: agent chờ redis + qdrant "service_healthy"; nginx chờ agent.
 Volumes: redis_data, qdrant_data (dữ liệu bền qua restart).
```

Các service gọi nhau bằng **tên service** qua DNS nội bộ của Docker (`redis`, `qdrant`, `agent`). Thứ tự khởi động do `depends_on` kèm healthcheck quyết định.

Kết quả test qua Nginx (`curl http://localhost/health`, `curl -X POST http://localhost/ask ...`): ⏳ **CHỜ DOCKER.**

---

## Part 3: Cloud Deployment

> Lớp K5 deploy chính agent Part 6 (`06-lab-complete/`). Xem thêm [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.1: Railway (nền tảng chính)

- URL: ⏳ **CHỜ DEPLOY** (cần đăng nhập Railway: `railway login`)
- Screenshot: [screenshots/dashboard.png](screenshots/dashboard.png), [screenshots/running.png](screenshots/running.png), [screenshots/test.png](screenshots/test.png) ⏳

### Exercise 3.2: So sánh `railway.toml` và `render.yaml` (`06-lab-complete/`)

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|---|---|---|---|
| Định dạng, phạm vi | TOML, cấu hình **1 service** (build + deploy) | YAML Blueprint, khai báo **nhiều service** (web agent và Key Value/Redis) trong một file | Railway coi mỗi service là một khối riêng, tạo Redis bằng dashboard/CLI. Render Blueprint mô tả cả hạ tầng (IaC) |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `dockerfilePath`, `rootDir: 06-lab-complete` | Render đọc Blueprint ở gốc repo nên phải chỉ thư mục con. Railway chạy `railway up` ngay trong `06-lab-complete/` |
| Health check | `[deploy] healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` | Cùng ý nghĩa: platform chỉ chuyển traffic sang bản deploy mới khi `/health` trả 200 |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Render tự restart, không cấu hình trong file | Railway cho chỉnh chính sách restart qua config |
| Secret | Không nằm trong file. Đặt bằng `railway variables --set AGENT_API_KEY=...` | `AGENT_API_KEY: generateValue: true` (Render tự sinh), hoặc `sync: false` (nhập tay trên dashboard) | Cả hai đều không ghi giá trị secret vào git. Render có thêm cơ chế tự sinh key |
| Redis | `REDIS_URL = ${{Redis.REDIS_URL}}` (biến tham chiếu tới service Redis trên Railway) | `fromService: {type: keyvalue, name: agent-redis, property: connectionString}` | Render nối Redis bằng khai báo trong file, Railway nối bằng biến tham chiếu |
| Port | Railway inject `PORT`, CMD dùng `${PORT:-8000}` | Render inject `PORT` (mặc định 10000), CMD dùng cùng cơ chế | Vì vậy CMD trong Dockerfile phải đọc `PORT`, không cố định 8000 |
| Plan, region | Không cấu hình trong file | `plan: free`, `region: singapore` | Render cho khai báo plan và region trong Blueprint |

### Exercise 3.3: Pipeline CI/CD Cloud Run (`03-cloud-deployment/production-cloud-run/`)

1. **Trigger:** push lên `main` kích hoạt Cloud Build chạy `cloudbuild.yaml` (máy `E2_MEDIUM`, timeout 20 phút).
2. **`test`:** container `python:3.11-slim` cài requirements và pytest, chạy `pytest tests/`. Test fail thì pipeline dừng ở đây.
3. **`build`, `push`:** `docker build` image gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tái dùng layer, rồi push lên Container Registry (`gcr.io/$PROJECT_ID/ai-agent`).
4. **`deploy`:** `gcloud run deploy` image theo đúng `COMMIT_SHA` lên region `asia-southeast1`. Cấu hình 1–10 instance (min 1 để tránh cold start), 512Mi RAM, 1 CPU, timeout 60s, `ENVIRONMENT=production`. Secret `OPENAI_API_KEY` lấy từ Secret Manager (`--set-secrets`), không nằm trong code.
5. **`service.yaml`:** mô tả cùng service đó dưới dạng IaC (Knative). Có autoscaling 1–10, `containerConcurrency: 80`, resource requests/limits, secret qua `secretKeyRef`, `livenessProbe` trên `/health` và `startupProbe` trên `/ready`. Áp dụng bằng `gcloud run services replace`.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop/app.py`)

- **Key được kiểm tra ở đâu:** dependency `verify_api_key` (dòng 39–54) dùng `APIKeyHeader(name="X-API-Key")`, gắn vào `/ask` bằng `Depends(verify_api_key)`. Key đúng lấy từ env `AGENT_API_KEY`.
- **Sai key thì sao:** thiếu header → **401** `Missing API key`. Có header nhưng sai → **403** `Invalid API key.`. Ở Part 6 tôi trả **401** cho cả hai trường hợp theo đúng yêu cầu bài, và so sánh bằng `hmac.compare_digest` để tránh timing attack.
- **Rotate key:** đổi biến `AGENT_API_KEY` trên platform rồi restart hoặc redeploy, không cần sửa code. Muốn không có downtime thì cho phép tạm thời 2 key (cũ và mới, ví dụ `AGENT_API_KEYS=old,new`), chuyển client sang key mới rồi bỏ key cũ.

Chạy với `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT (`04-api-gateway/production/`)

Luồng JWT (`auth.py`):
1. `POST /auth/token` với username/password. `authenticate_user` kiểm tra trong `DEMO_USERS`.
2. `create_token` ký payload `{sub, role, iat, exp=+60 phút}` bằng HS256 với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>`. `verify_token` decode và verify chữ ký cùng hạn dùng mà không cần tra DB (stateless). Token hết hạn → 401, token sai chữ ký → 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY

{"access_token":"<TOKEN_DA_CHE>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ...<che>"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'      # không có token
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer

{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK

{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic.","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Thuật toán:** sliding window log. Mỗi user có một `deque` timestamp, bỏ các timestamp cũ hơn 60s, đủ `max_requests` thì trả 429 kèm `Retry-After`.
- **Giới hạn:** user 10 req/phút (`rate_limiter_user`), admin 100 req/phút (`rate_limiter_admin`).
- **Bypass cho admin:** `app.py` chọn limiter theo `role` trong JWT. Token của `teacher` (role `admin`) dùng limiter 100 req/phút. Muốn bỏ hẳn giới hạn thì bỏ qua `limiter.check()` khi `role == "admin"`.
- **Hạn chế:** state nằm trong RAM của process, nên mỗi worker hay instance đếm riêng và restart là mất. Vì vậy Part 6 chuyển sang Redis sorted set.

Vòng lặp với token của `student` (request 1 là lệnh "Explain JWT" ở trên, vòng lặp gửi tiếp request 2–20):

```text
$ for i in {2..20}; do curl -s -o /dev/null -w "Request $i -> HTTP %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\": \"Test $i\"}"; done
Request 2 -> HTTP 200
Request 3 -> HTTP 200
Request 4 -> HTTP 200
Request 5 -> HTTP 200
Request 6 -> HTTP 200
Request 7 -> HTTP 200
Request 8 -> HTTP 200
Request 9 -> HTTP 200
Request 10 -> HTTP 200
Request 11 -> HTTP 429
Request 12 -> HTTP 429
...
Request 20 -> HTTP 429

$ curl -i ... (request tiếp theo sau khi đã vượt limit)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795596218
retry-after: 58

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":58}}

$ curl -s http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}
```

Đúng 10 request đầu được 200, từ request 11 trở đi là 429.

### Exercise 4.4: Cost guard

Bản mẫu (`04-api-gateway/production/cost_guard.py`) lưu usage trong dict RAM, budget $1/ngày mỗi user và $10/ngày toàn hệ thống. Cách làm của tôi ở Part 6 (`06-lab-complete/app/cost_guard.py`) dùng Redis:

1. **Tính tiền theo token:** `cost = input_tokens/1000 × $0.00015 + output_tokens/1000 × $0.0006` (giá gpt-4o-mini). Mock LLM không trả `usage` nên tôi ước lượng ~4 ký tự/token. Input tính cả history đưa vào prompt.
2. **`check_budget()` chạy TRƯỚC khi gọi LLM:** `MGET cost:daily:<YYYY-MM-DD> cost:monthly:<YYYY-MM>` (UTC). Đã tiêu `>= DAILY_BUDGET_USD` hoặc `>= MONTHLY_BUDGET_USD` thì trả **402 Payment Required** và không gọi LLM nữa. Redis lỗi thì trả 503 (fail closed, không cho tiêu tiền khi không đếm được).
3. **`record_cost()` chạy SAU khi gọi LLM:** dùng `INCRBYFLOAT` cho cả key ngày lẫn key tháng trong một transaction `MULTI/EXEC`, nên an toàn khi nhiều worker hoặc instance ghi cùng lúc. Kèm `EXPIRE` (2 ngày và 32 ngày), nên budget tự reset theo ngày và tháng mà không cần cron.
4. **Vì sao lưu ở Redis:** mọi instance dùng chung một tổng chi phí, restart không mất. Nếu để trong RAM thì 3 instance sẽ cho tiêu gấp 3 lần budget.
5. **Vì sao cần cả rate limit lẫn cost guard:** rate limit chặn *tốc độ* (chống spam hay DoS mỗi key). Cost guard chặn *tổng tiền*: kể cả khi mỗi client đều tuân thủ rate limit, nhiều client hoặc prompt rất dài vẫn có thể đốt hết budget.

Kiểm chứng bằng script chấm: với `DAILY_BUDGET_USD=0.000001` thì agent trả `402 ở request thứ 1` (xem output Part 6).

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

- `/health` (liveness) trả 200 kèm uptime, version, kiểm tra RAM bằng psutil (`status: degraded` khi RAM > 90%). Liveness **không** nên kiểm tra dependency bên ngoài: Redis chết mà restart agent cũng không chữa được.
- `/ready` (readiness) trả 503 khi `_is_ready = False` (đang khởi động hoặc đang shutdown). Load balancer dựa vào đây để ngừng gửi traffic, nhưng không restart.

```text
$ curl http://localhost:8000/health
{"status":"degraded","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:42:59.424338+00:00","checks":{"memory":{"status":"degraded","used_percent":91.6}}}
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

(`degraded` vì RAM máy lúc chạy đang dùng 91.6%, vượt ngưỡng 90%. HTTP vẫn 200 nên platform không restart.)

### Exercise 5.2: Graceful shutdown

Cơ chế trong `develop/app.py`: middleware đếm `_in_flight_requests`. Phần shutdown của `lifespan` đặt `_is_ready = False` (để `/ready` trả 503 và LB ngừng route traffic), rồi chờ tối đa 30s cho tới khi `_in_flight_requests == 0`. `uvicorn.run(..., timeout_graceful_shutdown=30)`. Handler `signal.signal(SIGTERM, ...)` chỉ ghi log.

> ⚠️ Handler này **thay thế** handler SIGTERM của uvicorn khi nó được đăng ký sau uvicorn, khiến process có thể không tự tắt. Ở Part 6 tôi **không** tự đăng ký signal handler. Uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong, chạy phần shutdown của `lifespan` rồi exit 0. CMD trong Dockerfile dùng `exec uvicorn ...` để uvicorn là PID 1 và nhận được signal.

Test `kill -TERM $PID` khi đang có request: ⏳ **CHỜ DOCKER / LINUX**. Windows không có SIGTERM: `kill` trong Git Bash gọi `TerminateProcess`, tức tắt ngay lập tức, nên không chạy được thí nghiệm này. Bài test tương ứng của script chấm (tiêu chí 11) cũng báo ⚠️ trên Windows.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

History hội thoại lưu ở Redis (`session:<id>`, `SETEX` TTL 1 giờ, tối đa 20 message), không nằm trong biến toàn cục. Bất kỳ instance nào cũng đọc được session của user. Nếu để trong RAM: request 1 vào instance A, request 2 vào instance B thì B không thấy history. Restart hay scale down cũng làm mất state. Part 6 áp dụng cùng nguyên tắc: rate limit (sorted set), cost (counter) và history (list) đều nằm trong Redis.

### Exercise 5.4 & 5.5: Load balancing và test stateless

⏳ **CHỜ DOCKER** cho lệnh chính thức:
`docker compose -p k5-day14-scaling up -d --build --scale agent=3` → `python test_stateless.py` → `docker compose -p k5-day14-scaling down`.

**Chạy thay thế khi chưa có Docker** (cùng code `05-scaling-reliability/production/app.py`, không sửa gì): 3 process uvicorn `INSTANCE_ID=agent-1..3` ở cổng 8081–8083, Redis local, cùng một proxy round-robin nhỏ viết bằng Python nghe ở **8080** đóng vai Nginx. Đây là mô phỏng, không phải stack Nginx thật.

**A) Có Redis.** `python test_stateless.py`:

```text
Session ID: 059c2a64-4630-43b6-9e25-4042d211dbd1

Request 1: [agent-2]   Q: What is Docker?
Request 2: [agent-3]   Q: Why do we need containers?
Request 3: [agent-1]   Q: What is Kubernetes?
Request 4: [agent-2]   Q: How does load balancing work?
Request 5: [agent-3]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'agent-3', 'agent-1', 'agent-2'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ... (đủ 5 cặp user/assistant)
✅ Session history preserved across all instances via Redis!
```

Sau đó tắt hẳn `agent-2` rồi gọi tiếp 4 request: tất cả đều 200, do `agent-3` và `agent-1` phục vụ (proxy bỏ qua instance chết, giống `proxy_next_upstream` của Nginx). State vẫn nằm trong Redis.

**B) Đối chứng, không có Redis** (`REDIS_URL` trỏ tới cổng không tồn tại, app tự rơi về in-memory store):

```text
Instances used: {'agent-2', 'agent-3', 'agent-1'}
--- Conversation History ---
Total messages: 2
  [user]: What is Kubernetes?...
✅ Session history preserved across all instances via Redis!
```

**Giải thích:**
- Có Redis: 5 request rải đều qua 3 instance, history vẫn đủ **10 message**. Đây là kết quả của thiết kế stateless.
- Không có Redis: mỗi instance chỉ nhớ phần hội thoại nó tự phục vụ. Request đọc history rơi vào instance chỉ biết 1 lượt nên chỉ thấy **2 message**, mất 8.
- Script vẫn in "✅ preserved via Redis" vì dòng này in cố định, không kiểm tra `count`. Vì vậy phải đọc số message thật chứ không tin vào dòng ✅.

---

## Part 6: Final Project — `06-lab-complete/`

Tóm tắt cách làm:

| Tiêu chí | Cách làm |
|---|---|
| Config | `app/config.py` dùng dataclass, mọi field đọc env qua `default_factory` (PORT, ENVIRONMENT, LOG_LEVEL, AGENT_API_KEY, REDIS_URL, RATE_LIMIT_PER_MINUTE, DAILY/MONTHLY_BUDGET_USD, HISTORY_MAX_MESSAGES). `AGENT_API_KEY` mặc định rỗng, khi rỗng thì từ chối tất cả request |
| Auth | `APIKeyHeader("X-API-Key")` và `hmac.compare_digest`. Thiếu hoặc sai key → 401 |
| Rate limit | Redis sorted set theo hash của API key: `ZREMRANGEBYSCORE → ZCARD → ZADD → EXPIRE` trong `MULTI/EXEC`. Vượt giới hạn → 429 kèm `Retry-After`. Redis lỗi → 503 |
| Cost guard | Counter `INCRBYFLOAT` theo ngày và tháng trong Redis. Vượt budget → 402 |
| Health / Ready | `/health` luôn 200. `/ready` ping Redis (timeout 2s), lỗi → 503 |
| Log | `JsonFormatter` (`json.dumps`), mỗi dòng 1 JSON gồm `ts, lvl, event, request_id, path, status, ms`, không ghi API key |
| Lỗi | Handler riêng cho `HTTPException`, `RequestValidationError` (422 kèm danh sách field sai) và `Exception` (500 không có stack trace) |
| Shutdown | `lifespan` để log và đóng Redis pool. Không tự đăng ký signal handler, để uvicorn xử lý SIGTERM. Dockerfile dùng `exec uvicorn` (PID 1) và `--timeout-graceful-shutdown 8` (< 10s của `docker stop`) |
| Stateless | Rate limit, cost và history (`history:<user_id>`, giữ 20 message, TTL 24h) đều trong Redis |
| Docker | Multi-stage `python:3.11-slim` (venv `/opt/venv` copy sang runtime), user `app` (uid 1001), `HEALTHCHECK` bằng python urllib theo `PORT`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000}"` |
| Compose | `agent` (build, `env_file .env required: false`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis healthy`) và `redis:7-alpine` (healthcheck, volume) |
| Deploy | `railway.toml` (`DOCKERFILE`, `/health`) và `render.yaml` (web docker + keyvalue, `AGENT_API_KEY generateValue`) |

Kiểm tra thủ công phần xử lý lỗi:

```text
$ curl -i -X POST localhost:8010/ask -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d '{"invalid":"data"}'
HTTP/1.1 422 Unprocessable Entity
{"error":"invalid_request","status":422,"details":[{"field":"question","message":"Field required"}]}
$ curl -X POST ... -d 'not json'
{"error":"invalid_request","status":422,"details":[{"field":"0","message":"JSON decode error"}]}
$ curl -X POST ... -d '{"user_id":"hue","question":"What is deployment?"}'
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","user_id":"hue","history_length":2,"usage":{"input_tokens":5,"output_tokens":19,"cost_usd":1.215e-05}}
```

### Output `python check_production_ready.py` (Windows, chưa có Docker, Redis local)

Lệnh: `python check_production_ready.py --redis-url redis://127.0.0.1:6379/0` (bản đầy đủ, không `--skip-docker`).

```text
================================================================
  📋 Checklist Deliverable
     ⚠️  Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ⚠️  Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 10/12 tiêu chí đạt · 38/38 checks passed
  ⚠️  Chưa kiểm tra được: Dockerfile (multi-stage, < 500 MB), Graceful shutdown
  ❌ CHƯA ĐẠT — còn tiêu chí chưa kiểm tra được (⚠️). Chạy đầy đủ trên máy có Docker.
================================================================
```

- **38/38 check đã chạy đều đạt, không có ❌.** Gồm rate limit `429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)`, stateless `restart agent vẫn bị 429`, cost guard `402 ở request thứ 1`, readiness `Redis mất kết nối → /ready 503`, log `17 dòng JSON`.
- 2 tiêu chí ⚠️ chỉ vì môi trường: (1) máy không có lệnh `docker` nên không build và đo được image, (2) Windows không gửi được SIGTERM.
- ⏳ Sẽ chạy lại trên máy có Docker hoặc Linux và thay khối trên bằng output `PASS 12/12`.

---

## AI log

| # | Hỏi AI (Claude Code) | Nhận được | Tôi tự sửa và kiểm tra thế nào |
|---|---|---|---|
| 1 | Đọc đề (README, CHECKPOINTS, RUBRIC, CODE_LAB, script chấm) và liệt kê phần nào làm được khi không có Docker/WSL | Bảng phần làm được / không làm được và gợi ý dùng GitHub Codespaces | Đối chiếu với RUBRIC: ⚠️ tính là chưa đạt, Lab Coach chấm trên máy có Docker |
| 2 | Viết các TODO Part 6 (`config`, `auth`, `redis_client`, `rate_limiter`, `cost_guard`, `main`, Dockerfile, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml`) | Code như trong `06-lab-complete/app/` | Chạy `check_production_ready.py` với Redis local: 38/38 check đạt. Test tay 422, history, `/docs` |
| 3 | Vì sao fakeredis không dùng được với script chấm | Script ping Redis bằng lệnh inline `PING\r\n` mà fakeredis không hỗ trợ | Đổi sang Redis bản Windows, `redis-cli ping` → `PONG`, socket inline → `+PONG` |
| 4 | Lỗi `UnicodeEncodeError cp1252` khi `run_eval.py --out` trên Windows | Bật `PYTHONUTF8=1`, không sửa code | Chạy lại, file JSON được ghi và đọc lại được |
| 5 | Sửa `KNOWLEDGE_BASE` cho `security-02`, `scaling-03` | 2 entry `jwt`, `hpa` | Grep golden dataset để chắc keyword mới không trùng câu khác. Eval cuối 23/23, 0 regression |
| 6 | Mô phỏng Exercise 5.4/5.5 khi không có Docker | 3 uvicorn + proxy round-robin Python | Chạy cả bản có Redis (10 message) và không Redis (2 message) để đối chứng |
| 7 | Soạn câu trả lời Part 1–5 và báo cáo Part 7 | Bản nháp các file `.md` | Mọi số liệu lấy từ output và JSON đã chạy. Phần cần Docker để ⏳, không điền số |
| 8 | Chạy Part 7 Bước 2 khi không có Docker | Dùng binary Windows cùng version với compose (Prometheus 3.16.0, Grafana 13.2.4, Jaeger 2.23.0, Nginx 1.28.0, Locust 2.47.0), config sao từ repo với hostname đổi thành `127.0.0.1` | Kiểm tra target `up`, `X-Served-By` luân phiên, `/metrics` qua Nginx trả 404, promtool `SUCCESS`. Khác biệt so với Docker (không giới hạn CPU, `psutil.suspend` thay `docker pause`) đã ghi trong báo cáo |
| 9 | Lỗi Grafana 500 (thiếu `public/build`) và panel trống | Do giải nén vượt giới hạn đường dẫn 260 ký tự, nên giải nén lại vào thư mục ngắn hơn. Panel trống do 401 với user ẩn danh và do chụp `full_page`, nên đăng nhập admin/admin và chụp với viewport cao | Xem lại từng ảnh: panel có số liệu, trace có span và event `exception`, Alerts có 2 FIRING |
| 10 | Đo thời gian tới lúc alert firing | Script poll `/api/v1/alerts` mỗi 2 s, ghi thời điểm pending/firing | Đối chiếu với `for` của từng rule (60/120/120/30 s). Log lưu ở `reports/alert_experiments.txt` |
| 11 | Chụp ảnh màn hình | Playwright điều khiển Edge headless có sẵn trên máy | Mở từng PNG xem lại nội dung trước khi đưa vào bài |
