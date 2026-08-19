import logging
from datetime import date

from fastapi import APIRouter

from app.deps import DbDep, LocaleDep, SiteDep
from app.errors import AIProviderError
from app.schemas import ReportOut
from app.services.ai import report as report_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["report"])


@router.get("/report", response_model=ReportOut)
def get_report(
    db: DbDep,
    site: SiteDep,
    locale: LocaleDep,
    day: date | None = None,
    refresh: bool = False,
):
    try:
        return report_service.generate_report(db, site, day, refresh, locale)
    except Exception as exc:
        log.exception("Report generation failed")
        raise AIProviderError("The report could not be generated.") from exc
