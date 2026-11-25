"""
Production AI Agent — Part 6 Final Project

    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2

Endpoint:
    GET  /health  liveness  — luôn 200 khi process còn sống (không phụ thuộc Redis)
    GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không
    POST /ask     X-API-Key → rate limit (429) → cost guard (402) → mock LLM
"""
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "pid": os.getpid(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifespan: startup + graceful shutdown (tiêu chí 11)
#   Uvicorn tự xử lý SIGTERM: ngừng nhận kết nối mới, đợi request đang chạy
#   xong, rồi mới chạy phần shutdown bên dưới và thoát với exit code 0.
#   Vì vậy app KHÔNG đăng ký signal handler riêng (tránh chặn mất handler của uvicorn).
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              redis_ok=redis_healthy())
    yield
    log_event("shutdown", detail="graceful shutdown: in-flight requests finished, closing Redis pool")
    try:
        r.close()
    except Exception:
        pass


app = FastAPI(
    title="Production AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    # Tắt Swagger UI ngoài môi trường dev
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.environment == "development" else None,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event("unhandled_error", logging.ERROR, request_id=request_id,
                  path=request.url.path, method=request.method)
        response = JSONResponse(status_code=500,
                                content={"error": "internal_error", "request_id": request_id})
    ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    log_event("request", request_id=request_id, method=request.method,
              path=request.url.path, status=response.status_code, ms=ms)
    return response


# ─────────────────────────────────────────────────────────
# Error handling: mã lỗi rõ ràng, không lộ stack trace
# ─────────────────────────────────────────────────────────
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code,
                        content={"error": exc.detail, "status": exc.status_code},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    problems = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                for e in exc.errors()]
    return JSONResponse(status_code=422,
                        content={"error": "invalid_request", "status": 422, "details": problems})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, error_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error", "status": 500})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field("anonymous", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: process còn sống là 200, không kiểm tra Redis."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis (state store) kết nối được."""
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not ready", "redis": "unreachable"})
    return {"status": "ready", "redis": "ok"}


def _history_key(user_id: str) -> str:
    return f"history:{user_id}"


def _load_history(user_id: str) -> list:
    try:
        return [json.loads(m) for m in r.lrange(_history_key(user_id), 0, -1)]
    except (redis.RedisError, OSError, ValueError):
        return []


def _save_turn(user_id: str, question: str, answer: str) -> None:
    key = _history_key(user_id)
    try:
        pipe = r.pipeline(transaction=True)
        pipe.rpush(key, json.dumps({"role": "user", "content": question}, ensure_ascii=False),
                   json.dumps({"role": "assistant", "content": answer}, ensure_ascii=False))
        pipe.ltrim(key, -settings.history_max_messages, -1)
        pipe.expire(key, 24 * 3600)
        pipe.execute()
    except (redis.RedisError, OSError):
        log_event("history_save_failed", logging.WARNING, user_id=user_id)


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    remaining = check_rate_limit(api_key)          # 429 khi vượt giới hạn
    check_budget()                                  # 402 khi hết budget

    history = _load_history(body.user_id)           # state trong Redis, không trong RAM
    prompt = "\n".join(m["content"] for m in history[-6:]) + "\n" + body.question
    answer = llm_ask(body.question)

    input_tokens, output_tokens = estimate_tokens(prompt), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    _save_turn(body.user_id, body.question, answer)

    log_event("llm_call", user_id=body.user_id, input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8), rate_remaining=remaining)
    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
        "history_length": len(history) + 2,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }
