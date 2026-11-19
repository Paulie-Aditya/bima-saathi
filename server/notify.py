"""Real external integrations: Telegram Bot API and Google Calendar (F16).
Every message is also mirrored to the on-screen demo phone, which is the fallback if Telegram fails."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import config, db
from .events import hub

log = logging.getLogger("notify")


async def telegram(text: str, member_id: str | None, chat_id: str | None = None, label: str = "demo") -> bool:
    chat = chat_id or config.TELEGRAM_DEMO_CHAT_ID
    delivered = False
    if config.TELEGRAM_BOT_TOKEN and chat:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True})
                delivered = r.status_code == 200 and r.json().get("ok", False)
                if not delivered:
                    log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
        except Exception as e:  # network failures must never break a voice turn
            log.warning("telegram error: %s", e)
    db.run("INSERT INTO notifications(member_id, channel, text, delivered, ts) VALUES (?,?,?,?,?)",
           member_id, label, text, int(delivered), db.now_iso())
    await hub.emit(member_id, {"type": "phone", "channel": label, "text": text, "delivered": delivered})
    return delivered


async def telegram_desk(text: str, member_id: str | None) -> bool:
    return await telegram(text, member_id, chat_id=config.TELEGRAM_DESK_CHAT_ID or config.TELEGRAM_DEMO_CHAT_ID,
                          label="desk")


# ---- Google Calendar via a service account ----

_creds = None


def _google_creds():
    global _creds
    if _creds is not None:
        return _creds
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw or not config.GOOGLE_CALENDAR_ID:
        return None
    from google.oauth2 import service_account

    info = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(Path(raw).expanduser().read_text())
    _creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar.events"])
    return _creds


def _token() -> str | None:
    creds = _google_creds()
    if creds is None:
        return None
    from google.auth.transport.requests import Request

    if not creds.valid:
        creds.refresh(Request())
    return creds.token


async def calendar_event(summary: str, start: datetime, minutes: int, description: str, location: str) -> str | None:
    """Create an event and return its link, or None if Calendar is not configured or fails."""
    try:
        token = await asyncio.to_thread(_token)
        if not token:
            return None
        body = {
            "summary": summary, "description": description, "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": "Asia/Kolkata"},
        }
        cal = httpx.URL(f"https://www.googleapis.com/calendar/v3/calendars/{config.GOOGLE_CALENDAR_ID}/events")
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(cal, json=body, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 300:
            log.warning("calendar failed: %s %s", r.status_code, r.text[:300])
            return None
        return r.json().get("htmlLink")
    except Exception as e:
        log.warning("calendar error: %s", e)
        return None


# ---- reminder scheduler ----

async def reminder_loop() -> None:
    while True:
        try:
            await fire_due_reminders()
        except Exception as e:
            log.warning("reminder loop: %s", e)
        await asyncio.sleep(10)


async def fire_due_reminders(force_id: str | None = None) -> int:
    if force_id:
        due = db.all_("SELECT * FROM reminders WHERE id = ? AND sent = 0", force_id)
    else:
        due = db.all_("SELECT * FROM reminders WHERE sent = 0 AND due_at <= ?", db.now_iso())
    for r in due:
        db.run("UPDATE reminders SET sent = 1 WHERE id = ?", r["id"])
        await telegram(f"⏰ Bima Saathi reminder: {r['text']}", r["member_id"], label="reminder")
        await hub.emit(r["member_id"], {"type": "reminder", "id": r["id"], "status": "sent", "text": r["text"],
                                        "due_at": r["due_at"]})
    return len(due)
