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
}

export interface Capture {
  id: string;
  bag_type: string;
  station_id: string;
  captured_at: string;
  analysis_status: "pending" | "processing" | "done" | "failed";
  detections?: Detection[];
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
