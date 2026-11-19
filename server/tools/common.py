"""Helpers shared by the tools: context, matching, parsing and formatting."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from .. import config, db


class ToolError(Exception):
    """Raised for a user-facing problem; the model sees the message and explains it."""


@dataclass
class Ctx:
    session_id: str
    member_id: str
    last_user_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def member(self) -> dict:
        m = db.one("SELECT * FROM members WHERE id = ?", self.member_id)
        if not m:
            raise ToolError("Unknown member")
        return m

    @property
    def policy(self) -> dict:
        return db.one("SELECT * FROM policies WHERE id = ?", self.member["policy_id"])  # type: ignore[return-value]

    @property
    def language(self) -> str:
        from ..memory import get
        return get(self.member_id, "language") or self.member["preferred_language"]


# ---- formatting ----

def inr(n: float | int) -> str:
    """Indian digit grouping: 120000 -> ₹1,20,000."""
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if n < 0 else "") + "₹" + s


def round_to(n: float, step: int = 500) -> int:
    return int(step * round(n / step))


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=config.IST)


# ---- text normalisation and matching ----

def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = re.sub(r"[^\w\sऀ-ॿಀ-೿-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if len(b) >= 3 and re.search(rf"(^|\s){re.escape(b)}($|\s)", a):
        return 1.0
    if len(a) >= 3 and re.search(rf"(^|\s){re.escape(a)}($|\s)", b):
        return 0.95
    ratio = SequenceMatcher(None, a, b).ratio()
    # best window match: compare b against each same-length span of words in a
    words = a.split()
    n = max(1, len(b.split()))
    for i in range(len(words)):
        ratio = max(ratio, SequenceMatcher(None, " ".join(words[i:i + n]), b).ratio())
    return ratio


def match_procedure(text: str) -> tuple[dict | None, float, list[dict]]:
    procs = db.all_("SELECT * FROM procedures")
    scored = []
    for p in procs:
        names = [p["name_en"], p["name_hi"], p["code"], *p["aliases"]]
        scored.append((max(similarity(text, n) for n in names), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    top3 = [{"code": p["code"], "name": p["name_en"], "score": round(s, 2)} for s, p in scored[:3]]
    if best_score < 0.72:
        return None, best_score, top3
    return best, best_score, top3


RELATIONS = {
    "self": ["self", "me", "myself", "main", "mera", "khud", "मैं", "मेरा", "apna", "i"],
    "mother": ["mother", "mom", "mummy", "mumma", "maa", "ma", "amma", "mataji", "मम्मी", "माँ", "मां", "माता"],
    "father": ["father", "dad", "papa", "pappa", "pitaji", "pita", "baba", "appa", "पापा", "पिताजी", "पिता"],
    "spouse": ["wife", "husband", "spouse", "patni", "pati", "biwi", "पत्नी", "पति"],
    "son": ["son", "beta", "बेटा"],
    "daughter": ["daughter", "beti", "बेटी"],
}


def resolve_person(member_id: str, who: str | None) -> dict:
    fam = db.all_("SELECT * FROM insured_persons WHERE member_id = ?", member_id)
    w = norm(who or "self")
    for p in fam:
        if norm(p["name"]) in w or norm(p["name"].split()[0]) in w.split():
            return p
    for rel, words in RELATIONS.items():
        if any(x in w.split() or x == w for x in words):
            for p in fam:
                if p["relation"] == rel:
                    return p
            raise ToolError(f"No insured {rel} on this policy. Insured: "
                            + ", ".join(f"{p['name']} ({p['relation']})" for p in fam))
    if w in ("", "self"):
        return next(p for p in fam if p["relation"] == "self")
    raise ToolError("Could not tell which family member. Insured: "
                    + ", ".join(f"{p['name']} ({p['relation']})" for p in fam))


# ---- geography ----

@lru_cache
def areas() -> dict[str, dict]:
    return json.loads((config.DATA_DIR / "seed.json").read_text())["areas"]


def km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resolve_area(text: str | None, fallback_area: str) -> tuple[str, dict]:
    t = norm(text or "")
    if t:
        for name, a in areas().items():
            if a["pincode"] in t or norm(name).split()[0] in t:
                return name, a
        h = db.one("SELECT area FROM hospitals WHERE pincode = ?", re.sub(r"\D", "", t)[:6])
        if h:
            return h["area"], areas()[h["area"]]
    return fallback_area, areas()[fallback_area]


SPECIALTY_WORDS = {
    "orthopaedics": ["ortho", "orthopedic", "orthopaedic", "bone", "haddi", "joint", "knee", "ghutna", "हड्डी"],
    "ophthalmology": ["eye", "aankh", "ankh", "netra", "cataract", "आँख"],
    "cardiology": ["heart", "cardio", "dil", "दिल"],
    "obstetrics": ["delivery", "pregnancy", "maternity", "prasav"],
    "gynaecology": ["gynae", "gyne", "women", "uterus"],
    "urology": ["uro", "kidney stone", "pathri"],
    "nephrology": ["kidney", "dialysis", "gurda"],
    "oncology": ["cancer", "onco", "chemo"],
    "ent": ["ent", "ear", "nose", "throat", "kaan", "naak", "gala"],
    "general surgery": ["surgery", "surgeon", "hernia", "appendix"],
    "general medicine": ["general", "fever", "bukhar", "physician"],
}


def resolve_specialty(text: str | None) -> str | None:
    t = norm(text or "")
    if not t:
        return None
    for spec, words in SPECIALTY_WORDS.items():
        if spec in t or any(w in t for w in words):
            return spec
    return None


# ---- time parsing (English, Hinglish, Hindi) ----

DAYS = {
    0: ["monday", "mon", "somvar", "somwar", "सोमवार"],
    1: ["tuesday", "tue", "mangalvar", "mangalwar", "मंगलवार"],
    2: ["wednesday", "wed", "budhvar", "budhwar", "बुधवार"],
    3: ["thursday", "thu", "guruvar", "guruwar", "brihaspativar", "गुरुवार"],
    4: ["friday", "fri", "shukravar", "shukrawar", "शुक्रवार"],
    5: ["saturday", "sat", "shanivar", "shaniwar", "शनिवार"],
    6: ["sunday", "sun", "ravivar", "raviwar", "itvaar", "रविवार", "इतवार"],
}
PARTS = {
    "morning": ([ "morning", "subah", "savere", "सुबह"], 6, 12),
    "afternoon": (["afternoon", "dopahar", "dopehar", "दोपहर"], 12, 16),
    "evening": (["evening", "shaam", "sham", "शाम"], 16, 21),
    "night": (["night", "raat", "रात"], 19, 23),
}


def parse_when(text: str, default_hour: int = 10) -> tuple[datetime, datetime | None]:
    """Return (start, window_end). window_end is set when only a part of day was given."""
    now = db.now()
    raw = (text or "").strip()
    try:
        return parse_iso(raw), None
    except ValueError:
        pass
    t = norm(raw)
    m = re.search(r"(?:in\s+|\+)?(\d+)\s*(?:m|min|mins|minute|minutes|मिनट)\b", t)
    if m and ("in" in t.split() or "+" in raw or "baad" in t or "mein" in t or "बाद" in t or t.startswith(m.group(0))):
        return now + timedelta(minutes=int(m.group(1))), None
    m = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours|ghante|घंटे)\b", t)
    if m and ("in" in t.split() or "baad" in t or "mein" in t or "बाद" in t):
        return now + timedelta(hours=int(m.group(1))), None

    day = None
    if any(w in t for w in ["parso", "day after tomorrow", "परसों"]):
        day = now + timedelta(days=2)
    elif any(w in t.split() for w in ["kal", "tomorrow", "कल"]):
        day = now + timedelta(days=1)
    elif any(w in t.split() for w in ["aaj", "today", "आज", "tonight"]):
        day = now
    else:
        for wd, words in DAYS.items():
            if any(w in t.split() for w in words):
                delta = (wd - now.weekday()) % 7 or 7
                if "next" in t.split() or "agle" in t.split():
                    delta += 7 if delta < 7 else 0
                day = now + timedelta(days=delta)
                break
        if day is None:
            m = re.search(r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t)
            if m:
                month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m.group(2)) + 1
                day = now.replace(month=month, day=int(m.group(1)))
                if day < now - timedelta(days=1):
                    day = day.replace(year=now.year + 1)
    if day is None:
        day = now

    hour, minute = None, 0
    m = re.search(r"(?<!\d)(\d{1,2})(?::|\.)?(\d{2})?\s*(am|pm|baje|बजे)?(?![\da-z])", t)
    if m and (m.group(3) or m.group(2)) and int(m.group(1)) <= 23:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) in ("baje", "बजे") and hour < 8 and any(w in t for w in PARTS["evening"][0] + ["dopahar"]):
            hour += 12
    part = next((k for k, (words, _, _) in PARTS.items() if any(w in t for w in words)), None)
    base = day.replace(second=0, microsecond=0)
    if hour is not None:
        return base.replace(hour=hour, minute=minute), None
    if part:
        _, start_h, end_h = PARTS[part]
        return base.replace(hour=start_h, minute=0), base.replace(hour=end_h, minute=0)
    return base.replace(hour=default_hour, minute=0), base.replace(hour=21, minute=0)


# ---- confirmation gate (F9) ----

# \b is unreliable next to Devanagari vowel signs, so use explicit letter boundaries.
_L = r"[\wऀ-ॿಀ-೿]"
AFFIRM = re.compile(
    rf"(?<!{_L})(haan|haa|han|ha|hanji|haanji|yes|yeah|yep|yup|ok|okay|okk|sure|theek|thik|bilkul|zaroor|confirm|confirmed|"
    r"go ahead|please do|kar do|kardo|kar dijiye|kar dijie|karo|kijiye|chalo|done|sahi|हाँ|हां|हा|जी|ठीक|बिल्कुल|ज़रूर|"
    rf"कर दो|कर दीजिए|ಹೌದು|sari|aagli|ji haan)(?!{_L})", re.I)
NEGATE = re.compile(rf"(?<!{_L})(nahi|nahin|no|nope|mat|ruko|wait|cancel|नहीं|मत|रुको|ಬೇಡ)(?!{_L})", re.I)


def is_affirmative(text: str) -> bool:
    t = (text or "").strip().lower()
    return bool(AFFIRM.search(t)) and not NEGATE.search(t)
