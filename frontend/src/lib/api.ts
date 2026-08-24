/**
 * Typed API client. Attaches the JWT, unwraps the backend's error envelope,
 * and supports both JSON and multipart (FormData) bodies.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "wastelens_access_token";

// crypto.randomUUID() only exists in secure contexts (HTTPS or localhost) —
// plain-HTTP deployments (e.g. an EC2 box without a domain/TLS yet) need a
// fallback or every call using it throws before the request is even sent.
export function generateUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

interface ErrorEnvelope {
  error: { code: string; message: string };
  request_id: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  // Let the browser set the multipart boundary itself for FormData bodies.
  if (!(init?.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const resp = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  if (!resp.ok) {
    let code = `http_${resp.status}`;
    let message = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as ErrorEnvelope;
      code = body.error.code;
      message = body.error.message;
    } catch {
      // non-JSON error body; keep defaults
    }
    throw new ApiError(resp.status, code, message);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// --- Domain types (mirror backend schemas) ---------------------------------

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

export interface StaffAccount {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "station_operator" | "reviewer" | "analyst" | "collector";
}

export interface Detection {
  id: string;
  item_name: string;
  subcategory: string | null;
  category: string | null;
  confidence: number;
  estimated_quantity: string | null;
  ocr_text: string | null;
  matched_brand_id: string | null;
  needs_review: boolean;
  review_status: string;
  corrected_item_name: string | null;
  brand_text: string | null;
  item_state: string | null;
  is_contaminant: boolean;
  count_est: number | null;
  bbox_x: number | null;
  bbox_y: number | null;
  bbox_w: number | null;
  bbox_h: number | null;
}

export interface InferenceRun {
  id: string;
  attempt_no: number;
  provider_name: string;
  model_name: string;
  status: string;
  latency_ms: number | null;
  overall_confidence: number | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Capture {
  id: string;
  session_id: string;
  bag_id: string;
  bag_type: string;
  station_id: string;
  captured_at: string;
  analysis_status: "pending" | "processing" | "done" | "failed";
  detections?: Detection[];
  /** Only present on the detail endpoint — a time-limited presigned URL, not
   * the raw S3 key. */
  image_url?: string;
  // --- Phase 4: upload-time provenance ---
  inspection_station_id?: string | null;
  tray_code?: string | null;
  lighting_condition?: string | null;
  image_sha256?: string | null;
  image_width?: number | null;
  image_height?: number | null;
  file_size_bytes?: number | null;
  /** Every vision-model attempt for this capture, oldest first as returned
   * — the station page sorts by attempt_no to show the repair-retry
   * history, not just the winning attempt. */
  inference_runs?: InferenceRun[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// --- Review workflow (Phase 3) -----------------------------------------

export type ReviewVerdict = "CONFIRMED" | "CORRECTED" | "REJECTED";

export interface ReviewQueueItem extends Detection {
  capture_id: string;
  capture_image_url: string;
  capture_bag_type: string;
  captured_at: string;
  queue_reason: string;
}

export interface ReviewerStat {
  reviewer_id: string;
  reviewer_email: string;
  reviewed_count: number;
  confirmed_count: number;
  corrected_count: number;
  rejected_count: number;
}

export interface ReviewStats {
  reviewed_today: number;
  agreement_rate: number;
  by_reviewer: ReviewerStat[];
}

export interface ReviewActionBody {
  verdict: ReviewVerdict;
  corrected_item_name?: string | null;
  corrected_brand_text?: string | null;
  corrected_count?: number | null;
  corrected_is_contaminant?: boolean | null;
  notes?: string | null;
  time_spent_seconds?: number | null;
}

export interface BulkReviewResult {
  reviewed: number;
  skipped: string[];
}

export interface VocabularyItem {
  id: string;
  bag_type: string;
  item_name: string;
  display_name: string;
  parent_category: string | null;
  parent_id: string | null;
  active: boolean;
  is_contaminant_by_default: boolean;
  is_sensitive: boolean;
  created_at: string;
}

export interface UnmappedLabel {
  id: string;
  raw_label: string;
  bag_type: string;
  occurrence_count: number;
  first_seen_at: string;
  last_seen_at: string;
  suggested_vocabulary_item_id: string | null;
  resolved: boolean;
}

// --- Phase 4: operations (pickups, collection sessions, stations, bins,
// collectors, calendar) ------------------------------------------------

export interface Bag {
  id: string;
  user_id: string;
  bag_type: string;
  tag_id: string;
  status: string;
  gross_weight_kg: string | null;
  tare_weight_kg: string | null;
  net_weight_kg: string | null;
  bag_condition: string | null;
  assigned_bin_id: string | null;
  collection_session_id: string | null;
}

export interface SessionBagInput {
  bag_type: string;
  tag_id?: string | null;
  gross_weight_kg?: string | null;
  tare_weight_kg?: string | null;
  bag_condition?: string | null;
}

export interface SessionCreateBody {
  user_id: string;
  collector_id?: string | null;
  vehicle_code?: string | null;
  route_code?: string | null;
  gps_latitude?: string | null;
  gps_longitude?: string | null;
  notes?: string | null;
  pickup_request_id?: string | null;
  bags: SessionBagInput[];
}

export interface SessionDetail {
  id: string;
  user_id: string;
  collected_at: string;
  collector_id: string | null;
  vehicle_code: string | null;
  route_code: string | null;
  gps_latitude: string | null;
  gps_longitude: string | null;
  warehouse_arrival_at: string | null;
  notes: string | null;
  bags: Bag[];
}

export interface Resident {
  id: string;
  name: string;
  phone: string;
  address: string;
  created_at: string;
}

export type PickupStatus = "REQUESTED" | "COMPLETED" | "CANCELLED" | "MISSED";

export interface PickupRequest {
  id: string;
  resident_id: string;
  requested_for_date: string;
  requested_window: string | null;
  requested_at: string;
  channel: string;
  declared_bag_count: number | null;
  status: PickupStatus;
  cancel_reason: string | null;
  collection_session_id: string | null;
}

export interface InspectionStation {
  id: string;
  station_code: string;
  facility_name: string;
  line_name: string | null;
  camera_identifier: string | null;
  default_lighting: string | null;
}

export interface Bin {
  id: string;
  bin_code: string;
  bin_type: string;
  location: string | null;
  capacity_kg: string | null;
  downstream_process: string | null;
  vendor_name: string | null;
}

export interface Collector {
  id: string;
  staff_account_id: string;
  employee_code: string;
  full_name: string;
  phone: string | null;
  default_vehicle_code: string | null;
  is_active: boolean;
}

export interface CalendarDay {
  calendar_date: string;
  day_of_week: number;
  is_weekend: boolean;
  is_poya: boolean;
  is_public_holiday: boolean;
  note: string | null;
}

// --- Calls ------------------------------------------------------------------

export function createStaffAccount(body: {
  email: string;
  full_name: string;
  password: string;
  role: string;
}) {
  return apiFetch<StaffAccount>("/auth/staff", { method: "POST", body: JSON.stringify(body) });
}

export async function login(email: string, password: string): Promise<StaffAccount> {
  const tokens = await apiFetch<TokenPair>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(tokens.access_token);
  return apiFetch<StaffAccount>("/auth/me");
}

export function uploadCapture(
  image: File,
  bagTagId: string,
  stationId: string,
  extra?: { inspectionStationId?: string; trayCode?: string; lightingCondition?: string },
) {
  const form = new FormData();
  form.append("image", image);
  form.append("bag_tag_id", bagTagId);
  form.append("station_id", stationId);
  if (extra?.inspectionStationId) form.append("inspection_station_id", extra.inspectionStationId);
  if (extra?.trayCode) form.append("tray_code", extra.trayCode);
  if (extra?.lightingCondition) form.append("lighting_condition", extra.lightingCondition);
  return apiFetch<Capture>("/captures", {
    method: "POST",
    body: form,
    headers: { "Idempotency-Key": generateUUID() },
  });
}

export function getCapture(id: string) {
  return apiFetch<Capture>(`/captures/${id}`);
}

// --- Phase 4: collector doorstep flow -----------------------------------

export function lookupResidentByPhone(phone: string) {
  return apiFetch<Resident>(`/users/by-phone/${encodeURIComponent(phone)}`);
}

export function lookupResidentByQr(qrCode: string) {
  return apiFetch<Resident>(`/users/by-qr/${encodeURIComponent(qrCode)}`);
}

export function createSession(body: SessionCreateBody) {
  return apiFetch<SessionDetail>("/sessions", { method: "POST", body: JSON.stringify(body) });
}

export function getSession(id: string) {
  return apiFetch<SessionDetail>(`/sessions/${id}`);
}

export function arriveSession(id: string, arrivedAt?: string) {
  return apiFetch<SessionDetail>(`/sessions/${id}/arrive`, {
    method: "PATCH",
    body: JSON.stringify(arrivedAt ? { arrived_at: arrivedAt } : {}),
  });
}

export function getBag(bagId: string) {
  return apiFetch<Bag>(`/bags/${bagId}`);
}

export function weighBag(
  bagId: string,
  body: { gross_weight_kg?: string; tare_weight_kg?: string; bag_condition?: string },
) {
  return apiFetch<Bag>(`/bags/${bagId}/weigh`, { method: "PATCH", body: JSON.stringify(body) });
}

// --- Phase 4: pickups ----------------------------------------------------

export function bookPickup(body: {
  user_id: string;
  requested_for_date: string;
  requested_window?: string;
  channel: string;
  declared_bag_count?: number;
}) {
  return apiFetch<PickupRequest>("/pickups", { method: "POST", body: JSON.stringify(body) });
}

export function listPickups(params?: { date?: string; status?: string }) {
  const qs = new URLSearchParams();
  if (params?.date) qs.set("date", params.date);
  if (params?.status) qs.set("status", params.status);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch<Page<PickupRequest>>(`/pickups${suffix}`);
}

export function cancelPickup(id: string, reason?: string) {
  return apiFetch<PickupRequest>(`/pickups/${id}/cancel`, {
    method: "PATCH",
    body: JSON.stringify({ reason }),
  });
}

export function missPickup(id: string) {
  return apiFetch<PickupRequest>(`/pickups/${id}/miss`, { method: "POST" });
}

// --- Phase 4: admin — stations, bins, collectors, calendar ---------------

export function listStations(limit = 200) {
  return apiFetch<Page<InspectionStation>>(`/stations?limit=${limit}`);
}

export function createStation(body: {
  station_code: string;
  facility_name: string;
  line_name?: string;
  camera_identifier?: string;
  default_lighting?: string;
}) {
  return apiFetch<InspectionStation>("/stations", { method: "POST", body: JSON.stringify(body) });
}

export function updateStation(id: string, body: Partial<InspectionStation>) {
  return apiFetch<InspectionStation>(`/stations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteStation(id: string) {
  return apiFetch<void>(`/stations/${id}`, { method: "DELETE" });
}

export function listBins(limit = 200) {
  return apiFetch<Page<Bin>>(`/bins?limit=${limit}`);
}

export function createBin(body: {
  bin_code: string;
  bin_type: string;
  location?: string;
  capacity_kg?: string;
  downstream_process?: string;
  vendor_name?: string;
}) {
  return apiFetch<Bin>("/bins", { method: "POST", body: JSON.stringify(body) });
}

export function updateBin(id: string, body: Partial<Bin>) {
  return apiFetch<Bin>(`/bins/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function transferBagToBin(body: {
  bag_id: string;
  bin_id: string;
  from_tray_code?: string;
  weight_kg?: string;
}) {
  return apiFetch<unknown>("/bins/transfer", { method: "POST", body: JSON.stringify(body) });
}

export function listCollectors(limit = 200) {
  return apiFetch<Page<Collector>>(`/collectors?limit=${limit}`);
}

export function createCollector(body: {
  staff_account_id: string;
  employee_code: string;
  full_name: string;
  phone?: string;
  default_vehicle_code?: string;
}) {
  return apiFetch<Collector>("/collectors", { method: "POST", body: JSON.stringify(body) });
}

export function updateCollector(id: string, body: Partial<Collector>) {
  return apiFetch<Collector>(`/collectors/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function listCalendarDays(year: number) {
  return apiFetch<Page<CalendarDay>>(`/calendar?year=${year}&limit=400`);
}

export function updateCalendarDay(
  date: string,
  body: { is_poya?: boolean; is_public_holiday?: boolean; note?: string },
) {
  return apiFetch<CalendarDay>(`/calendar/${date}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

// --- Review workflow ---------------------------------------------------

export function getReviewQueue(limit = 20, offset = 0) {
  return apiFetch<Page<ReviewQueueItem>>(`/review/queue?limit=${limit}&offset=${offset}`);
}

export function getReviewStats() {
  return apiFetch<ReviewStats>("/review/stats");
}

export function reviewDetection(detectionId: string, body: ReviewActionBody) {
  return apiFetch<Detection>(`/detections/${detectionId}/review`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function bulkReviewDetections(detectionIds: string[], verdict: ReviewVerdict = "CONFIRMED") {
  return apiFetch<BulkReviewResult>("/detections/bulk-review", {
    method: "POST",
    body: JSON.stringify({ detection_ids: detectionIds, verdict }),
  });
}

export function getVocabulary(bagType: string, activeOnly = true) {
  return apiFetch<Page<VocabularyItem>>(
    `/vocabulary?bag_type=${bagType}&active=${activeOnly}&limit=200`,
  );
}

export function listUnmappedLabels(resolved = false) {
  return apiFetch<Page<UnmappedLabel>>(`/vocabulary/unmapped?resolved=${resolved}&limit=100`);
}

export function promoteUnmappedLabel(id: string) {
  return apiFetch<VocabularyItem>(`/vocabulary/from-unmapped/${id}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}
