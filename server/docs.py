"""Document check (MVP+, FEATURE_DOCS): a vision model reads a claim document photo."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger("docs")

DOC_TYPES = ["discharge_summary", "original_bills", "investigation_reports", "doctor_prescription",
             "hospital_preauth_form", "cancelled_cheque", "id_proof", "policy_card", "other"]

PROMPT = (
    "You read Indian hospital and insurance documents. Classify this document as one of: "
    + ", ".join(DOC_TYPES)
    + ". Then extract the key fields you can read (patient_name, hospital, admission_date, discharge_date, "
    "diagnosis, procedure, total_amount, doctor). Reply with JSON only: "
    '{"doc_type": "...", "fields": {...}, "readable": true}'
)


async def extract(path: str, mime: str) -> dict:
    if not config.llm_ready():
        return {"doc_type": _guess_from_name(path), "fields": {"note": "vision model not configured; guessed from file name"}}
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    data = Path(path).read_bytes()
    if mime == "application/pdf":
        from pypdf import PdfReader
        import io
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)[:6000]
        content = [{"type": "text", "text": PROMPT + "\n\nDocument text:\n" + text}]
    else:
        b64 = base64.b64encode(data).decode()
        content = [{"type": "text", "text": PROMPT},
                   {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
    try:
        r = await client.chat.completions.create(model=config.VISION_MODEL, messages=[{"role": "user", "content": content}],
                                                 response_format={"type": "json_object"}, max_tokens=400)
        out = json.loads(r.choices[0].message.content or "{}")
        if out.get("doc_type") not in DOC_TYPES:
            out["doc_type"] = "other"
        out.setdefault("fields", {})
        return out
    except Exception as e:
        log.warning("vision failed: %s", e)
        return {"doc_type": _guess_from_name(path), "fields": {"note": "could not read the document"}}


def _guess_from_name(path: str) -> str:
    name = Path(path).name.lower()
    for t in DOC_TYPES:
        if t.split("_")[0] in name:
            return t
    return "discharge_summary" if "discharge" in name else "other"
