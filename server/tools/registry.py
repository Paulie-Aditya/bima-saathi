"""Tool schemas (OpenAI function format) and dispatch.

member_id and session_id never come from the model: they are injected from the session (Ctx).
State-changing tools pass through a server-side confirmation gate (F9)."""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable

from .. import db
from . import claims, comms, hospitals, policy
from .common import Ctx, ToolError, is_affirmative

log = logging.getLogger("tools")


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
PERSON = {"type": "string", "description": "Who is being treated, in the user's words: 'mother', 'papa', 'self' or a name."}
PROC = {"type": "string", "description": "Procedure in the user's own words, any language, e.g. 'ghutna badalna', 'cataract'."}

TOOLS: dict[str, dict[str, Any]] = {
    "get_member_profile": {
        "fn": policy.get_member_profile, "confirm": False,
        "schema": _fn("get_member_profile", "Caller's profile: name, language, masked policy, insured family, open claims.", {}, []),
    },
    "check_coverage": {
        "fn": policy.check_coverage, "confirm": False,
        "schema": _fn("check_coverage", "Is a procedure covered for a family member: waiting period, room-rent cap, co-pay, sub-limit, exclusions.",
                      {"insured_person": PERSON, "procedure": PROC}, ["insured_person", "procedure"]),
    },
    "estimate_out_of_pocket": {
        "fn": policy.estimate_out_of_pocket, "confirm": False,
        "schema": _fn("estimate_out_of_pocket", "Low/high estimate in rupees of what the member pays themselves, and what drives it.",
                      {"insured_person": PERSON, "procedure": PROC,
                       "hospital_id": {"type": "string", "description": "Optional hospital id from find_network_hospitals"},
                       "room_type": {"type": "string", "enum": ["general", "twin", "single", "deluxe"]}},
                      ["insured_person", "procedure"]),
    },
    "find_network_hospitals": {
        "fn": hospitals.find_network_hospitals, "confirm": False,
        "schema": _fn("find_network_hospitals", "Network hospitals near an area or pincode in Bengaluru, cashless first, with next free slot.",
                      {"area": {"type": "string", "description": "Area name or pincode; omit to use the member's home area"},
                       "specialty": {"type": "string", "description": "e.g. orthopaedics, eye, heart"},
                       "limit": {"type": "integer", "default": 2}}, []),
    },
    "book_appointment": {
        "fn": hospitals.book_appointment, "confirm": True,
        "schema": _fn("book_appointment", "Book a hospital slot and add it to Google Calendar. Needs a spoken yes first.",
                      {"hospital_id": S, "slot": {"type": "string", "description": "ISO datetime, or words like 'Saturday morning', 'kal 10 baje'"},
                       "insured_person": PERSON}, ["hospital_id", "slot"]),
    },
    "initiate_preauth": {
        "fn": claims.initiate_preauth, "confirm": True,
        "schema": _fn("initiate_preauth", "File a cashless pre-authorisation. Returns pre-auth id and missing documents. Needs a spoken yes first.",
                      {"hospital_id": S, "procedure": PROC, "insured_person": PERSON,
                       "admission_date": {"type": "string", "description": "Optional; defaults to the booked slot date"}},
                      ["hospital_id", "procedure"]),
    },
    "get_claim_status": {
        "fn": claims.get_claim_status, "confirm": False,
        "schema": _fn("get_claim_status", "Status, last update, pending items and expected time for the member's claims.",
                      {"claim_id": {"type": "string", "description": "Optional; omit for the member's open claims"},
                       "insured_person": PERSON}, []),
    },
    "send_document_link": {
        "fn": claims.send_document_link, "confirm": False,
        "schema": _fn("send_document_link", "Send the member a Telegram message with an upload link for pending claim documents.",
                      {"claim_id": S, "doc_types": {"type": "array", "items": {"type": "string"}}}, ["claim_id"]),
    },
    "set_reminder": {
        "fn": comms.set_reminder, "confirm": True,
        "schema": _fn("set_reminder", "Schedule a Telegram reminder. Needs a spoken yes first.",
                      {"when": {"type": "string", "description": "ISO datetime or words: 'kal subah', 'in 2 minutes', 'Friday 7 pm'"},
                       "text": {"type": "string", "description": "Short reminder text in the user's language"}}, ["when", "text"]),
    },
    "send_summary": {
        "fn": comms.send_summary, "confirm": False,
        "schema": _fn("send_summary", "Send this call's summary (bookings, pre-auth, documents, reminders) to the member on Telegram.", {}, []),
    },
    "escalate_to_human": {
        "fn": comms.escalate_to_human, "confirm": True,
        "schema": _fn("escalate_to_human", "Create a ticket for the human claims desk with a callback slot. Needs a spoken yes first.",
                      {"reason": S, "claim_id": S, "callback_time": S}, ["reason"]),
    },
    "search_policy_wording": {
        "fn": policy.search_policy_wording, "confirm": False, "flag": "FEATURE_RAG",
        "schema": _fn("search_policy_wording", "Search the policy wording document; returns clauses with clause numbers to paraphrase.",
                      {"query": S}, ["query"]),
    },
    "check_document": {
        "fn": claims.check_document, "confirm": False, "flag": "FEATURE_DOCS",
        "schema": _fn("check_document", "Read the latest document the member sent for a claim and say what is still missing.",
                      {"claim_id": S, "document_id": S}, ["claim_id"]),
    },
}

STATE_CHANGING = [n for n, t in TOOLS.items() if t["confirm"]]


def schemas() -> list[dict]:
    return [t["schema"] for t in TOOLS.values() if not t.get("flag") or db.flag(t["flag"])]


def display_args(name: str, args: dict) -> dict:
    return {k: v for k, v in args.items() if v not in (None, "", [])}


async def run(name: str, args: dict, ctx: Ctx) -> tuple[str, dict]:
    """Returns (status, result). status is done | confirm | error. result always carries a 'summary'."""
    tool = TOOLS.get(name)
    if not tool or (tool.get("flag") and not db.flag(tool["flag"])):
        return "error", {"error": f"Unknown tool {name}", "summary": "Unknown tool"}
    # One spoken yes authorises one state-changing action in this turn.
    if tool["confirm"] and (not is_affirmative(ctx.last_user_text) or ctx.extra.get("confirm_used")):
        return "confirm", {
            "error": "confirmation_required",
            "instruction": "Do not do this yet. Say in one short sentence what you will do and ask for a clear yes.",
            "summary": "Waiting for a spoken yes",
        }
    fn: Callable = tool["fn"]
    params = inspect.signature(fn).parameters
    kwargs = {k: v for k, v in args.items() if k in params and k != "ctx"}
    try:
        result = fn(ctx, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if tool["confirm"]:
            ctx.extra["confirm_used"] = True
            result["confirmed_by"] = ctx.last_user_text[:60]
        return "done", result
    except ToolError as e:
        return "error", {"error": str(e), "summary": str(e)[:80]}
    except TypeError as e:
        log.exception("bad args for %s", name)
        return "error", {"error": f"Bad arguments: {e}", "summary": "Bad arguments"}
    except Exception as e:  # never kill the voice turn
        log.exception("tool %s failed", name)
        return "error", {"error": f"Tool failed: {e}", "summary": "Tool failed"}


def model_view(result: dict) -> str:
    """What the model sees: the full result minus UI-only fields, kept small for latency."""
    return json.dumps({k: v for k, v in result.items() if not k.startswith("_")}, ensure_ascii=False, default=str)[:3000]
