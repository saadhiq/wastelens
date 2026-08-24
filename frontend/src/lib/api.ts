/**
 * Typed API client. Attaches the JWT, unwraps the backend's error envelope,
 * and supports both JSON and multipart (FormData) bodies.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "wastelens_access_token";

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
  role: "admin" | "station_operator" | "reviewer" | "analyst";
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

export interface Capture {
  id: string;
  bag_type: string;
  station_id: string;
  captured_at: string;
  analysis_status: "pending" | "processing" | "done" | "failed";
  detections?: Detection[];
  /** Only present on the detail endpoint — a time-limited presigned URL, not
   * the raw S3 key. */
  image_url?: string;
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

// --- Calls ------------------------------------------------------------------

export async function login(email: string, password: string): Promise<StaffAccount> {
  const tokens = await apiFetch<TokenPair>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setToken(tokens.access_token);
  return apiFetch<StaffAccount>("/auth/me");
}

export function uploadCapture(image: File, bagTagId: string, stationId: string) {
  const form = new FormData();
  form.append("image", image);
  form.append("bag_tag_id", bagTagId);
  form.append("station_id", stationId);
  return apiFetch<Capture>("/captures", {
    method: "POST",
    body: form,
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
}

export function getCapture(id: string) {
  return apiFetch<Capture>(`/captures/${id}`);
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
