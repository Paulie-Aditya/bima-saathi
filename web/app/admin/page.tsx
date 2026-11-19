"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DOC_LABELS } from "@/components/ui";

const STATUSES = ["SUBMITTED", "QUERY", "APPROVED", "SETTLED", "REJECTED"];
const FLAG_TEXT: Record<string, string> = {
  FEATURE_RAG: "Policy wording Q&A (search_policy_wording)",
  FEATURE_CALLBACK: "Proactive callback on claim status change",
  FEATURE_DOCS: "Document check with the vision model",
  FEATURE_KANNADA: "Kannada replies",
};

export default function Admin() {
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => api("/admin/state").then(setS).catch((e) => setMsg(String(e.message))), []);
  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [load]);

  const act = async (key: string, fn: () => Promise<any>, done: string) => {
    setBusy(key);
    try {
      await fn();
      setMsg(done);
      await load();
    } catch (e: any) {
      setMsg(String(e.message || e));
    } finally {
      setBusy(null);
    }
  };

  if (!s) return <main className="shell">{msg || "Loading…"}</main>;

  return (
    <main className="shell admin">
      <header className="topbar">
        <div className="brand">
          <div className="logo">ब</div>
          <div>
            <h1>Demo controls</h1>
            <p>Flip claims on stage, reset between runs</p>
          </div>
        </div>
        <div className="spacer" />
        <a className="ghost" href="/" style={{ textDecoration: "none" }}>
          ← Talk page
        </a>
        <button
          className="danger"
          disabled={busy === "reset"}
          onClick={() => act("reset", () => api("/admin/reset", {}), "Demo DB reset.")}
        >
          {busy === "reset" ? "Resetting…" : "Reset demo DB"}
        </button>
      </header>
      {msg ? <div className="banner" style={{ margin: "0 0 14px" }}>{msg}</div> : null}

      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <section className="panel">
          <div className="panel-h">
            <h2>Claims</h2>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Claim</th>
                  <th>Member · patient</th>
                  <th>Pending</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {s.claims.map((c: any) => (
                  <tr key={c.claim_id}>
                    <td>
                      <b>{c.claim_id}</b>
                      <div style={{ color: "var(--ink-3)", fontSize: 13 }}>{c.claim_type}</div>
                    </td>
                    <td>
                      {c.member}
                      <div style={{ color: "var(--ink-3)", fontSize: 13 }}>
                        {c.patient} · {c.procedure}
                      </div>
                    </td>
                    <td>{c.pending_docs.map((d: string) => DOC_LABELS[d] || d).join(", ") || "—"}</td>
                    <td>
                      <div className="seg">
                        {STATUSES.map((st) => (
                          <button
                            key={st}
                            className={c.status === st ? "on" : ""}
                            disabled={busy === c.claim_id}
                            onClick={() =>
                              act(c.claim_id, () => api(`/admin/claims/${c.claim_id}/status`, { status: st }), `${c.claim_id} → ${st}`)
                            }
                          >
                            {st.charAt(0) + st.slice(1).toLowerCase()}
                          </button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <section className="panel">
            <div className="panel-h">
              <h2>Today</h2>
            </div>
            <div className="panel-b stats">
              <div className="stat">
                <b>{s.stats.sessions_today}</b>
                <span>calls</span>
              </div>
              <div className="stat">
                <b>{s.stats.tool_calls}</b>
                <span>tool calls</span>
              </div>
              <div className="stat">
                <b>{s.stats.escalations}</b>
                <span>handoffs</span>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-h">
              <h2>MVP+ feature flags</h2>
            </div>
            <div className="panel-b">
              {Object.entries(s.flags as Record<string, boolean>).map(([name, on]) => (
                <label className="switch" key={name}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={(e) => act(name, () => api("/admin/flags", { name, on: e.target.checked }), `${name} ${e.target.checked ? "on" : "off"}`)}
                  />
                  <span>
                    {FLAG_TEXT[name] || name}
                    <div style={{ color: "var(--ink-3)", fontSize: 12 }}>{name}</div>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-h">
              <h2>Reminders</h2>
              <div className="spacer" />
              <button className="ghost" onClick={() => act("fire", () => api("/admin/reminders/fire", {}), "Pending reminders sent.")}>
                Send all now
              </button>
            </div>
            <div className="panel-b">
              {s.reminders.length === 0 ? <div style={{ color: "var(--ink-3)" }}>None yet.</div> : null}
              {s.reminders.map((r: any) => (
                <div key={r.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                  <b>{r.id}</b> · {new Date(r.due_at).toLocaleString("en-IN")} · {r.sent ? "sent" : "pending"}
                  <div style={{ color: "var(--ink-2)" }}>{r.text}</div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-h">
              <h2>Integrations</h2>
            </div>
            <div className="panel-b" style={{ fontSize: 14 }}>
              {["agora", "llm", "telegram", "calendar"].map((k) => (
                <div key={k} className="switch">
                  <span className={`dot ${s.integrations[k] ? "on" : "off"}`} /> {k}
                </div>
              ))}
              <div style={{ color: "var(--ink-3)", marginTop: 6 }}>Agora calls: {s.integrations.public_base_url}</div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
