"use client";

import { useEffect, useRef } from "react";
import type { Booking, Claim, PhoneMsg, Reminder, ToolEvent } from "@/lib/api";

export const TOOL_META: Record<string, { label: string; icon: string }> = {
  get_member_profile: { label: "Member profile", icon: "🪪" },
  check_coverage: { label: "Coverage check", icon: "🛡️" },
  estimate_out_of_pocket: { label: "Cost estimate", icon: "₹" },
  find_network_hospitals: { label: "Network hospitals", icon: "🏥" },
  book_appointment: { label: "Book appointment", icon: "📅" },
  initiate_preauth: { label: "Cashless pre-auth", icon: "🧾" },
  get_claim_status: { label: "Claim status", icon: "🔎" },
  send_document_link: { label: "Document upload link", icon: "📎" },
  set_reminder: { label: "Reminder", icon: "⏰" },
  send_summary: { label: "Call summary", icon: "💬" },
  escalate_to_human: { label: "Human handoff", icon: "🙋" },
  search_policy_wording: { label: "Policy wording", icon: "📜" },
  check_document: { label: "Document check", icon: "📄" },
};

export const DOC_LABELS: Record<string, string> = {
  discharge_summary: "Discharge summary",
  doctor_prescription: "Doctor's advice",
  investigation_reports: "Investigation reports",
  hospital_preauth_form: "Pre-auth form",
  original_bills: "Original bills",
  cancelled_cheque: "Cancelled cheque",
  id_proof: "ID proof",
  policy_card: "Policy e-card",
};

const STEPS = ["SUBMITTED", "QUERY", "APPROVED", "SETTLED"] as const;

function argChips(args: Record<string, unknown>) {
  return Object.entries(args)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .slice(0, 4)
    .map(([k, v]) => (
      <span className="chip" key={k}>
        {k.replace(/_/g, " ")}: {Array.isArray(v) ? v.join(", ") : String(v)}
      </span>
    ));
}

export function ActionCard({ e }: { e: ToolEvent }) {
  const meta = TOOL_META[e.name] || { label: e.name, icon: "⚙️" };
  const time = new Date(e.ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return (
    <div className={`card ${e.status}`}>
      <div className="ico" aria-hidden>
        {meta.icon}
      </div>
      <div>
        <h3>{meta.label}</h3>
        <div className="res">{e.status === "running" ? "Working…" : e.summary}</div>
        <div className="args">
          {argChips(e.args || {})}
          {e.confirmed_by ? <span className="chip yes">✓ said “{e.confirmed_by}”</span> : null}
        </div>
      </div>
      <div className="meta">
        {e.status === "running" ? (
          <span className="spinner" />
        ) : (
          <span className={`badge ${e.status}`}>{e.status === "confirm" ? "needs yes" : e.status}</span>
        )}
        <div style={{ marginTop: 6 }}>{time}</div>
        {e.ms !== undefined ? <div>{e.ms} ms</div> : null}
      </div>
    </div>
  );
}

export function ClaimCard({ c, flash }: { c: Claim; flash: boolean }) {
  const idx = STEPS.indexOf(c.status as (typeof STEPS)[number]);
  const rejected = c.status === "REJECTED";
  return (
    <div className={`claim ${flash ? "flash" : ""}`}>
      <div className="claim-top">
        <span className="claim-id">{c.claim_id}</span>
        <span className="chip">{c.claim_type === "cashless" ? "Cashless" : "Reimbursement"}</span>
      </div>
      <div className="claim-sub">
        {c.patient} · {c.procedure}
        <br />
        {c.hospital}
      </div>
      <div className="steps">
        {STEPS.map((s, i) => {
          let cls = "step";
          if (rejected) cls += i === 0 ? " past" : i === 1 ? " now rejected" : "";
          else if (i < idx) cls += " past";
          else if (i === idx) cls += ` now ${s === "QUERY" ? "query" : ""}`;
          return (
            <div className={cls} key={s}>
              {rejected && i === 1 ? "Rejected" : s.charAt(0) + s.slice(1).toLowerCase()}
            </div>
          );
        })}
      </div>
      {c.pending_docs.length > 0 ? (
        <div className="pending">
          Pending:
          {c.pending_docs.map((d) => (
            <span className="chip" key={d}>
              {DOC_LABELS[d] || d}
            </span>
          ))}
        </div>
      ) : c.note ? (
        <div className="claim-sub">{c.note}</div>
      ) : null}
    </div>
  );
}

export function SideList({ bookings, reminders }: { bookings: Booking[]; reminders: Reminder[] }) {
  if (!bookings.length && !reminders.length) return null;
  const fmt = (s: string) =>
    new Date(s).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  return (
    <div className="side-list">
      {bookings.map((b) => (
        <span className="chip" key={b.id}>
          📅 {b.id}: {b.hospital.split(",")[0]} · {fmt(b.start_at)}{" "}
          {b.calendar_link ? (
            <a href={b.calendar_link} target="_blank" rel="noreferrer">
              Calendar
            </a>
          ) : null}
        </span>
      ))}
      {reminders.map((r) => (
        <span className="chip" key={r.id}>
          ⏰ {fmt(r.due_at)} · {r.text} {r.sent ? "✓ sent" : ""}
        </span>
      ))}
    </div>
  );
}

export function Phone({ msgs }: { msgs: PhoneMsg[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [msgs.length]);
  const ordered = [...msgs].reverse();
  return (
    <div className="phone" aria-label="Demo phone">
      <div className="phone-top">
        <div className="av">B</div>
        <div>
          <b>Bima Saathi</b>
          <small>Telegram · bot</small>
        </div>
      </div>
      <div className="phone-body" ref={ref}>
        {ordered.length === 0 ? <div className="empty">Messages to the member's phone appear here.</div> : null}
        {ordered.map((m, i) => (
          <div className="msg" key={i}>
            <span className={`lbl ${m.channel === "desk" ? "desk" : ""}`}>
              {m.channel === "desk" ? "Claims desk group" : m.channel}
            </span>
            {m.text}
            <span className="t">{m.delivered ? "✓✓ Telegram" : "on-screen only"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Two-tone ring using WebAudio, so no audio file is needed. */
function useRingtone(on: boolean) {
  useEffect(() => {
    if (!on) return;
    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
    } catch {
      return;
    }
    const ring = () => {
      if (!ctx) return;
      [0, 0.4].forEach((t) => {
        const o = ctx!.createOscillator();
        const g = ctx!.createGain();
        o.frequency.value = t ? 480 : 440;
        g.gain.setValueAtTime(0.0001, ctx!.currentTime + t);
        g.gain.exponentialRampToValueAtTime(0.2, ctx!.currentTime + t + 0.05);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx!.currentTime + t + 0.35);
        o.connect(g).connect(ctx!.destination);
        o.start(ctx!.currentTime + t);
        o.stop(ctx!.currentTime + t + 0.4);
      });
    };
    ring();
    const id = setInterval(ring, 1800);
    return () => {
      clearInterval(id);
      ctx?.close();
    };
  }, [on]);
}

export function IncomingCall({
  claimId,
  status,
  onAccept,
  onDecline,
}: {
  claimId: string;
  status: string;
  onAccept: () => void;
  onDecline: () => void;
}) {
  useRingtone(true);
  return (
    <div className="overlay" role="dialog" aria-modal="true">
      <div className="modal">
        <div className="call-ico">📞</div>
        <h3>Bima Saathi is calling</h3>
        <p>
          Update on claim {claimId}: {status.charAt(0) + status.slice(1).toLowerCase()}
        </p>
        <div className="btns">
          <button className="btn decline" onClick={onDecline}>
            Decline
          </button>
          <button className="btn accept" onClick={onAccept}>
            Answer
          </button>
        </div>
      </div>
    </div>
  );
}

export function OtpModal({ phoneLast4, onDone }: { phoneLast4: string; onDone: () => void }) {
  return (
    <div className="overlay" role="dialog" aria-modal="true">
      <form
        className="modal"
        onSubmit={(e) => {
          e.preventDefault();
          const v = (e.currentTarget.elements.namedItem("otp") as HTMLInputElement).value;
          if (/^\d{4}$/.test(v)) onDone();
        }}
      >
        <h3>Verify it's you</h3>
        <p>We sent a 4-digit OTP to ••••{phoneLast4}. (Demo: any 4 digits.)</p>
        <input className="otp" name="otp" inputMode="numeric" maxLength={4} autoFocus defaultValue="" placeholder="____" />
        <div className="btns">
          <button className="btn primary" type="submit">
            Verify
          </button>
        </div>
      </form>
    </div>
  );
}
