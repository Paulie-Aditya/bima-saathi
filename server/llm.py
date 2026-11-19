"""OpenAI-compatible /chat/completions that Agora ConvoAI calls each turn.

Per turn: load memory -> call the model with tools (streaming) -> run tools, emit action cards ->
loop until the model answers in text (max MAX_TOOL_ROUNDS) -> stream the text back as SSE."""
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from typing import AsyncIterator

from openai import APIError, AsyncOpenAI

from . import config, db, memory
from .events import hub
from .tools import registry
from .tools.common import Ctx

log = logging.getLogger("llm")

SYSTEM_PROMPT = """You are Bima Saathi, a voice assistant for health insurance members in India.
Speak like a helpful claims-desk executive: warm, brief, clear. You are female ("karti hoon").

Language: reply in the language the user just used. Hinglish in, Hinglish out; Hindi in, Hindi out; English in, English out.
Write Hindi words in Devanagari only when the user spoke pure Hindi; for Hinglish write romanised Hinglish.
Keep claim, cashless, co-pay, room rent, pre-auth in English. Say amounts the Indian way ("pachaas hazaar", "ek lakh bees hazaar"), never "120 thousand".{kannada}

Length: this is spoken audio. 1 to 3 short sentences per turn. One question at a time. No lists, no markdown, no emoji.

Facts: never state coverage, amounts, hospitals, dates or claim status unless a tool returned them in this conversation.
If unsure, call a tool. Say policy numbers only as "policy ending in 4417". Round amounts when speaking.

Actions: before book_appointment, initiate_preauth, set_reminder or escalate_to_human, say what you will do and wait for a clear yes.
If a tool returns confirmation_required, ask for the yes; do not claim it is done.
After the cashless pre-auth is filed, call send_summary, then offer a reminder the evening before admission.

Boundaries: no medical advice or diagnosis. For "should I get surgery" or "which treatment" questions, say the treating doctor decides, and offer help with cover instead.
If the user is upset, disputes a decision or a rejection, or asks for a person ("kisi insaan se baat karao"), offer escalate_to_human.
If the user interrupts or changes the request, drop the old one and follow the new one.

Memory (from earlier calls and the insurer's records):
{memory}"""

KANNADA_RULE = "\nIf the user speaks Kannada, reply in simple Kannada (Kannada script), keeping insurance terms in English."

FILLERS = {
    "hi": ["एक सेकंड, चेक करती हूँ।", "जी, देख रही हूँ।"],
    "hinglish": ["Ek second, check karti hoon.", "Haan ji, dekh rahi hoon.", "Ek second."],
    "en": ["One second, let me check.", "Sure, checking that now."],
    "kn": ["ಒಂದು ಕ್ಷಣ, ಚೆಕ್ ಮಾಡುತ್ತೇನೆ."],
}
FALLBACK = {
    "hi": "माफ़ कीजिए, यह मुझसे अभी नहीं हो पा रहा। क्या मैं आपको किसी claims executive से जोड़ दूँ?",
    "hinglish": "Sorry, yeh abhi mujhse nahi ho pa raha. Kya main aapko claims desk ke kisi executive se connect kar doon?",
    "en": "Sorry, I can't complete that right now. Shall I connect you to someone at the claims desk?",
    "kn": "ಕ್ಷಮಿಸಿ, ಇದು ಈಗ ಆಗುತ್ತಿಲ್ಲ. ನಿಮ್ಮನ್ನು claims desk ಗೆ ಸಂಪರ್ಕಿಸಲೇ?",
}
# Said when the model provider errors or rate-limits, so the caller never hears silence.
BUSY = {
    "hi": "माफ़ कीजिए, एक पल के लिए दिक्कत आ गई। क्या आप फिर से बोल सकते हैं?",
    "hinglish": "Sorry, ek second ke liye dikkat aa gayi. Kya aap phir se bol sakte hain?",
    "en": "Sorry, I had a hiccup there. Could you say that again?",
    "kn": "ಕ್ಷಮಿಸಿ, ಒಂದು ಕ್ಷಣ ತೊಂದರೆ ಆಯಿತು. ಮತ್ತೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?",
}

_client: AsyncOpenAI | None = None
_last_user: dict[str, tuple[str, float]] = {}
# Agora's history only carries text turns, so tool results from earlier turns of this call
# (hospital ids, pre-auth ids) are kept here and put back into the prompt every turn.
_tool_log: dict[str, list[str]] = {}


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=config.LLM_API_KEY or "missing", base_url=config.LLM_BASE_URL)
    return _client


def resolve_session(session_id: str | None, member_hint: str | None = None) -> dict | None:
    if session_id:
        s = db.one("SELECT * FROM sessions WHERE id = ?", session_id)
        if s:
            return s
    if member_hint:
        return db.one("SELECT * FROM sessions WHERE member_id = ? ORDER BY started_at DESC LIMIT 1", member_hint)
    return db.one("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def build_messages(session: dict, incoming: list[dict], lang: str) -> list[dict]:
    kannada = KANNADA_RULE if db.flag("FEATURE_KANNADA") else ""
    system = SYSTEM_PROMPT.format(kannada=kannada, memory=memory.block(session["member_id"], session.get("reason")))
    log_lines = _tool_log.get(session["id"], [])[-8:]
    if log_lines:
        system += "\n\nTool results earlier in this call (use these ids; they are facts):\n" + "\n".join(log_lines)
    history = [m for m in incoming if m.get("role") in ("user", "assistant", "tool")]
    # Agora keeps only text turns; drop orphan tool messages it may not have.
    history = [m for m in history if m["role"] != "tool"]
    msgs = [{"role": "system", "content": system}]
    for m in history[-20:]:
        msgs.append({"role": m["role"], "content": _text_of(m.get("content"))})
    return msgs


async def turn(session: dict, incoming: list[dict]) -> AsyncIterator[str]:
    """Yields text pieces to speak. Emits transcript and tool events as side effects."""
    sid, mid = session["id"], session["member_id"]
    user_text = next((_text_of(m.get("content")) for m in reversed(incoming) if m.get("role") == "user"), "")
    lang = memory.get(mid, "language") or "hinglish"
    if user_text.strip():
        detected = memory.detect_language(user_text)
        if detected != lang and len(user_text.split()) >= 2:
            lang = detected
            memory.put(mid, "language", lang)
        prev = _last_user.get(sid)
        if not prev or prev[0] != user_text or time.time() - prev[1] > 8:  # skip Agora retries of the same turn
            _last_user[sid] = (user_text, time.time())
            await hub.emit(mid, {"type": "transcript", "role": "user", "text": user_text, "lang": lang}, sid)

    if not config.llm_ready():
        msg = "LLM_API_KEY is not set on the server."
        await hub.emit(mid, {"type": "transcript", "role": "agent", "text": msg}, sid)
        yield msg
        return

    ctx = Ctx(session_id=sid, member_id=mid, last_user_text=user_text)
    messages = build_messages(session, incoming, lang)
    spoken: list[str] = []
    filler_sent = False
    t0 = time.time()

    for round_no in range(config.MAX_TOOL_ROUNDS + 1):
        if round_no == config.MAX_TOOL_ROUNDS:
            fb = FALLBACK.get(lang, FALLBACK["hinglish"])
            spoken.append(fb)
            yield fb
            break
        try:
            stream = await client().chat.completions.create(
                model=config.LLM_MODEL, messages=messages, tools=registry.schemas(), stream=True,
                temperature=0.3, max_tokens=300)
        except APIError as e:
            log.warning("model call failed: %s", e)
            busy = BUSY.get(lang, BUSY["hinglish"])
            spoken.append(busy)
            yield busy
            break
        calls: dict[int, dict] = {}
        text_this_round = ""
        first = True
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.tool_calls:
                if first and not filler_sent and not text_this_round:
                    f = random.choice(FILLERS.get(lang, FILLERS["hinglish"]))
                    filler_sent = True
                    spoken.append(f)
                    yield f + " "
                for tc in delta.tool_calls:
                    c = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        c["id"] = tc.id
                    if tc.function and tc.function.name:
                        c["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        c["args"] += tc.function.arguments
            if delta.content:
                if first:
                    log.info("first token %.0f ms", (time.time() - t0) * 1000)
                text_this_round += delta.content
                yield delta.content
            first = False
        if text_this_round:
            spoken.append(text_this_round)
        if not calls:
            break

        ordered = [calls[i] for i in sorted(calls)]
        for c in ordered:
            c["id"] = c["id"] or f"call_{uuid.uuid4().hex[:8]}"
        messages.append({"role": "assistant", "content": text_this_round or None,
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"], "arguments": c["args"] or "{}"}} for c in ordered]})
        for c in ordered:
            try:
                args = json.loads(c["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            card = {"type": "tool", "id": c["id"], "name": c["name"], "args": registry.display_args(c["name"], args)}
            await hub.emit(mid, {**card, "status": "running", "summary": ""}, sid)
            ts = time.time()
            status, result = await registry.run(c["name"], args, ctx)
            await hub.emit(mid, {**card, "status": status, "summary": result.get("summary", ""),
                                 "confirmed_by": result.get("confirmed_by"), "ms": int((time.time() - ts) * 1000)}, sid)
            view = registry.model_view(result)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": view})
            if status == "done":
                _tool_log.setdefault(sid, []).append(f"- {c['name']}({json.dumps(args, ensure_ascii=False)}) -> {view[:900]}")

    final = " ".join(s.strip() for s in spoken if s.strip())
    log.info("turn done %.0f ms: %s", (time.time() - t0) * 1000, final[:120])
    if final:
        await hub.emit(mid, {"type": "transcript", "role": "agent", "text": final}, sid)


def sse_chunk(text: str | None, model: str, cid: str, finish: str | None = None) -> str:
    delta = {"content": text} if text is not None else {}
    body = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


async def sse(session: dict, incoming: list[dict], model: str) -> AsyncIterator[str]:
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
    try:
        async for piece in turn(session, incoming):
            yield sse_chunk(piece, model, cid)
    except Exception:
        log.exception("turn failed")
        yield sse_chunk(FALLBACK["hinglish"], model, cid)
    yield sse_chunk(None, model, cid, finish="stop")
    yield "data: [DONE]\n\n"


async def complete(session: dict, incoming: list[dict]) -> str:
    parts = [p async for p in turn(session, incoming)]
    return "".join(parts).strip()


async def summarise_call(session_id: str, member_id: str) -> str | None:
    rows = db.all_("SELECT payload FROM events WHERE session_id = ? AND type IN ('transcript','tool') ORDER BY id", session_id)
    lines = []
    for r in rows:
        e = json.loads(r["payload"])
        if e["type"] == "transcript":
            lines.append(f"{e['role']}: {e['text']}")
        elif e.get("status") == "done":
            lines.append(f"[tool {e['name']}: {e.get('summary')}]")
    if len(lines) < 2:
        return None
    day = db.now().strftime("%d %b")
    if not config.llm_ready():
        return f"{day}: " + "; ".join(l for l in lines if l.startswith("[tool"))[:300]
    try:
        r = await client().chat.completions.create(
            model=config.LLM_MODEL, max_tokens=120, temperature=0.2,
            messages=[{"role": "system", "content": "Summarise this insurance helpline call in 1-2 short sentences for the "
                       "agent's memory: what was asked, what was done (ids, dates), what is pending. Use the caller's language."},
                      {"role": "user", "content": "\n".join(lines)[-6000:]}])
        return f"{day}: {r.choices[0].message.content.strip()}"
    except Exception as e:
        log.warning("summary failed: %s", e)
        return None
