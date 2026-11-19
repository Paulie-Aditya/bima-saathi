"""Policy wording search (MVP+, FEATURE_RAG). BM25 over numbered clauses, no embeddings needed.

Uses data/sample_policy_wording.pdf if present (pypdf), else data/sample_policy_wording.md."""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache

from . import config

CLAUSE = re.compile(r"^\s*(\d{1,2}\.\d{1,2})\s+([^.\n]{2,80})\.\s*(.*)$")

# Hinglish/Hindi words people use, mapped to the wording's English terms.
SYNONYMS = {
    "kamra": "room", "kamre": "room", "कमरा": "room", "kiraya": "rent", "किराया": "rent",
    "limit": "limit", "seema": "limit", "सीमा": "limit", "intezaar": "waiting", "waiting": "waiting",
    "pratiksha": "waiting", "motiyabind": "cataract", "मोतियाबिंद": "cataract", "ghutna": "knee", "घुटना": "knee",
    "delivery": "maternity", "prasav": "maternity", "पहले": "pre-existing", "purani": "pre-existing",
    "bimari": "disease", "बीमारी": "disease", "dant": "dental", "daant": "dental", "दांत": "dental",
    "cashless": "cashless", "kitne": "within", "din": "days", "दिन": "days", "copay": "co-payment",
    "co-pay": "co-payment", "hissa": "co-payment", "ambulance": "ambulance", "shikayat": "grievance",
    "शिकायत": "grievance", "documents": "documents", "kagaz": "documents", "kaagaz": "documents",
    "reject": "reject", "kharij": "reject", "ivf": "infertility", "lasik": "refractive",
}
STOP = set("the a an of to and or in for is are be by on with as at this that any from such if it its will may".split())


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9\-]+|[ऀ-ॿ]+", text.lower())
    out = []
    for w in words:
        w = SYNONYMS.get(w, w)
        if w in STOP or len(w) < 2:
            continue
        out.append(w.rstrip("s") if len(w) > 4 else w)
    return out


def _load_text() -> tuple[str, str]:
    pdf = config.DATA_DIR / "sample_policy_wording.pdf"
    if pdf.exists():
        from pypdf import PdfReader
        return pdf.name, "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages)
    md = config.DATA_DIR / "sample_policy_wording.md"
    return "Suraksha Family Floater – Policy Wording (sample)", md.read_text()


@lru_cache
def _index():
    title, text = _load_text()
    clauses = []
    section = ""
    for line in text.splitlines():
        h = re.match(r"^#+\s*\d+\.\s*(.+)$", line.strip())
        if h:
            section = h.group(1)
            continue
        m = CLAUSE.match(line)
        if m:
            clauses.append({"clause": m.group(1), "title": m.group(2).strip(), "text": m.group(3).strip(), "section": section})
        elif clauses and line.strip() and not line.startswith("#"):
            clauses[-1]["text"] += " " + line.strip()
    # the section heading gives each clause its context ("Waiting periods" for 5.3)
    docs = [Counter(_tokens(f"{c['section']} {c['title']} {c['title']} {c['text']}")) for c in clauses]
    df = Counter(t for d in docs for t in d)
    avg = sum(sum(d.values()) for d in docs) / max(1, len(docs))
    return title, clauses, docs, df, avg


def doc_title() -> str:
    return _index()[0]


def search(query: str, k: int = 3) -> list[dict]:
    title, clauses, docs, df, avg = _index()
    q = _tokens(query)
    n = len(docs)
    scores = []
    for i, d in enumerate(docs):
        dl = sum(d.values())
        s = 0.0
        matched = 0
        for t in set(q):
            if t not in d:
                continue
            matched += 1
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            tf = d[t]
            s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / avg))
        # prefer clauses that cover more of the question's distinct terms
        scores.append((s * (0.5 + matched / max(1, len(set(q)))), i))
    scores.sort(reverse=True)
    return [clauses[i] for s, i in scores[:k] if s > 0]
