import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class SiteOut(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str
    closing_time: time

    model_config = {"from_attributes": True}


class SiteUpdateIn(BaseModel):
    timezone: str = Field(min_length=1)
    closing_time: time


class AnalyzeIn(BaseModel):
    ends_at: datetime | None = None


class CameraOut(BaseModel):
    id: uuid.UUID
    name: str
    rtsp_url: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class ZoneIn(BaseModel):
    camera_id: uuid.UUID
    name: str
    kind: Literal[
        "entrance", "checkout_area", "store_room", "dining", "truck", "delivery_door", "custom"
    ] = "custom"
    polygon: list[list[float]] = Field(min_length=3)
    record_clips: bool = False
    privacy_mask: bool = False


class ZoneOut(ZoneIn):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class ZoneOccupancy(BaseModel):
    zone_id: uuid.UUID
    name: str
    count: int
    ts: datetime
    snapshot_url: str | None = None


class QueueStatus(BaseModel):
    zone_id: uuid.UUID
    name: str
    queue_len: int
    ts: datetime
    threshold: int | None = None
    snapshot_url: str | None = None


class LiveMetrics(BaseModel):
    total_occupancy: int
    ts: datetime | None = None
    snapshot_url: str | None = None
    per_zone: list[ZoneOccupancy]
    queues: list[QueueStatus]


class TrafficBucket(BaseModel):
    bucket_start: datetime
    entries: int


class DwellSummary(BaseModel):
    zone_name: str
    avg_dwell_s: float


class PeakOccupancy(BaseModel):
    value: int
    ts: datetime | None = None
    camera_name: str | None = None
    snapshot_url: str | None = None


class Summary(BaseModel):
    entries_total: int
    unique_visitors: int = 0
    peak_occupancy: PeakOccupancy
    avg_dwell: list[DwellSummary]
    last_entry_snapshot_url: str | None = None


class SourceZoneOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str


class SourceJobOut(BaseModel):
    state: str
    progress: float = 0.0
    events_written: int = 0
    error: str | None = None
    position: int = 0


class SourceOut(BaseModel):
    camera_id: uuid.UUID
    name: str
    source_type: Literal["upload", "cctv"]
    rtsp_url: str | None = None
    zones: list[SourceZoneOut] = []
    last_analyzed: datetime | None = None
    events_count: int = 0
    entries_count: int = 0
    has_processed: bool = False
    job: SourceJobOut | None = None


class UploadSourceOut(BaseModel):
    camera_id: uuid.UUID
    zone_id: uuid.UUID | None = None
    name: str
    duration_s: float
    fps: float


class CctvIn(BaseModel):
    rtsp_url: str = Field(pattern=r"^rtsps?://")
    name: str = Field(min_length=1, max_length=100)
    kind: Literal[
        "entrance", "checkout_area", "store_room", "dining", "truck", "delivery_door", "custom"
    ] = "entrance"
    auto_zone: bool = True


class CctvTestIn(BaseModel):
    rtsp_url: str = Field(pattern=r"^rtsps?://")


class CaptureIn(BaseModel):
    duration_s: int = Field(default=120, ge=10, le=300)


class EntryFrameOut(BaseModel):
    event_id: int
    ts: datetime
    zone_name: str
    snapshot_url: str | None = None


class ProductTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    units_per_package: int | None = Field(default=None, ge=1, le=10000)
    unit_label: str | None = Field(default=None, max_length=40)


class ProductSampleOut(BaseModel):
    id: uuid.UUID
    url: str


class ProductTypeOut(BaseModel):
    id: uuid.UUID
    name: str
    units_per_package: int | None
    unit_label: str | None
    samples: list[ProductSampleOut] = []


class DeliveryItem(BaseModel):
    product_type_id: str | None = None
    product_name: str
    count: int
    confidence: float


class DeliveryTripOut(BaseModel):
    event_id: int
    camera_name: str
    zone_name: str | None
    ts_start: datetime
    ts_end: datetime | None
    items: list[DeliveryItem] = []
    unmatched: int = 0
    snapshot_url: str | None = None
    crop_url: str | None = None


class SaveTripSampleIn(BaseModel):
    product_type_id: uuid.UUID


class DeliveryTotal(BaseModel):
    product_type_id: str | None = None
    product_name: str
    packages: int
    units: int | None = None
    unit_label: str | None = None


class DeliverySummaryOut(BaseModel):
    day: str
    trips: list[DeliveryTripOut] = []
    totals: list[DeliveryTotal] = []
    unmatched_packages: int = 0


class TelegramSettingsOut(BaseModel):
    chat_id: str | None
    enabled: bool
    digest_time: time | None
    bot_configured: bool


class TelegramSettingsIn(BaseModel):
    chat_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    digest_time: time | None = None


class DigestIn(BaseModel):
    day: date | None = None


class PosItem(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    qty: int = Field(ge=1, le=10_000)
    unit_price: int = Field(ge=0)


class PosReceiptIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=64)
    kind: Literal["sale", "void", "refund"] = "sale"
    ts: datetime
    total: int = Field(default=0, ge=0)
    items: list[PosItem] = []
    zone_id: uuid.UUID | None = None


class PosIngestIn(BaseModel):
    receipts: list[PosReceiptIn] = Field(min_length=1, max_length=500)


class PosIngestOut(BaseModel):
    ingested: int
    duplicates: int


class PosReceiptOut(BaseModel):
    id: uuid.UUID
    external_id: str
    kind: str
    ts: datetime
    total: int
    items: list[PosItem] = []
    zone_id: uuid.UUID | None = None
    zone_name: str | None = None
    source: str
    flag: str | None = None


class PosSeenItem(BaseModel):

    name: str
    qty: int


class DiscrepancyOut(BaseModel):
    flag: Literal["no_person_at_sale", "void_no_customer", "unscanned_visit"]
    status: Literal["open", "cleared"] = "open"
    ts: datetime
    ts_end: datetime | None = None
    zone_name: str | None = None
    receipt: PosReceiptOut | None = None
    seen_items: list[PosSeenItem] | None = None
    evidence_event_id: int | None = None
    snapshot_url: str | None = None
    explanation: str


class PosVisitOut(BaseModel):

    ts_start: datetime
    ts_end: datetime
    zone_name: str
    kind: Literal["sale", "administrative", "unclear"] | None = None
    items: list[PosSeenItem] = []
    confidence: float | None = None
    notes: str | None = None
    snapshot_url: str | None = None
    receipt: PosReceiptOut | None = None


class PosDiscrepanciesOut(BaseModel):
    day: str
    receipts_total: int
    discrepancies: list[DiscrepancyOut] = []
    unverified_receipts: int = 0


class PosSimulateIn(BaseModel):
    day: date | None = None


class PosSimulateOut(BaseModel):
    receipts: int
    planted: dict[str, int] = {}


class SavingsLine(BaseModel):
    key: Literal["queues", "after_hours", "deliveries", "pos"]
    count: int
    unit_value: int
    amount: int


class SavingsOut(BaseModel):
    month: str
    lines: list[SavingsLine] = []
    total: int
    subscription: int
    net: int
    constants: dict[str, int] = {}


class AlertRuleIn(BaseModel):
    zone_id: uuid.UUID
    metric: Literal["queue_len", "occupancy"]
    threshold: int
    sustain_seconds: int = 30
    is_active: bool = True


class AlertRuleOut(AlertRuleIn):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: int
    rule_id: uuid.UUID
    triggered_at: datetime
    value: int
    message: str
    status: str
    clip_url: str | None = None
    snapshot_url: str | None = None


class ClipUrl(BaseModel):
    url: str
    snapshot_url: str | None = None


class ToolCallTrace(BaseModel):
    name: str
    args: dict = {}


class ChatEventOut(BaseModel):
    id: int
    type: str
    zone_name: str | None
    ts_start: datetime
    attributes: dict
    snapshot_url: str | None = None


class ChatClipOut(BaseModel):
    event_id: int
    url: str
    ts_start: datetime


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    surface: Literal["ask", "live"] = "ask"


class ChatTurnOut(BaseModel):
    session_id: str
    answer_text: str
    degraded: bool = False
    events: list[ChatEventOut] = []
    clips: list[ChatClipOut] = []
    tool_calls: list[ToolCallTrace] = []


class ReportOut(BaseModel):
    day: str
    markdown: str
    data: dict
    generated_by: Literal["openai", "gemini", "openrouter", "fallback"]
    generated_at: datetime


class LiveEventOut(BaseModel):
    id: int
    type: str
    zone_name: str | None
    ts: datetime
    attributes: dict


class LiveEventsOut(BaseModel):
    events: list[LiveEventOut] = []
    latest_id: int | None = None


class WorkerCameraOut(BaseModel):
    camera_id: str
    name: str
    pid: int | None = None
    state: Literal["starting", "running", "reconnecting", "restarting", "stopped"]
    fps: float = 0.0
    tracks: int = 0
    events: int = 0
    started_at: datetime | None = None
    last_event_at: datetime | None = None
    restarts: int = 0
    error: str | None = None


class WorkerStatusOut(BaseModel):

    running: bool = False
    updated_at: datetime | None = None
    cameras: list[WorkerCameraOut] = []


class LiveCommentaryOut(BaseModel):
    skipped: bool = False
    reason: Literal["no_events", "debounce"] | None = None
    text: str | None = None
    degraded: bool = False
    events_count: int = 0
    with_image: bool = False
    latest_id: int | None = None


Role = Literal["owner", "admin", "viewer"]


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    company_name: str = Field(min_length=1, max_length=120)
    full_name: str | None = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class WhoAmIOut(BaseModel):

    kind: Literal["user", "api_key", "legacy"]
    tenant_id: uuid.UUID
    tenant_slug: str
    role: str
    user: UserOut | None = None


class UserCreateIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: Role = "viewer"
    full_name: str | None = Field(default=None, max_length=120)


class UserUpdateIn(BaseModel):
    role: Role | None = None
    full_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=128)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    role: str
    site_id: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Role = "admin"
    site_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class ResetIn(BaseModel):

    confirm: str = Field(min_length=1, max_length=80)


class ApiKeyCreatedOut(BaseModel):
    key: ApiKeyOut
    secret: str
