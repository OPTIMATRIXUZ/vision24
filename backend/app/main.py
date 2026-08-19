import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app import exception_handlers, middleware
from app.config import settings
from app.logging_config import configure_logging
from app.routers import (
    alerts,
    api_keys,
    auth,
    cameras,
    chat,
    clips,
    deliveries,
    live,
    metrics,
    pos,
    products,
    report,
    site,
    sources,
    stats,
    telegram,
    tts,
    users,
    videos,
    zones,
)

configure_logging(
    "api",
    level=settings.log_level,
    fmt=settings.log_format,
    log_dir=settings.log_path,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.deps import check_auth_config, check_reset_config
    from app.services import jobs, replay
    from app.services import tts as tts_service

    log.info(settings.startup_report())
    check_auth_config()
    check_reset_config()

    try:
        jobs.reconcile_orphans()
    except SQLAlchemyError as exc:
        log.warning("Could not reconcile interrupted jobs: %s", exc)
    if settings.ai_degraded:
        log.warning(
            "No API key for AI_PROVIDER=%s — chat and reports will use the "
            "deterministic keyword fallback.",
            settings.ai_provider,
        )

    tts_service.warmup()

    import asyncio

    from app.services import digest_scheduler

    digest_task = asyncio.create_task(digest_scheduler.run_forever())

    try:
        yield
    finally:
        digest_task.cancel()
        replay.stop_replay()


app = FastAPI(title="Vision 24 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    expose_headers=["X-Request-ID", "X-Auth-Deprecated"],
)

middleware.register(app)
exception_handlers.register(app)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": app.version,
        "ai_degraded": settings.ai_degraded,
        "tts_enabled": settings.tts_enabled,
    }


@app.get("/api/ready")
def ready():
    from app.services import readiness

    body, is_ready = readiness.report()
    return JSONResponse(status_code=200 if is_ready else 503, content=body)


app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(api_keys.router, prefix="/api")
app.include_router(site.router, prefix="/api")
app.include_router(cameras.router, prefix="/api")
app.include_router(zones.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(clips.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(videos.router, prefix="/api")
app.include_router(live.router, prefix="/api")
app.include_router(tts.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(deliveries.router, prefix="/api")
app.include_router(pos.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
