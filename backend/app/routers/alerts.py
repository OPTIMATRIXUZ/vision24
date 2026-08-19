import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app import storage
from app.deps import DbDep, SiteDep, require_admin
from app.errors import NotFoundError
from app.models import Alert, AlertRule, Clip
from app.schemas import AlertOut, AlertRuleIn, AlertRuleOut
from app.scoping import site_zone_ids

router = APIRouter(tags=["alerts"])


@router.get("/alert-rules", response_model=list[AlertRuleOut])
def list_rules(db: DbDep, site: SiteDep):
    return db.scalars(select(AlertRule).where(AlertRule.zone_id.in_(site_zone_ids(db, site)))).all()


@router.post("/alert-rules", response_model=AlertRuleOut, dependencies=[Depends(require_admin)])
def create_rule(body: AlertRuleIn, db: DbDep, site: SiteDep):
    if body.zone_id not in site_zone_ids(db, site):
        raise NotFoundError("Zone not found.")
    rule = AlertRule(**body.model_dump())
    db.add(rule)
    db.commit()
    return rule


@router.put(
    "/alert-rules/{rule_id}", response_model=AlertRuleOut, dependencies=[Depends(require_admin)]
)
def update_rule(rule_id: uuid.UUID, body: AlertRuleIn, db: DbDep, site: SiteDep):
    zone_ids = site_zone_ids(db, site)
    rule = db.get(AlertRule, rule_id)
    if rule is None or rule.zone_id not in zone_ids:
        raise NotFoundError("Rule not found.")
    if body.zone_id not in zone_ids:
        raise NotFoundError("Zone not found.")
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    db.commit()
    return rule


@router.delete("/alert-rules/{rule_id}", dependencies=[Depends(require_admin)])
def delete_rule(rule_id: uuid.UUID, db: DbDep, site: SiteDep):
    rule = db.get(AlertRule, rule_id)
    if rule is None or rule.zone_id not in site_zone_ids(db, site):
        raise NotFoundError("Rule not found.")
    db.delete(rule)
    db.commit()
    return {"deleted": str(rule_id)}


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(db: DbDep, site: SiteDep, limit: int = 50):
    alerts = db.scalars(
        select(Alert)
        .where(
            Alert.rule_id.in_(
                select(AlertRule.id).where(AlertRule.zone_id.in_(site_zone_ids(db, site)))
            )
        )
        .order_by(Alert.triggered_at.desc())
        .limit(limit)
    ).all()

    out: list[AlertOut] = []
    for alert in alerts:
        clip_url = snapshot_url = None
        if alert.event_id is not None:
            clip = db.scalars(select(Clip).where(Clip.event_id == alert.event_id)).first()
            if clip is not None:
                if clip.storage_key:
                    clip_url = storage.presign_get(clip.storage_key)
                if clip.snapshot_key:
                    snapshot_url = storage.presign_get(clip.snapshot_key)
        out.append(
            AlertOut(
                id=alert.id,
                rule_id=alert.rule_id,
                triggered_at=alert.triggered_at,
                value=alert.value,
                message=alert.message,
                status=alert.status,
                clip_url=clip_url,
                snapshot_url=snapshot_url,
            )
        )
    return out
