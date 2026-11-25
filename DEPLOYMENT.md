# Deployment Information

## Public URL

⏳ **CHỜ DEPLOY**: `https://<service>.up.railway.app` (điền sau khi chạy `railway domain`)

## Platform

**Railway** (deploy thư mục `06-lab-complete/`, builder `DOCKERFILE` theo [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml)) và dịch vụ **Redis** của Railway.
Phương án dự phòng: Render Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml) (web docker + Key Value).

## Các bước deploy (Railway CLI)

```bash
npm i -g @railway/cli
cd 06-lab-complete
railway login
railway init                                   # tạo project mới
railway add --database redis                   # tạo service Redis
railway up                                     # build Dockerfile trên Railway và deploy service agent
# Đặt biến cho service agent (dashboard → Variables, hoặc CLI):
railway variables --set "AGENT_API_KEY=<key ngẫu nhiên, KHÔNG commit>" \
                  --set 'REDIS_URL=${{Redis.REDIS_URL}}' \
                  --set "ENVIRONMENT=production" \
                  --set "RATE_LIMIT_PER_MINUTE=10" \
                  --set "DAILY_BUDGET_USD=1.0" --set "MONTHLY_BUDGET_USD=10.0"
railway domain                                 # tạo public URL
```

## Test Commands (CP6, mỗi lệnh chỉ in mã HTTP, không lộ key)

```bash
BASE=https://<service>.up.railway.app
AGENT_API_KEY=<key đã đặt trên Railway>

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả: ⏳

### API Test (with authentication)

```bash
curl -X POST "$BASE/ask" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "Hello"}'
```

### Rate limit (vượt 10 req/phút → 429)

```bash
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "$i -> %{http_code}\n" -X POST "$BASE/ask" \
    -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
    -d '{"user_id":"test","question":"test"}'
done
```

## Environment Variables Set (trên Railway, không ghi trong code)

| Biến | Giá trị |
|---|---|
| `PORT` | Railway tự cấp (CMD dùng `${PORT:-8000}`) |
| `AGENT_API_KEY` | secret, chỉ đặt trên Railway |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` (tham chiếu tới service Redis) |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` / `MONTHLY_BUDGET_USD` | `1.0` / `10.0` |
| `LOG_LEVEL` | `INFO` (mặc định) |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png) ⏳
- [Service running](screenshots/running.png) ⏳
- [Test results](screenshots/test.png) ⏳
