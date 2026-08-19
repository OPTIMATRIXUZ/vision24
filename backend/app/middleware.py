import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.logging_config import request_id_var

log = logging.getLogger("app.access")

HEADER = "X-Request-ID"


def register(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get(HEADER) or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = (time.perf_counter() - started) * 1000
            response.headers[HEADER] = rid
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            log.log(
                level,
                "%s %s -> %d in %.0fms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed,
            )
            return response
        except Exception:
            elapsed = (time.perf_counter() - started) * 1000
            log.warning(
                "%s %s -> unhandled exception in %.0fms",
                request.method,
                request.url.path,
                elapsed,
            )
            raise
        finally:
            request_id_var.reset(token)
