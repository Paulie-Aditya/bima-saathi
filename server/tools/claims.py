"""Cashless pre-auth, claim status, document link and document check."""
from __future__ import annotations

import json
from datetime import timedelta

from .. import config, db, docs, notify
from ..events import hub
from .common import Ctx, ToolError, fmt_dt, inr, match_procedure, parse_iso, parse_when, resolve_person
from .hospitals import resolve_hospital

DOC_LABELS = {
    "discharge_summary": "discharge summary",
    "doctor_prescription": "doctor's prescription / advice for admission",
    "investigation_reports": "investigation reports (X-ray, blood tests)",
    "hospital_preauth_form": "pre-auth form signed by the hospital",
    "original_bills": "original hospital bills",
    "cancelled_cheque": "cancelled cheque for the bank transfer",
    "id_proof": "ID proof",
    "policy_card": "policy e-card",
}
STATUS_STEPS = ["SUBMITTED", "QUERY", "APPROVED", "SETTLED"]


def claim_view(c: dict) -> dict:
    proc = db.one("SELECT name_en FROM procedures WHERE code = ?", c["procedure_code"])
    person = db.one("SELECT name, relation FROM insured_persons WHERE id = ?", c["insured_person_id"])
    hosp = db.one("SELECT name FROM hospitals WHERE id = ?", c["hospital_id"])
    return {"claim_id": c["id"], "claim_type": c["type"], "status": c["status"],
            "patient": f"{person['name']} ({person['relation']})" if person else None,
            "procedure": proc["name_en"] if proc else None, "hospital": hosp["name"] if hosp else None,
            "amount": c["amount"], "pending_docs": c["pending_docs"], "admission_date": c["admission_date"],
            "updated_at": c["updated_at"], "note": c["note"]}


async def emit_claim(member_id: str, claim_id: str, session_id: str | None = None) -> None:
    c = db.one("SELECT * FROM claims WHERE id = ?", claim_id)
    if c:
        await hub.emit(member_id, {"type": "claim", **claim_view(c)}, session_id)


def expected_tat(c: dict) -> str:
    if c["status"] == "SETTLED":
        return "settled"
    if c["status"] == "APPROVED":
        return "amount is paid to the hospital at discharge" if c["type"] == "cashless" else "payment within 7 working days"
    if c["type"] == "cashless":
        return "decision within 1 hour of complete documents (IRDAI cashless rule)"
    return "decision within 30 days of the last document"


async def initiate_preauth(ctx: Ctx, hospital_id: str, procedure: str, insured_person: str | None = None,
                           admission_date: str | None = None) -> dict:
    h = resolve_hospital(hospital_id)
    if not h["cashless"]:
        return {"filed": False, "reason": f"{h['name']} is not cashless on this policy; the member can pay and claim reimbursement.",
                "summary": "Not cashless here · reimbursement route"}
    person = resolve_person(ctx.member_id, insured_person)
    proc, _, top3 = match_procedure(procedure)
    if proc is None:
        return {"filed": False, "did_you_mean": top3, "summary": "Which procedure?"}
    if admission_date:
        adm = parse_when(admission_date)[0].date()
    else:
        b = db.one("SELECT start_at FROM bookings WHERE member_id = ? AND hospital_id = ? ORDER BY created_at DESC LIMIT 1",
                   ctx.member_id, h["id"])
        adm = parse_iso(b["start_at"]).date() if b else (db.now() + timedelta(days=3)).date()
    pid = db.next_id("PA", "claims")
    pending = ["doctor_prescription", "investigation_reports", "hospital_preauth_form"]
    estimate = int((proc["cost_low"] + proc["cost_high"]) / 2)
    now = db.now_iso()
    db.run("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", pid, ctx.member_id, person["id"], h["id"],
           proc["code"], "cashless", "SUBMITTED", json.dumps(pending), estimate, adm.isoformat(),
           "ID proof and policy e-card already on file", now, now)
    await emit_claim(ctx.member_id, pid, ctx.session_id)
    return {"filed": True, "preauth_id": pid, "status": "SUBMITTED", "patient": person["name"],
            "procedure": proc["name_en"], "hospital": h["name"], "admission_date": adm.isoformat(),
            "estimated_amount": inr(estimate),
            "missing_documents": [DOC_LABELS[d] for d in pending],
            "already_on_file": ["ID proof", "policy e-card"],
            "expected_tat": "decision within 1 hour once the hospital sends the documents",
            "summary": f"Pre-auth {pid} · Submitted · {len(pending)} docs missing"}


def get_claim_status(ctx: Ctx, claim_id: str | None = None, insured_person: str | None = None) -> dict:
    if claim_id:
        c = db.one("SELECT * FROM claims WHERE id = ? AND member_id = ?", claim_id.upper().replace(" ", ""), ctx.member_id)
        if not c:
            raise ToolError(f"No claim {claim_id} on this member's policy.")
        claims = [c]
    else:
        claims = db.all_("SELECT * FROM claims WHERE member_id = ? ORDER BY CASE status WHEN 'SETTLED' THEN 1 ELSE 0 END, "
                         "updated_at DESC", ctx.member_id)
        if insured_person:
            p = resolve_person(ctx.member_id, insured_person)
            claims = [c for c in claims if c["insured_person_id"] == p["id"]]
    if not claims:
        return {"claims": [], "summary": "No claims found"}
    out = []
    for c in claims[:3]:
        v = claim_view(c)
        v["pending_items"] = [DOC_LABELS.get(d, d) for d in c["pending_docs"]]
        v["last_update"] = fmt_dt(parse_iso(c["updated_at"]))
        v["expected_tat"] = expected_tat(c)
        out.append(v)
    top = out[0]
    summary = f"{top['claim_id']} · {top['status'].title()}"
    if top["pending_items"]:
        summary += " · needs " + ", ".join(top["pending_items"])
    return {"claims": out, "summary": summary}


async def send_document_link(ctx: Ctx, claim_id: str, doc_types: list[str] | None = None) -> dict:
    c = db.one("SELECT * FROM claims WHERE id = ? AND member_id = ?", claim_id.upper(), ctx.member_id)
    if not c:
        raise ToolError(f"No claim {claim_id} on this member's policy.")
    docs_needed = doc_types or c["pending_docs"]
    labels = [DOC_LABELS.get(d, d.replace("_", " ")) for d in docs_needed]
    link = f"{config.WEB_BASE_URL}/upload/{c['id']}"
    text = (f"📄 Bima Saathi: claim {c['id']} ke liye yeh documents upload kijiye: {', '.join(labels)}.\n"
            f"Upload link: {link}\nYa is chat mein photo bhej dijiye (caption mein {c['id']} likhein).")
    sent = await notify.telegram(text, ctx.member_id, label="upload-link")
    return {"sent": True, "via": "telegram" if sent else "demo phone", "documents": labels, "link": link,
            "summary": "Upload link sent" + (" on Telegram ✓" if sent else " (demo phone)")}


async def check_document(ctx: Ctx, claim_id: str, document_id: str | None = None) -> dict:
    c = db.one("SELECT * FROM claims WHERE id = ?", claim_id.upper())
    if not c:
        raise ToolError(f"No claim {claim_id}.")
    d = (db.one("SELECT * FROM documents WHERE id = ?", document_id) if document_id else
         db.one("SELECT * FROM documents WHERE claim_id = ? ORDER BY created_at DESC LIMIT 1", c["id"]))
    if not d:
        return {"found": False, "summary": "No document received yet"}
    return await apply_document(c, d, ctx.session_id)


async def apply_document(c: dict, d: dict, session_id: str | None = None) -> dict:
    """Read a stored document with the vision model, update the claim's pending list and emit events."""
    if not d.get("doc_type"):
        result = await docs.extract(d["path"], d["mime"])
        db.run("UPDATE documents SET doc_type = ?, fields = ? WHERE id = ?", result["doc_type"],
               json.dumps(result["fields"], ensure_ascii=False), d["id"])
        d = {**d, **result}
    doc_type = d["doc_type"]
    pending = [p for p in c["pending_docs"] if p != doc_type]
    status = c["status"]
    note = c["note"]
    if doc_type in c["pending_docs"]:
        note = f"{DOC_LABELS.get(doc_type, doc_type)} received {db.now().strftime('%d %b')}"
        if not pending and status == "QUERY":
            status = "SUBMITTED"
    db.run("UPDATE claims SET pending_docs = ?, status = ?, note = ?, updated_at = ? WHERE id = ?",
           json.dumps(pending), status, note, db.now_iso(), c["id"])
    await emit_claim(c["member_id"], c["id"], session_id)
    fields = d.get("fields") or {}
    return {"found": True, "document_type": doc_type, "label": DOC_LABELS.get(doc_type, doc_type),
            "extracted_fields": fields, "matched_pending_item": doc_type in c["pending_docs"],
            "still_missing": [DOC_LABELS.get(p, p) for p in pending], "claim_status": status,
            "summary": f"{DOC_LABELS.get(doc_type, doc_type).capitalize()} read" +
                       (f" · still missing {len(pending)}" if pending else " · nothing pending")}
