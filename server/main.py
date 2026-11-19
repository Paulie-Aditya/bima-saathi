"""Bima Saathi API. Run: uvicorn server.main:app --port 8000"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import agora, config, db, llm, memory, notify, seed
from .events import hub
from .tools import claims as claim_tools
from .tools import comms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

TEXT_HISTORY: dict[str, list[dict]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.DB_PATH.exists():
        log.info("seeding demo DB: %s", seed.reset())
    db.init()
    task = asyncio.create_task(notify.reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="Bima Saathi", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True, "agora": config.agora_ready(), "llm": config.llm_ready(),
            "telegram": bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_DEMO_CHAT_ID),
            "calendar": bool(config.GOOGLE_SERVICE_ACCOUNT_JSON and config.GOOGLE_CALENDAR_ID),
            "public_base_url": config.PUBLIC_BASE_URL, "flags": db.flags()}


# ---------------- demo login + page state ----------------

@app.get("/api/members")
def members():
    return [{"id": m["id"], "name": m["name"], "language": m["preferred_language"], "phone_last4": m["phone"][-4:]}
            for m in db.all_("SELECT * FROM members ORDER BY id")]


@app.get("/api/state/{member_id}")
def state(member_id: str):
    m = db.one("SELECT * FROM members WHERE id = ?", member_id)
    if not m:
        raise HTTPException(404, "unknown member")
    cls = db.all_("SELECT * FROM claims WHERE member_id = ? ORDER BY updated_at DESC", member_id)
    bookings = db.all_("SELECT b.*, h.name AS hospital, ip.name AS patient FROM bookings b JOIN hospitals h ON h.id = b.hospital_id "
                       "JOIN insured_persons ip ON ip.id = b.insured_person_id WHERE b.member_id = ? ORDER BY b.start_at", member_id)
    return {
        "member": {"id": m["id"], "name": m["name"], "language": memory.get(member_id, "language") or m["preferred_language"],
                   "policy": memory.mask_policy(m["policy_id"])},
        "claims": [claim_tools.claim_view(c) for c in cls],
        "bookings": bookings,
        "reminders": db.all_("SELECT * FROM reminders WHERE member_id = ? ORDER BY due_at DESC LIMIT 5", member_id),
        "phone": db.all_("SELECT channel, text, delivered, ts FROM notifications WHERE member_id = ? OR channel = 'desk' "
                         "ORDER BY id DESC LIMIT 20", member_id),
        "flags": db.flags(),
        "integrations": health(),
    }


# ---------------- sessions ----------------

class StartReq(BaseModel):
    member_id: str
    mode: str = "voice"  # voice | text
    reason: str | None = None  # e.g. claim_update
    claim_id: str | None = None


STATUS_HI = {"SUBMITTED": "submitted है", "QUERY": "query में है", "APPROVED": "approve हो गया है", "SETTLED": "settle हो गया है",
             "REJECTED": "reject हुआ है"}
STATUS_HINGLISH = {"SUBMITTED": "submitted hai", "QUERY": "query mein hai", "APPROVED": "approve ho gaya hai",
                   "SETTLED": "settle ho gaya hai", "REJECTED": "reject hua hai"}
REL_HI = {"father": "पापा", "mother": "मम्मी", "self": "", "spouse": "पत्नी"}


def greeting(member: dict, lang: str, claim: dict | None) -> str:
    first = memory.first_name(member["name"])
    if claim:
        rel = claim.get("relation", "")
        if lang == "hi":
            who = f"आपके {REL_HI.get(rel, '')} के " if REL_HI.get(rel) else "आपके "
            g = f"नमस्ते {first} जी, बीमा साथी बोल रही हूँ। {who}claim {claim['id']} में update है: claim {STATUS_HI.get(claim['status'], claim['status'])}।"
            if claim["pending_docs"]:
                g += " बस एक document बाकी है। बताऊँ?"
            return g
        who = {"father": "papa ke ", "mother": "mummy ke "}.get(rel, "aapke ")
        return (f"Namaste {first}, Bima Saathi bol rahi hoon. {who}claim {claim['id']} mein update hai: "
                f"claim {STATUS_HINGLISH.get(claim['status'], claim['status'])}. Details bataun?")
    return {
        "hi": f"नमस्ते {first} जी! मैं बीमा साथी। आज मैं आपकी क्या मदद करूँ?",
        "en": f"Hello {first}! I'm Bima Saathi. How can I help you today?",
        "kn": f"ನಮಸ್ಕಾರ {first}! ನಾನು ಬಿಮಾ ಸಾಥಿ. ಇವತ್ತು ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
    }.get(lang, f"Namaste {first}! Main Bima Saathi. Aaj kaise help karun?")


@app.post("/api/session/start")
async def session_start(req: StartReq):
    m = db.one("SELECT * FROM members WHERE id = ?", req.member_id)
    if not m:
        raise HTTPException(404, "unknown member")
    # close any session the page left open
    for s in db.all_("SELECT * FROM sessions WHERE member_id = ? AND ended_at IS NULL", req.member_id):
        asyncio.create_task(_finish_session(s))

    sid = uuid.uuid4().hex[:10]
    lang = memory.get(req.member_id, "language") or m["preferred_language"]
    claim = None
    reason = None
    if req.reason == "claim_update" and req.claim_id:
        c = db.one("SELECT c.*, ip.relation FROM claims c JOIN insured_persons ip ON ip.id = c.insured_person_id WHERE c.id = ?",
                   req.claim_id)
        if c:
            claim = c
            reason = (f"Outbound callback from Bima Saathi because claim {c['id']} changed to {c['status']}. "
                      f"You already told them the new status in the greeting; answer follow-ups with get_claim_status.")
    g = greeting(m, lang, claim)
    mode = "voice" if req.mode == "voice" and config.agora_ready() else "text"
    channel = f"bima-{sid}"
    uid = random.randint(10000, 99999)
    agent_id = None
    if mode == "voice":
        try:
            res = await agora.join(agora.join_body(sid, channel, uid, g, lang))
            agent_id = res.get("agent_id")
        except Exception as e:
            log.error("agora join failed: %s", e)
            raise HTTPException(502, str(e))
    db.run("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", sid, req.member_id, channel, agent_id, mode, reason,
           db.now_iso(), None, 0)
    TEXT_HISTORY[sid] = [{"role": "assistant", "content": g}]
    await hub.emit(req.member_id, {"type": "session", "status": "started", "mode": mode}, sid)
    await hub.emit(req.member_id, {"type": "transcript", "role": "agent", "text": g}, sid)
    return {"sessionId": sid, "mode": mode, "appId": config.AGORA_APP_ID, "channel": channel, "uid": uid,
            "token": agora.build_token(channel, uid) if mode == "voice" else None, "greeting": g}


class StopReq(BaseModel):
    sessionId: str


async def _finish_session(s: dict) -> None:
    if s["ended_at"]:
        return
    db.run("UPDATE sessions SET ended_at = ? WHERE id = ?", db.now_iso(), s["id"])
    if s["agent_id"]:
        try:
            await agora.leave(s["agent_id"])
        except Exception as e:
            log.warning("agora leave failed: %s", e)
    await hub.emit(s["member_id"], {"type": "session", "status": "ended"}, s["id"])
    summary = await llm.summarise_call(s["id"], s["member_id"])
    if summary:
        memory.put(s["member_id"], "last_call_summary", summary)
    did_work = db.one("SELECT COUNT(*) n FROM events WHERE session_id = ? AND type IN ('booking','claim','reminder','ticket')",
                      s["id"])["n"]
    fresh = db.one("SELECT summary_sent FROM sessions WHERE id = ?", s["id"])
    if did_work and not fresh["summary_sent"]:
        from .tools.common import Ctx
        await comms.send_summary(Ctx(session_id=s["id"], member_id=s["member_id"]))
    TEXT_HISTORY.pop(s["id"], None)


@app.post("/api/session/stop")
async def session_stop(req: StopReq):
    s = db.one("SELECT * FROM sessions WHERE id = ?", req.sessionId)
    if not s:
        raise HTTPException(404, "unknown session")
    asyncio.create_task(_finish_session(s))
    return {"ok": True}


class DevChat(BaseModel):
    sessionId: str
    text: str


@app.post("/api/dev/chat")
async def dev_chat(req: DevChat):
    """Typed turn through the exact same tool loop. Used by the text fallback and scripts/matrix.py."""
    s = db.one("SELECT * FROM sessions WHERE id = ?", req.sessionId)
    if not s:
        raise HTTPException(404, "unknown session")
    hist = TEXT_HISTORY.setdefault(req.sessionId, [])
    hist.append({"role": "user", "content": req.text})
    reply = await llm.complete(s, hist[-20:])
    hist.append({"role": "assistant", "content": reply})
    tools = [json.loads(r["payload"]) for r in db.all_(
        "SELECT payload FROM events WHERE session_id = ? AND type = 'tool' ORDER BY id DESC LIMIT 10", req.sessionId)]
    return {"reply": reply, "recent_tools": tools}


# ---------------- the endpoint Agora calls ----------------

def _check_auth(authorization: str | None) -> None:
    if config.LLM_SHARED_SECRET and authorization != f"Bearer {config.LLM_SHARED_SECRET}":
        raise HTTPException(401, "bad api key")


async def _chat(request: Request, session_id: str | None, authorization: str | None, member_hint: str | None):
    _check_auth(authorization)
    body = await request.json()
    s = llm.resolve_session(session_id or request.headers.get("x-session-id"), member_hint)
    if not s:
        raise HTTPException(400, "no active session")
    model = body.get("model") or config.LLM_MODEL
    if body.get("stream", True):
        return StreamingResponse(llm.sse(s, body.get("messages", []), model), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    text = await llm.complete(s, body.get("messages", []))
    return {"id": f"chatcmpl-{uuid.uuid4().hex[:12]}", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


@app.post("/chat/completions")
async def chat_plain(request: Request, authorization: str | None = Header(None), x_member_id: str | None = Header(None)):
    return await _chat(request, None, authorization, x_member_id)


@app.post("/s/{session_id}/chat/completions")
async def chat_session(session_id: str, request: Request, authorization: str | None = Header(None)):
    return await _chat(request, session_id, authorization, None)


# ---------------- websocket ----------------

@app.websocket("/ws/{member_id}")
async def ws(websocket: WebSocket, member_id: str):
    await websocket.accept()
    await hub.join(member_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # pings
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(member_id, websocket)


@app.get("/api/events/{session_id}")
def replay(session_id: str):
    """Event replay for the backup video / Plan D."""
    return [json.loads(r["payload"]) for r in db.all_("SELECT payload FROM events WHERE session_id = ? ORDER BY id", session_id)]


@app.get("/api/sessions")
def sessions(limit: int = 20):
    return db.all_("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", limit)


# ---------------- admin (demo controls) ----------------

class StatusReq(BaseModel):
    status: str
    pending_docs: list[str] | None = None
    note: str | None = None


@app.get("/admin/state")
def admin_state():
    cls = db.all_("SELECT * FROM claims ORDER BY updated_at DESC")
    member_names = {m["id"]: m["name"] for m in db.all_("SELECT id, name FROM members")}
    stats = {
        "sessions_today": db.one("SELECT COUNT(*) n FROM sessions WHERE started_at >= ?", db.now().date().isoformat())["n"],
        "tool_calls": db.one("SELECT COUNT(*) n FROM events WHERE type = 'tool' AND payload LIKE '%\"status\": \"done\"%'")["n"],
        "escalations": db.one("SELECT COUNT(*) n FROM tickets")["n"],
    }
    return {"claims": [{**claim_tools.claim_view(c), "member_id": c["member_id"], "member": member_names.get(c["member_id"])}
                       for c in cls],
            "reminders": db.all_("SELECT * FROM reminders ORDER BY due_at DESC LIMIT 10"),
            "tickets": db.all_("SELECT * FROM tickets ORDER BY created_at DESC LIMIT 10"),
            "flags": db.flags(), "stats": stats, "integrations": health()}


@app.post("/admin/claims/{claim_id}/status")
async def admin_status(claim_id: str, req: StatusReq):
    c = db.one("SELECT * FROM claims WHERE id = ?", claim_id)
    if not c:
        raise HTTPException(404, "unknown claim")
    status = req.status.upper()
    pending = req.pending_docs
    if pending is None:
        pending = [] if status in ("APPROVED", "SETTLED") else (c["pending_docs"] or (["discharge_summary"] if status == "QUERY" else []))
    db.run("UPDATE claims SET status = ?, pending_docs = ?, note = ?, updated_at = ? WHERE id = ?", status,
           json.dumps(pending), req.note or c["note"], db.now_iso(), claim_id)
    await claim_tools.emit_claim(c["member_id"], claim_id)
    await notify.telegram(f"🔔 Bima Saathi: claim {claim_id} ka status ab {status.title()} hai.", c["member_id"], label="status")
    if db.flag("FEATURE_CALLBACK"):
        await hub.emit(c["member_id"], {"type": "incoming_call", "reason": "claim_status_changed", "claim_id": claim_id,
                                        "status": status})
    return {"ok": True, "status": status, "pending_docs": pending}


@app.post("/admin/reset")
async def admin_reset():
    TEXT_HISTORY.clear()
    llm._tool_log.clear()
    counts = seed.reset()
    await hub.emit("*", {"type": "reset"})
    for m in db.all_("SELECT id FROM members"):
        await hub.emit(m["id"], {"type": "reset"})
    return {"ok": True, "counts": counts}


class FlagReq(BaseModel):
    name: str
    on: bool


@app.post("/admin/flags")
def admin_flags(req: FlagReq):
    if req.name not in config.FLAG_NAMES:
        raise HTTPException(400, "unknown flag")
    db.set_flag(req.name, req.on)
    return db.flags()


@app.post("/admin/reminders/fire")
async def admin_fire(reminder_id: str | None = None):
    if reminder_id:
        return {"fired": await notify.fire_due_reminders(force_id=reminder_id)}
    n = 0
    for r in db.all_("SELECT id FROM reminders WHERE sent = 0"):
        n += await notify.fire_due_reminders(force_id=r["id"])
    return {"fired": n}


# ---------------- documents (MVP+) ----------------

UPLOADS = config.DATA_DIR / "uploads"


async def _store_document(claim: dict, data: bytes, filename: str, mime: str, via: str) -> dict:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    did = f"DOC-{uuid.uuid4().hex[:6]}"
    ext = Path(filename).suffix or (".pdf" if mime == "application/pdf" else ".jpg")
    stem = "".join(ch for ch in Path(filename).stem if ch.isalnum() or ch in "-_")[:40] or "doc"
    path = UPLOADS / f"{claim['id']}-{did}-{stem}{ext}"
    path.write_bytes(data)
    db.run("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", did, claim["id"], str(path), mime, None, None, db.now_iso())
    if not db.flag("FEATURE_DOCS"):
        return {"stored": True, "document_id": did, "summary": "Document received"}
    card = {"type": "tool", "id": did, "name": "check_document", "args": {"claim_id": claim["id"], "via": via}}
    await hub.emit(claim["member_id"], {**card, "status": "running", "summary": ""})
    d = db.one("SELECT * FROM documents WHERE id = ?", did)
    result = await claim_tools.apply_document(claim, d)
    await hub.emit(claim["member_id"], {**card, "status": "done", "summary": result["summary"]})
    return {"stored": True, "document_id": did, **result}


@app.get("/api/claims/{claim_id}")
def claim_public(claim_id: str):
    c = db.one("SELECT * FROM claims WHERE id = ?", claim_id.upper())
    if not c:
        raise HTTPException(404, "unknown claim")
    v = claim_tools.claim_view(c)
    v["pending_labels"] = [claim_tools.DOC_LABELS.get(d, d) for d in c["pending_docs"]]
    return v


@app.post("/api/claims/{claim_id}/documents")
async def upload_document(claim_id: str, file: UploadFile = File(...)):
    c = db.one("SELECT * FROM claims WHERE id = ?", claim_id.upper())
    if not c:
        raise HTTPException(404, "unknown claim")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large")
    return await _store_document(c, data, file.filename or "upload.jpg", file.content_type or "image/jpeg", "web")


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    msg = update.get("message") or {}
    file_id, mime, name = None, "image/jpeg", "photo.jpg"
    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]
    elif msg.get("document"):
        file_id = msg["document"]["file_id"]
        mime = msg["document"].get("mime_type", "application/octet-stream")
        name = msg["document"].get("file_name", "document")
    if not file_id or not config.TELEGRAM_BOT_TOKEN:
        return {"ok": True}
    caption = (msg.get("caption") or "").upper()
    claim = next((db.one("SELECT * FROM claims WHERE id = ?", w.strip(".,")) for w in caption.split()
                  if w.startswith(("CLM-", "PA-"))), None)
    if not claim:
        last = db.one("SELECT member_id FROM sessions ORDER BY started_at DESC LIMIT 1")
        if last:
            claim = db.one("SELECT * FROM claims WHERE member_id = ? AND status = 'QUERY' ORDER BY updated_at DESC LIMIT 1",
                           last["member_id"])
    if not claim:
        return {"ok": True}
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    async with httpx.AsyncClient(timeout=15) as client:
        info = (await client.get(f"{base}/getFile", params={"file_id": file_id})).json()
        path = info["result"]["file_path"]
        data = (await client.get(f"https://api.telegram.org/file/bot{config.TELEGRAM_BOT_TOKEN}/{path}")).content
    result = await _store_document(claim, data, name, mime, "telegram")
    reply = f"✅ {claim['id']}: {result.get('summary', 'document received')}"
    await notify.telegram(reply, claim["member_id"], chat_id=str(msg["chat"]["id"]), label="doc-check")
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exc(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
