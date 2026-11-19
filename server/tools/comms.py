"""Reminders, call summary and human escalation."""
from __future__ import annotations

import json
from datetime import timedelta

from .. import db, memory, notify
from ..events import hub
from .common import Ctx, fmt_dt, parse_iso, parse_when


async def set_reminder(ctx: Ctx, when: str, text: str) -> dict:
    due, _ = parse_when(when, default_hour=10)
    if due < db.now():
        due = db.now() + timedelta(minutes=1)
    rid = db.next_id("RMD", "reminders")
    db.run("INSERT INTO reminders VALUES (?,?,?,?,?,?)", rid, ctx.member_id, due.isoformat(timespec="seconds"),
           text, 0, db.now_iso())
    await hub.emit(ctx.member_id, {"type": "reminder", "id": rid, "status": "scheduled", "text": text,
                                   "due_at": due.isoformat()}, ctx.session_id)
    return {"reminder_id": rid, "when": fmt_dt(due), "text": text, "channel": "telegram",
            "summary": f"Reminder {rid} · {fmt_dt(due)}"}


def compose_summary(member_id: str, session_id: str) -> str:
    m = db.one("SELECT * FROM members WHERE id = ?", member_id)
    hindi = (memory.get(member_id, "language") or m["preferred_language"]) == "hi"
    events = db.all_("SELECT payload FROM events WHERE session_id = ? AND type IN ('booking','claim','reminder','tool') "
                     "ORDER BY id", session_id)
    lines = [f"🩺 Bima Saathi – {'कॉल सारांश' if hindi else 'call summary'} ({db.now().strftime('%d %b')})",
             f"{'नमस्ते' if hindi else 'Namaste'} {memory.first_name(m['name'])}{' जी' if hindi else ''},"]
    seen = set()
    for row in events:
        e = json.loads(row["payload"])
        key = (e["type"], e.get("id") or e.get("claim_id") or e.get("name"))
        if e["type"] == "booking" and key not in seen:
            lines.append(f"🏥 Appointment: {e['hospital']}, {fmt_dt(parse_iso(e['start_at']))} ({e['id']})")
        elif e["type"] == "claim" and e["claim_id"].startswith("PA-") and key not in seen:
            lines.append(f"🧾 Cashless pre-auth {e['claim_id']}: {e['status'].title()}")
            if e["pending_docs"]:
                from .claims import DOC_LABELS
                lines.append("📋 Documents to give the hospital desk:")
                lines += [f"   ☐ {DOC_LABELS.get(d, d)}" for d in e["pending_docs"]]
        elif e["type"] == "reminder" and e.get("status") == "scheduled" and key not in seen:
            lines.append(f"⏰ Reminder: {fmt_dt(parse_iso(e['due_at']))} – {e['text']}")
        elif e["type"] == "tool" and e.get("name") == "check_coverage" and e.get("status") == "done" and key not in seen:
            lines.append(f"✅ Coverage: {e.get('summary')}")
        seen.add(key)
    if len(lines) == 2:
        for c in memory.open_claims(member_id):
            lines.append(f"🧾 {c['id']}: {c['status'].title()}" + (f" – pending {', '.join(c['pending_docs'])}" if c["pending_docs"] else ""))
    lines.append("Koi sawaal ho to Bima Saathi ko call kijiye. 🙏" if not hindi else "कोई सवाल हो तो बीमा साथी को कॉल कीजिए। 🙏")
    return "\n".join(lines)


async def send_summary(ctx: Ctx) -> dict:
    text = compose_summary(ctx.member_id, ctx.session_id)
    sent = await notify.telegram(text, ctx.member_id, label="summary")
    db.run("UPDATE sessions SET summary_sent = 1 WHERE id = ?", ctx.session_id)
    return {"sent": True, "via": "telegram" if sent else "demo phone", "message": text,
            "summary": "Summary sent" + (" on Telegram ✓" if sent else " (demo phone)")}


def _callback_slot(requested: str | None):
    if requested:
        return parse_when(requested, default_hour=11)[0]
    now = db.now()
    slot = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if slot.hour >= 19 or slot.hour < 9:
        slot = (slot + timedelta(days=1 if slot.hour >= 19 else 0)).replace(hour=10)
    return slot


async def escalate_to_human(ctx: Ctx, reason: str, claim_id: str | None = None, callback_time: str | None = None) -> dict:
    tid = db.next_id("TKT", "tickets")
    slot = _callback_slot(callback_time)
    db.run("INSERT INTO tickets VALUES (?,?,?,?,?,?)", tid, ctx.member_id, claim_id, reason,
           slot.isoformat(timespec="seconds"), db.now_iso())
    m = ctx.member
    desk = (f"🚩 Escalation {tid}\nMember: {m['name']} ({m['id']}), {memory.mask_policy(m['policy_id'])}\n"
            f"Claim: {claim_id or '-'}\nReason: {reason}\nCallback: {fmt_dt(slot)}\n"
            f"Language: {ctx.language}")
    sent = await notify.telegram_desk(desk, ctx.member_id)
    await hub.emit(ctx.member_id, {"type": "ticket", "id": tid, "reason": reason, "callback_at": slot.isoformat()},
                   ctx.session_id)
    return {"ticket_id": tid, "callback_at": fmt_dt(slot), "desk_notified": sent,
            "summary": f"Ticket {tid} · callback {fmt_dt(slot)}" + (" · desk ✓" if sent else "")}
