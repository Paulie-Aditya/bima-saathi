"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Booking, type Claim, type Member, type PhoneMsg, type Reminder, type StartResp, type ToolEvent } from "@/lib/api";
import { joinVoice, type VoiceHandle } from "@/lib/agora";
import { subscribe, type ServerEvent } from "@/lib/events";
import { ActionCard, ClaimCard, IncomingCall, OtpModal, Phone, SideList } from "@/components/ui";

type Line = { role: "user" | "agent"; text: string; ts: number };
type Status = "idle" | "connecting" | "live" | "ending";

const STATUS_TEXT: Record<Status, string> = {
  idle: "Tap to talk",
  connecting: "Connecting…",
  live: "Listening",
  ending: "Ending call…",
};

function store(key: string, value?: string): string | null {
  try {
    if (value !== undefined) window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export default function Home() {
  const [members, setMembers] = useState<Member[]>([]);
  const [memberId, setMemberId] = useState<string>("");
  const [verified, setVerified] = useState<Record<string, boolean>>({});
  const [claims, setClaims] = useState<Claim[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [phone, setPhone] = useState<PhoneMsg[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [wsUp, setWsUp] = useState(false);

  const [status, setStatus] = useState<Status>("idle");
  const [session, setSession] = useState<StartResp | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [cards, setCards] = useState<ToolEvent[]>([]);
  const [agentLevel, setAgentLevel] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [incoming, setIncoming] = useState<{ claim_id: string; status: string } | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preferText, setPreferText] = useState(false);

  const voice = useRef<VoiceHandle | null>(null);
  const sessionRef = useRef<StartResp | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  sessionRef.current = session;

  const member = members.find((m) => m.id === memberId);

  // ---- data ----
  const load = useCallback(async (id: string) => {
    if (!id) return;
    const s = await api(`/api/state/${id}`);
    setClaims(s.claims);
    setBookings(s.bookings);
    setReminders(s.reminders);
    setPhone(s.phone);
    setHealth(s.integrations);
  }, []);

  useEffect(() => {
    api<Member[]>("/api/members")
      .then((ms) => {
        setMembers(ms);
        const saved = store("bima.member");
        setMemberId(saved && ms.some((m) => m.id === saved) ? saved : ms[0]?.id || "");
      })
      .catch(() => setError("Cannot reach the API server. Is it running on " + (process.env.NEXT_PUBLIC_API_BASE || "localhost:8000") + "?"));
  }, []);

  useEffect(() => {
    if (!memberId) return;
    store("bima.member", memberId);
    setLines([]);
    setCards([]);
    load(memberId).catch(() => {});
    return subscribe(memberId, onEvent, setWsUp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memberId]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [lines.length, typing]);

  // ---- events from the server ----
  const onEvent = (e: ServerEvent) => {
    const current = sessionRef.current?.sessionId;
    switch (e.type) {
      case "transcript":
        if (e.session_id && e.session_id !== current) return;
        if (e.role === "agent") setTyping(false);
        setLines((ls) =>
          ls.some((l) => l.role === e.role && l.text === e.text && Math.abs(l.ts - (e.ts || 0)) < 10)
            ? ls
            : [...ls, { role: e.role, text: e.text, ts: e.ts || Date.now() / 1000 }],
        );
        break;
      case "tool":
        setCards((cs) => {
          const i = cs.findIndex((c) => c.id === e.id);
          if (i === -1) return [e as ToolEvent, ...cs];
          const next = [...cs];
          next[i] = { ...next[i], ...(e as ToolEvent) };
          return next;
        });
        break;
      case "claim":
        setClaims((cs) => {
          const c = e as unknown as Claim;
          const i = cs.findIndex((x) => x.claim_id === c.claim_id);
          if (i === -1) return [c, ...cs];
          const next = [...cs];
          next[i] = { ...next[i], ...c };
          return next;
        });
        setFlash(e.claim_id);
        setTimeout(() => setFlash(null), 2500);
        break;
      case "booking":
      case "reminder":
        load(memberId).catch(() => {});
        break;
      case "phone":
        setPhone((p) => [{ channel: e.channel, text: e.text, delivered: e.delivered, ts: e.ts || 0 }, ...p]);
        break;
      case "incoming_call":
        if (!sessionRef.current) setIncoming({ claim_id: e.claim_id, status: e.status });
        break;
      case "session":
        if (e.status === "ended" && e.session_id === current) teardown();
        break;
      case "reset":
        setCards([]);
        setLines([]);
        load(memberId).catch(() => {});
        break;
    }
  };

  // ---- call control ----
  const teardown = async () => {
    const v = voice.current;
    voice.current = null;
    if (v) await v.leave().catch(() => {});
    setSession(null);
    setStatus("idle");
    setAgentLevel(0);
    setMicLevel(0);
    setTyping(false);
  };

  const start = async (opts?: { reason?: string; claim_id?: string }) => {
    if (!memberId || status !== "idle") return;
    setError(null);
    setStatus("connecting");
    setLines([]);
    setCards([]);
    try {
      const s = await api<StartResp>("/api/session/start", {
        member_id: memberId,
        mode: preferText ? "text" : "voice",
        ...opts,
      });
      setSession(s);
      sessionRef.current = s;
      // the server's greeting event can arrive before we know our session id, so seed it here
      setLines([{ role: "agent", text: s.greeting, ts: Date.now() / 1000 }]);
      if (s.mode === "voice") {
        voice.current = await joinVoice({
          appId: s.appId,
          channel: s.channel,
          token: s.token,
          uid: s.uid,
          onAgentLevel: setAgentLevel,
          onMicLevel: setMicLevel,
          onAgentJoined: () => {},
          onAgentLeft: () => {},
        });
      }
      setStatus("live");
    } catch (err: any) {
      setError(String(err?.message || err));
      await teardown();
    }
  };

  const stop = async () => {
    const s = sessionRef.current;
    if (!s) return;
    setStatus("ending");
    await api("/api/session/stop", { sessionId: s.sessionId }).catch(() => {});
    await teardown();
    setTimeout(() => load(memberId).catch(() => {}), 1500);
  };

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const s = sessionRef.current;
    if (!text.trim() || !s) return;
    const t = text.trim();
    setText("");
    setTyping(true);
    try {
      await api("/api/dev/chat", { sessionId: s.sessionId, text: t });
    } catch (err: any) {
      setError(String(err?.message || err));
    } finally {
      setTyping(false);
    }
  };

  const live = status === "live";
  const agentSpeaking = agentLevel > 2;
  const stateText = live ? (session?.mode === "text" ? "Text mode" : agentSpeaking ? "Bima Saathi is speaking" : "Listening…") : STATUS_TEXT[status];
  const ring = Math.min(1, Math.max(micLevel, agentLevel) / 40);

  const dots = [
    ["Agora", health?.agora],
    ["LLM", health?.llm],
    ["Telegram", health?.telegram],
    ["Calendar", health?.calendar],
    ["Live updates", wsUp],
  ] as const;

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="logo">ब</div>
          <div>
            <h1>Bima Saathi</h1>
            <p>Claim ka har sawaal, aapki bhasha mein</p>
          </div>
        </div>
        <div className="spacer" />
        {dots.map(([label, on]) => (
          <span className="pill" key={label} title={on ? "connected" : "not configured"}>
            <span className={`dot ${on ? "on" : "off"}`} />
            {label}
          </span>
        ))}
        <select
          className="member-select"
          value={memberId}
          disabled={status !== "idle"}
          onChange={(e) => setMemberId(e.target.value)}
          aria-label="Demo member"
        >
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} · {m.language}
            </option>
          ))}
        </select>
        <a className="ghost" href="/admin" style={{ textDecoration: "none" }}>
          Admin
        </a>
      </header>

      {error ? <div className="banner" style={{ margin: "0 0 14px" }}>{error}</div> : null}

      <div className="grid">
        <section className="panel">
          <div className="talk">
            <button
              className={`talk-btn ${live ? "live" : ""}`}
              onClick={() => (live ? stop() : start())}
              disabled={status === "connecting" || status === "ending" || !memberId}
              aria-label={live ? "End call" : "Start call"}
            >
              <span className="ring" style={{ opacity: ring, transform: `scale(${1 + ring * 0.12})` }} />
              {live ? "End" : status === "connecting" ? "…" : "Talk"}
            </button>
            <div className="talk-state">{stateText}</div>
            <div className="row">
              {live && session?.mode === "voice" ? (
                <button
                  className="ghost"
                  onClick={async () => {
                    await voice.current?.setMuted(!muted);
                    setMuted(!muted);
                  }}
                >
                  {muted ? "Unmute mic" : "Mute mic"}
                </button>
              ) : null}
              {!live ? (
                <label className="talk-sub" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input type="checkbox" checked={preferText} onChange={(e) => setPreferText(e.target.checked)} />
                  Type instead of speaking
                </label>
              ) : null}
            </div>
          </div>
          {live && session?.mode === "text" && !preferText ? (
            <div className="banner">Agora keys are not set on the server, so this call runs in text mode.</div>
          ) : null}
          <div className="panel-h">
            <h2>Live transcript</h2>
          </div>
          <div className="transcript" ref={transcriptRef}>
            {lines.length === 0 ? (
              <div className="empty">Say “Meri mummy ka knee replacement covered hai kya?”</div>
            ) : null}
            {lines.map((l, i) => (
              <div key={i} className={`bubble ${l.role}`}>
                <span className="who">{l.role === "agent" ? "Bima Saathi" : member?.name.split(" ")[0] || "You"}</span>
                {l.text}
              </div>
            ))}
            {typing ? (
              <div className="bubble agent">
                <span className="typing">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            ) : null}
          </div>
          {live && session?.mode === "text" ? (
            <form className="composer" onSubmit={send}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type in Hindi, English or Hinglish…" autoFocus />
              <button type="submit">Send</button>
            </form>
          ) : null}
        </section>

        <section className="panel">
          <div className="panel-h">
            <h2>What the agent is doing</h2>
          </div>
          <div className="cards">
            {cards.length === 0 ? <div className="empty">Each tool call shows up here as it runs.</div> : null}
            {cards.map((c) => (
              <ActionCard key={c.id} e={c} />
            ))}
          </div>
        </section>

        <section className="phone-col">
          <Phone msgs={phone} />
        </section>
      </div>

      <section className="panel tracker">
        <div className="panel-h">
          <h2>Claim tracker</h2>
        </div>
        <div className="claims">
          {claims.length === 0 ? <div className="empty">No claims yet.</div> : null}
          {claims.map((c) => (
            <ClaimCard key={c.claim_id} c={c} flash={flash === c.claim_id} />
          ))}
        </div>
        <SideList bookings={bookings} reminders={reminders} />
      </section>

      {member && !verified[member.id] ? (
        <OtpModal phoneLast4={member.phone_last4} onDone={() => setVerified((v) => ({ ...v, [member.id]: true }))} />
      ) : null}

      {incoming ? (
        <IncomingCall
          claimId={incoming.claim_id}
          status={incoming.status}
          onDecline={() => setIncoming(null)}
          onAccept={() => {
            const c = incoming;
            setIncoming(null);
            start({ reason: "claim_update", claim_id: c.claim_id });
          }}
        />
      ) : null}
    </main>
  );
}
