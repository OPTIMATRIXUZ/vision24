import { toApiError } from "@/lib/api-error";

export const GO2RTC_URL = process.env.NEXT_PUBLIC_GO2RTC_URL ?? "http://localhost:1984";

const CREDENTIALS: RequestCredentials = "include";

async function send(rawPath: string, init: RequestInit): Promise<Response> {
  return fetch(withSite(rawPath), { ...init, credentials: CREDENTIALS });
}

const JSON_HEADERS = { "Content-Type": "application/json" };

const SITE_KEY = "v24.site_id";

let selectedSiteId = "";

export function getSelectedSite(): string {
  if (selectedSiteId) return selectedSiteId;
  if (typeof window === "undefined") return "";
  selectedSiteId = window.localStorage.getItem(SITE_KEY) ?? "";
  return selectedSiteId;
}

export function setSelectedSite(siteId: string): void {
  selectedSiteId = siteId;
  if (typeof window === "undefined") return;
  if (siteId) window.localStorage.setItem(SITE_KEY, siteId);
  else window.localStorage.removeItem(SITE_KEY);
}

function withSite(path: string): string {
  const site = getSelectedSite();
  if (!site || path.startsWith("/api/auth/") || path.includes("site_id=")) return path;
  return path + (path.includes("?") ? "&" : "?") + `site_id=${encodeURIComponent(site)}`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await send(path, { ...init, headers: { ...JSON_HEADERS, ...init?.headers } });
  if (!res.ok) throw await toApiError(res);
  return res.json();
}

export async function* apiStream<T>(path: string, init?: RequestInit): AsyncGenerator<T> {
  const res = await send(path, { ...init, headers: { ...JSON_HEADERS, ...init?.headers } });
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw new Error("Response has no body to stream");

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += value;
      let split: number;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (data) yield JSON.parse(data) as T;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}

async function apiUpload<T>(path: string, file: File, fields?: Record<string, string>): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  for (const [key, value] of Object.entries(fields ?? {})) {
    form.append(key, value);
  }

  const res = await send(path, { method: "POST", body: form });
  if (!res.ok) throw await toApiError(res);
  return res.json();
}

export const startReplay = (cameraId?: string) =>
  api<{ playing: boolean; stream?: string }>("/api/live/replay", {
    method: "POST",
    body: JSON.stringify(cameraId ? { camera_id: cameraId } : {}),
  });
export const replayStatus = () => api<{ playing: boolean; stream?: string }>("/api/live/replay");
export const workerStatus = () => api<WorkerStatus>("/api/live/workers");

export async function synthesizeSpeech(text: string, signal?: AbortSignal): Promise<Blob> {
  const res = await send("/api/tts", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ text }),
    signal,
  });
  if (!res.ok) throw await toApiError(res);
  return res.blob();
}

export async function apiBlob(path: string): Promise<string> {
  const res = await send(path, {});
  if (!res.ok) throw await toApiError(res);
  return URL.createObjectURL(await res.blob());
}

export interface WorkerCamera {
  camera_id: string;
  name: string;
  pid: number | null;
  state: "starting" | "running" | "reconnecting" | "restarting" | "stopped";
  fps: number;
  tracks: number;
  events: number;
  started_at: string | null;
  last_event_at: string | null;
  restarts: number;
  error: string | null;
}

export interface WorkerStatus {
  running: boolean;
  updated_at: string | null;
  cameras: WorkerCamera[];
}

export interface Camera {
  id: string;
  name: string;
  rtsp_url: string;
  role: string;
  is_active: boolean;
}

export interface Zone {
  id: string;
  camera_id: string;
  name: string;
  kind:
    "entrance" | "checkout_area" | "store_room" | "dining" | "truck" | "delivery_door" | "custom";
  polygon: number[][];
  record_clips: boolean;
  privacy_mask: boolean;
}

export interface LiveMetrics {
  total_occupancy: number;
  ts: string | null;
  snapshot_url: string | null;
  per_zone: {
    zone_id: string;
    name: string;
    count: number;
    ts: string;
    snapshot_url: string | null;
  }[];
  queues: {
    zone_id: string;
    name: string;
    queue_len: number;
    ts: string;
    threshold: number | null;
    snapshot_url: string | null;
  }[];
}

export interface TrafficBucket {
  bucket_start: string;
  entries: number;
}

export interface Summary {
  entries_total: number;
  unique_visitors: number;
  peak_occupancy: {
    value: number;
    ts: string | null;
    camera_name: string | null;
    snapshot_url: string | null;
  };
  avg_dwell: { zone_name: string; avg_dwell_s: number }[];
  last_entry_snapshot_url: string | null;
}

export interface EntryFrame {
  event_id: number;
  ts: string;
  zone_name: string;
  snapshot_url: string | null;
}

export interface Alert {
  id: number;
  rule_id: string;
  triggered_at: string;
  value: number;
  message: string;
  status: string;
  clip_url: string | null;
  snapshot_url: string | null;
}

export interface UploadedVideo {
  camera_id: string;
  name: string;
  duration_s: number;
  fps: number;
}

export interface AnalysisStatus {
  state: "idle" | "queued" | "capturing" | "running" | "done" | "error";
  progress: number;
  events_written: number;
  error: string | null;
  position?: number;
}

export type ZoneKind =
  "entrance" | "checkout_area" | "store_room" | "dining" | "truck" | "delivery_door" | "custom";

export interface SourceJob {
  state: "queued" | "capturing" | "running" | "done" | "error";
  progress: number;
  events_written: number;
  error: string | null;
  position: number;
}

export interface Source {
  camera_id: string;
  name: string;
  source_type: "upload" | "cctv";
  rtsp_url: string | null;
  zones: { id: string; name: string; kind: ZoneKind }[];
  last_analyzed: string | null;
  events_count: number;
  entries_count: number;
  has_processed: boolean;
  job: SourceJob | null;
}

export interface UploadSourceResult {
  camera_id: string;
  zone_id: string | null;
  name: string;
  duration_s: number;
  fps: number;
}

export function getSources(): Promise<Source[]> {
  return api<Source[]>("/api/sources");
}

export function addUploadSource(
  file: File,
  name: string,
  kind: ZoneKind,
  autoZone: boolean,
): Promise<UploadSourceResult> {
  return apiUpload<UploadSourceResult>("/api/sources/upload", file, {
    name,
    kind,
    auto_zone: String(autoZone),
  });
}

export function reuploadSource(cameraId: string, file: File): Promise<UploadSourceResult> {
  return apiUpload<UploadSourceResult>(`/api/sources/${cameraId}/reupload`, file);
}

export const createDemoSource = () =>
  api<UploadSourceResult>("/api/sources/demo", { method: "POST" });

export function addCctvSource(body: {
  rtsp_url: string;
  name: string;
  kind: ZoneKind;
  auto_zone: boolean;
}): Promise<UploadSourceResult> {
  return api<UploadSourceResult>("/api/sources/cctv", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function testCctv(
  rtspUrl: string,
): Promise<{ ok: boolean; snapshot_b64?: string; error?: string }> {
  return api("/api/sources/cctv/test", {
    method: "POST",
    body: JSON.stringify({ rtsp_url: rtspUrl }),
  });
}

export function stopCapture(cameraId: string): Promise<{ status: string }> {
  return api(`/api/sources/${cameraId}/capture/stop`, { method: "POST" });
}

export function captureNow(cameraId: string, durationS: number): Promise<{ status: string }> {
  return api(`/api/sources/${cameraId}/capture`, {
    method: "POST",
    body: JSON.stringify({ duration_s: durationS }),
  });
}

export function analyzeSource(cameraId: string, endsAt?: string): Promise<{ status: string }> {
  return api(`/api/videos/${cameraId}/analyze`, {
    method: "POST",
    body: JSON.stringify(endsAt ? { ends_at: endsAt } : {}),
  });
}

export interface SiteInfo {
  id: string;
  name: string;
  timezone: string;
  closing_time: string;
}

export function getSite(): Promise<SiteInfo> {
  return api<SiteInfo>("/api/site");
}

export function updateSite(timezone: string, closingTime: string): Promise<SiteInfo> {
  return api<SiteInfo>("/api/site", {
    method: "PUT",
    body: JSON.stringify({ timezone, closing_time: closingTime }),
  });
}

export interface AlertRule {
  id: string;
  zone_id: string;
  metric: "queue_len" | "occupancy";
  threshold: number;
  sustain_seconds: number;
  is_active: boolean;
}

export function getAlertRules(): Promise<AlertRule[]> {
  return api<AlertRule[]>("/api/alert-rules");
}

export function createAlertRule(rule: Omit<AlertRule, "id">): Promise<AlertRule> {
  return api<AlertRule>("/api/alert-rules", { method: "POST", body: JSON.stringify(rule) });
}

export function updateAlertRule(id: string, rule: Omit<AlertRule, "id">): Promise<AlertRule> {
  return api<AlertRule>(`/api/alert-rules/${id}`, { method: "PUT", body: JSON.stringify(rule) });
}

export function deleteAlertRule(id: string): Promise<{ deleted: string }> {
  return api(`/api/alert-rules/${id}`, { method: "DELETE" });
}

export function deleteSource(cameraId: string): Promise<{ deleted: string }> {
  return api(`/api/sources/${cameraId}`, { method: "DELETE" });
}

export function analyzeAll(): Promise<{ queued: string[]; skipped: unknown[] }> {
  return api("/api/sources/analyze-all", { method: "POST" });
}

export interface ToolCallTrace {
  name: string;
  args: Record<string, unknown>;
}

export interface ChatEvent {
  id: number;
  type: string;
  zone_name: string | null;
  ts_start: string;
  attributes: Record<string, unknown>;
  snapshot_url: string | null;
}

export interface ChatClip {
  event_id: number;
  url: string;
  ts_start: string;
}

export interface ChatTurn {
  session_id: string;
  answer_text: string;
  degraded: boolean;
  events: ChatEvent[];
  clips: ChatClip[];
  tool_calls: ToolCallTrace[];
}

export interface Report {
  day: string;
  markdown: string;
  data: Record<string, unknown>;
  generated_by: "openai" | "gemini" | "openrouter" | "fallback";
  generated_at: string;
}

export type ChatSurface = "ask" | "live";

export type ChatStreamEvent =
  | { type: "delta"; text: string }
  | { type: "reset" }
  | { type: "tool"; name: string; args: Record<string, unknown> }
  | { type: "done"; turn: ChatTurn }
  | { type: "error"; message: string };

export function postChatStream(
  sessionId: string,
  message: string,
  surface: ChatSurface = "ask",
): AsyncGenerator<ChatStreamEvent> {
  return apiStream<ChatStreamEvent>("/api/chat/stream", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message, surface }),
  });
}

export function deleteChat(sessionId: string): Promise<{ deleted: string }> {
  return api<{ deleted: string }>(`/api/chat/${sessionId}`, { method: "DELETE" });
}

export function getProcessedVideo(cameraId: string): Promise<{ url: string }> {
  return api<{ url: string }>(`/api/videos/${cameraId}/processed`);
}

export function getHeatmap(cameraId: string): Promise<{ url: string }> {
  return api<{ url: string }>(`/api/videos/${cameraId}/heatmap`);
}

export interface LiveEvent {
  id: number;
  type: string;
  zone_name: string | null;
  ts: string;
  attributes: Record<string, unknown>;
}

export function getReport(day?: string, refresh?: boolean): Promise<Report> {
  const params = new URLSearchParams();
  if (day) params.set("day", day);
  if (refresh) params.set("refresh", "true");
  const qs = params.toString();
  return api<Report>(`/api/report${qs ? `?${qs}` : ""}`);
}

export interface ProductSample {
  id: string;
  url: string;
}

export interface ProductType {
  id: string;
  name: string;
  units_per_package: number | null;
  unit_label: string | null;
  samples: ProductSample[];
}

export interface DeliveryItem {
  product_type_id: string | null;
  product_name: string;
  count: number;
  confidence: number;
}

export interface DeliveryTrip {
  event_id: number;
  camera_name: string;
  zone_name: string | null;
  ts_start: string;
  ts_end: string | null;
  items: DeliveryItem[];
  unmatched: number;
  snapshot_url: string | null;

  crop_url: string | null;
}

export interface DeliveryTotal {
  product_type_id: string | null;
  product_name: string;
  packages: number;
  units: number | null;
  unit_label: string | null;
}

export interface DeliverySummary {
  day: string;
  trips: DeliveryTrip[];
  totals: DeliveryTotal[];
  unmatched_packages: number;
}

export const getProducts = () => api<ProductType[]>("/api/products");

export function createProduct(body: {
  name: string;
  units_per_package: number | null;
  unit_label: string | null;
}): Promise<ProductType> {
  return api<ProductType>("/api/products", { method: "POST", body: JSON.stringify(body) });
}

export function updateProduct(
  id: string,
  body: { name: string; units_per_package: number | null; unit_label: string | null },
): Promise<ProductType> {
  return api<ProductType>(`/api/products/${id}`, { method: "PUT", body: JSON.stringify(body) });
}

export function deleteProduct(id: string): Promise<{ deleted: string }> {
  return api(`/api/products/${id}`, { method: "DELETE" });
}

export function addProductSample(productId: string, file: File): Promise<ProductSample> {
  return apiUpload<ProductSample>(`/api/products/${productId}/samples`, file);
}

export function deleteProductSample(
  productId: string,
  sampleId: string,
): Promise<{ deleted: string }> {
  return api(`/api/products/${productId}/samples/${sampleId}`, { method: "DELETE" });
}

export function saveTripAsSample(eventId: number, productTypeId: string): Promise<ProductSample> {
  return api<ProductSample>(`/api/deliveries/${eventId}/sample`, {
    method: "POST",
    body: JSON.stringify({ product_type_id: productTypeId }),
  });
}

export function getDeliveries(day?: string, cameraId?: string): Promise<DeliverySummary> {
  const params = new URLSearchParams();
  if (day) params.set("day", day);
  if (cameraId) params.set("camera_id", cameraId);
  const qs = params.toString();
  return api<DeliverySummary>(`/api/deliveries${qs ? `?${qs}` : ""}`);
}

export interface TelegramSettings {
  chat_id: string | null;
  enabled: boolean;
  digest_time: string | null;

  bot_configured: boolean;
}

export const getTelegramSettings = () => api<TelegramSettings>("/api/telegram");

export function updateTelegramSettings(body: {
  chat_id: string | null;
  enabled: boolean;
  digest_time: string | null;
}): Promise<TelegramSettings> {
  return api<TelegramSettings>("/api/telegram", { method: "PUT", body: JSON.stringify(body) });
}

export const sendTelegramTest = () =>
  api<{ sent: boolean }>("/api/telegram/test", { method: "POST" });

export const sendTelegramDigest = () =>
  api<{ sent: boolean }>("/api/telegram/digest", { method: "POST", body: JSON.stringify({}) });

export interface PosItem {
  sku: string;
  name: string;
  qty: number;
  unit_price: number;
}

export type PosFlag = "no_person_at_sale" | "void_no_customer" | "unscanned_visit";

export interface PosReceipt {
  id: string;
  external_id: string;
  kind: "sale" | "void" | "refund";
  ts: string;
  total: number;
  items: PosItem[];
  zone_id: string | null;
  zone_name: string | null;
  source: "api" | "simulated";

  flag: PosFlag | null;
}

export interface PosSeenItem {
  name: string;
  qty: number;
}

export interface PosDiscrepancy {
  flag: PosFlag;

  status: "open" | "cleared";
  ts: string;
  ts_end: string | null;
  zone_name: string | null;
  receipt: PosReceipt | null;

  seen_items: PosSeenItem[] | null;
  evidence_event_id: number | null;
  snapshot_url: string | null;
  explanation: string;
}

export interface PosVisit {
  ts_start: string;
  ts_end: string;
  zone_name: string;

  kind: "sale" | "administrative" | "unclear" | null;

  items: PosSeenItem[];
  confidence: number | null;
  notes: string | null;
  snapshot_url: string | null;
  receipt: PosReceipt | null;
}

export interface PosDiscrepancies {
  day: string;
  receipts_total: number;
  discrepancies: PosDiscrepancy[];

  unverified_receipts: number;
}

export function getPosReceipts(day?: string): Promise<PosReceipt[]> {
  return api<PosReceipt[]>(`/api/pos/receipts${day ? `?day=${day}` : ""}`);
}

export function getPosDiscrepancies(day?: string): Promise<PosDiscrepancies> {
  return api<PosDiscrepancies>(`/api/pos/discrepancies${day ? `?day=${day}` : ""}`);
}

export function getPosVisits(day?: string): Promise<PosVisit[]> {
  return api<PosVisit[]>(`/api/pos/visits${day ? `?day=${day}` : ""}`);
}

export function simulatePos(
  day?: string,
): Promise<{ receipts: number; planted: Record<string, number> }> {
  return api("/api/pos/simulate", { method: "POST", body: JSON.stringify(day ? { day } : {}) });
}

export interface SavingsLine {
  key: "queues" | "after_hours" | "deliveries" | "pos";
  count: number;
  unit_value: number;
  amount: number;
}

export interface Savings {
  month: string;
  lines: SavingsLine[];
  total: number;
  subscription: number;
  net: number;

  constants: Record<string, number>;
}

export function getSavings(month?: string): Promise<Savings> {
  return api<Savings>(`/api/savings${month ? `?month=${month}` : ""}`);
}

export interface SessionUser {
  id: string;
  email: string;
  full_name: string | null;
  role: "owner" | "admin" | "viewer";
  is_active: boolean;
}

export interface WhoAmI {
  kind: "user" | "api_key" | "legacy";
  tenant_id: string;

  tenant_slug: string;
  role: SessionUser["role"];
  user: SessionUser | null;
}

export const resetEverything = (confirm: string) =>
  api<{ status: string; files_removed: number; objects_removed: number }>("/api/reset", {
    method: "POST",
    body: JSON.stringify({ confirm }),
  });

export async function whoAmI(): Promise<WhoAmI | null> {
  try {
    return await api<WhoAmI>("/api/auth/me");
  } catch {
    return null;
  }
}

export interface SiteSummary {
  id: string;
  name: string;
  timezone: string;
  closing_time: string;
}

export const listSites = () => api<SiteSummary[]>("/api/sites");
