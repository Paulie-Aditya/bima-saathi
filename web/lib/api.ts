export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000").replace(/\/$/, "");
export const WS_BASE = API_BASE.replace(/^http/, "ws");

export async function api<T = any>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: body === undefined || body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data as T;
}

export type Member = { id: string; name: string; language: string; phone_last4: string };

export type Claim = {
  claim_id: string;
  claim_type: "cashless" | "reimbursement";
  status: "SUBMITTED" | "QUERY" | "APPROVED" | "SETTLED" | "REJECTED";
  patient: string | null;
  procedure: string | null;
  hospital: string | null;
  amount: number;
  pending_docs: string[];
  admission_date: string;
  updated_at: string;
  note: string;
};

export type ToolEvent = {
  type: "tool";
  id: string;
  name: string;
  status: "running" | "done" | "error" | "confirm";
  args: Record<string, unknown>;
  summary: string;
  confirmed_by?: string | null;
  ms?: number;
  ts: number;
};

export type PhoneMsg = { channel: string; text: string; delivered: number | boolean; ts: string | number };

export type Booking = { id: string; hospital: string; patient: string; start_at: string; calendar_link: string | null };
export type Reminder = { id: string; due_at: string; text: string; sent: number };

export type StartResp = {
  sessionId: string;
  mode: "voice" | "text";
  appId: string;
  channel: string;
  uid: number;
  token: string | null;
  greeting: string;
};
