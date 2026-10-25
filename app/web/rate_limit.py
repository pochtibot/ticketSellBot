"""Лёгкий per-IP rate limiting для web API (без внешних зависимостей).

Web (FastAPI) работает в одном uvicorn-воркере → in-memory счётчики достаточны.
Лимит — скользящее окно в 1 минуту, по умолчанию settings.rate_limit_per_minute.
Whitelist: /health, /metrics, /static (мониторинг и статика не лимитируются).

Защита: brute-force кода привязки (/me/link), скрейпинг, DDoS-спайки.
"""

import time
import ipaddress
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

_WHITELIST_PREFIXES = ("/health", "/metrics", "/static")
_WINDOW_SECONDS = 60


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Ограничивает число запросов с одного IP в скользящем окне."""

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Return the real client IP when the trusted Nginx proxy supplied it.

        Nginx overwrites X-Real-IP with $remote_addr. Do not use the
        comma-separated X-Forwarded-For chain: its leftmost values may be
        supplied by the client. Keep request.client as a safe fallback for
        local/test deployments without the configured proxy.
        """
        forwarded_ip = request.headers.get("x-real-ip", "").strip()
        if forwarded_ip:
            try:
                return str(ipaddress.ip_address(forwarded_ip))
            except ValueError:
                pass
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _WHITELIST_PREFIXES):
            return await call_next(request)

        limit = settings.rate_limit_per_minute
        if limit and limit > 0:
            ip = self._client_ip(request)
            now = time.monotonic()

            # Remove idle IP buckets periodically so a long-running process
            # does not retain every address it has ever seen.
            if now - self._last_cleanup >= _WINDOW_SECONDS:
                cutoff = now - _WINDOW_SECONDS
                for address in [
                    address for address, timestamps in self._requests.items()
                    if not timestamps or timestamps[-1] <= cutoff
                ]:
                    del self._requests[address]
                self._last_cleanup = now

            bucket = self._requests[ip]
            # Очистить записи старше окна (скользящее окно)
            bucket[:] = [t for t in bucket if t > now - _WINDOW_SECONDS]
            if len(bucket) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Слишком много запросов. Попробуйте позже."},
                )
            bucket.append(now)

        return await call_next(request)
