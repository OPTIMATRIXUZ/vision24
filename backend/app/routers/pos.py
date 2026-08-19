from datetime import date

from fastapi import APIRouter, Depends

from app.deps import DbDep, SiteDep, require_admin
from app.schemas import (
    PosDiscrepanciesOut,
    PosIngestIn,
    PosIngestOut,
    PosReceiptOut,
    PosSimulateIn,
    PosSimulateOut,
    PosVisitOut,
    SavingsOut,
)
from app.services import pos

router = APIRouter(tags=["pos"])


@router.post("/pos/receipts", response_model=PosIngestOut, dependencies=[Depends(require_admin)])
def ingest_receipts(body: PosIngestIn, db: DbDep, site: SiteDep):
    ingested, duplicates = pos.ingest_receipts(db, site, body.receipts)
    return PosIngestOut(ingested=ingested, duplicates=duplicates)


@router.get(
    "/pos/receipts",
    response_model=list[PosReceiptOut],
    dependencies=[Depends(require_admin)],
)
def list_receipts(db: DbDep, site: SiteDep, day: date | None = None):
    return pos.list_receipts(db, site, day)


@router.get(
    "/pos/discrepancies",
    response_model=PosDiscrepanciesOut,
    dependencies=[Depends(require_admin)],
)
def get_discrepancies(db: DbDep, site: SiteDep, day: date | None = None):
    return pos.get_discrepancies(db, site, day)


@router.get(
    "/pos/visits",
    response_model=list[PosVisitOut],
    dependencies=[Depends(require_admin)],
)
def list_visits(db: DbDep, site: SiteDep, day: date | None = None):
    return pos.list_visits(db, site, day)


@router.post("/pos/simulate", response_model=PosSimulateOut, dependencies=[Depends(require_admin)])
def simulate(body: PosSimulateIn, db: DbDep, site: SiteDep):
    result = pos.simulate_day(db, site, body.day)
    return PosSimulateOut(**result)


@router.get("/savings", response_model=SavingsOut)
def get_savings(db: DbDep, site: SiteDep, month: str | None = None):
    return pos.get_savings(db, site, month)
