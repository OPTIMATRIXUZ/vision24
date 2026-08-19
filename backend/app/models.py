import uuid
from datetime import date, datetime, time

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EVENT_TYPES = (
    "entry",
    "exit",
    "occupancy",
    "queue_len",
    "dwell",
    "delivery_trip",
    "delivery_summary",
)
ZONE_KINDS = (
    "entrance",
    "checkout_area",
    "store_room",
    "dining",
    "truck",
    "delivery_door",
    "custom",
)
ALERT_METRICS = ("queue_len", "occupancy")


class Base(DeclarativeBase):
    pass


ROLES = ("owner", "admin", "viewer")


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(String, unique=True)
    stats_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    sites: Mapped[list["Site"]] = relationship(back_populates="tenant")
    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class Site(Base):
    __tablename__ = "site"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String, default="Asia/Tashkent")
    closing_time: Mapped[time] = mapped_column(Time, default=time(21, 0))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    telegram_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    telegram_digest_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    telegram_digest_last_sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="sites")
    cameras: Mapped[list["Camera"]] = relationship(back_populates="site")
    zones: Mapped[list["Zone"]] = relationship(back_populates="site")


class Camera(Base):
    __tablename__ = "camera"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("site.id"))
    name: Mapped[str] = mapped_column(String)
    rtsp_url: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="general")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    site: Mapped[Site] = relationship(back_populates="cameras")


class Zone(Base):
    __tablename__ = "zone"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("site.id"))
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("camera.id"))
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="custom")
    polygon: Mapped[list] = mapped_column(JSONB)
    record_clips: Mapped[bool] = mapped_column(Boolean, default=False)
    privacy_mask: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    site: Mapped[Site] = relationship(back_populates="zones")


class ProductType(Base):

    __tablename__ = "product_type"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("site.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    units_per_package: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    samples: Mapped[list["ProductSample"]] = relationship(back_populates="product")

    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_product_type_site_name"),)


class ProductSample(Base):

    __tablename__ = "product_sample"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_type.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    product: Mapped[ProductType] = relationship(back_populates="samples")


RECEIPT_KINDS = ("sale", "void", "refund")


class PosReceipt(Base):

    __tablename__ = "pos_receipt"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("site.id"), index=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("zone.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str] = mapped_column(String, default="api", server_default="api")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("site_id", "external_id", name="uq_pos_receipt_site_external"),
        Index("ix_pos_receipt_site_ts", "site_id", "ts"),
    )


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("camera.id"))
    zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("zone.id"), nullable=True)
    type: Mapped[str] = mapped_column(String)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    ts_end: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_event_camera_ts", "camera_id", "ts_start"),
        Index("ix_event_zone_type_ts", "zone_id", "type", "ts_start"),
    )


class Clip(Base):
    __tablename__ = "clip"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))
    storage_key: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_key: Mapped[str | None] = mapped_column(String, nullable=True)
    ts_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    people_frames: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class Embedding(Base):

    __tablename__ = "embedding"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    vec: Mapped[list] = mapped_column(Vector(512))


class AlertRule(Base):
    __tablename__ = "alert_rule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("zone.id"))
    metric: Mapped[str] = mapped_column(String)
    threshold: Mapped[int] = mapped_column(Integer)
    sustain_seconds: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_rule.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id"), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    value: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="open")


class SiteDailyStats(Base):

    __tablename__ = "site_daily_stats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("site.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    visitors: Mapped[int] = mapped_column(Integer)
    peak_occupancy: Mapped[int] = mapped_column(Integer)
    peak_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_dwell_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_breaches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    alerts_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("site_id", "day", name="uq_site_daily_stats_site_day"),)


JOB_STATES = ("queued", "capturing", "running", "done", "error")
JOB_TERMINAL = ("done", "error")


class AnalysisJob(Base):

    __tablename__ = "analysis_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("camera.id"), index=True)
    kind: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    events_written: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    runtime_id: Mapped[str] = mapped_column(String(32))
    queued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (Index("ix_analysis_job_camera_queued", "camera_id", "queued_at"),)


class User(Base):

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class UserSession(Base):

    __tablename__ = "user_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String, unique=True)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_session.id"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(String, nullable=True)


class ApiKey(Base):

    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("site.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    prefix: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="admin")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
