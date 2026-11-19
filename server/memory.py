"""Member memory: profile, family, language, open claims, reminders, last call summary (F8)."""
from __future__ import annotations

import re

from . import db

OPEN_STATUSES = ("SUBMITTED", "QUERY", "APPROVED")

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
KANNADA = re.compile(r"[ಀ-೿]")
HINDI_WORDS = re.compile(
    r"\b(hai|hain|kya|mera|meri|mere|mujhe|karo|kar|do|dena|batao|kitna|kab|kaise|nahi|haan|ji|"
    r"aur|ka|ki|ke|wala|wali|abhi|kal|chahiye|karna|karwana|papa|mummy|bhai|theek|accha)\b", re.I)


def detect_language(text: str) -> str:
    if KANNADA.search(text):
        return "kn"
    if DEVANAGARI.search(text):
        return "hi"
    if len(HINDI_WORDS.findall(text)) >= 1:
        return "hinglish"
    return "en"


def get(member_id: str, key: str) -> str | None:
    r = db.one("SELECT value FROM memories WHERE member_id = ? AND key = ?", member_id, key)
    return r["value"] if r else None


def put(member_id: str, key: str, value: str) -> None:
    db.run("INSERT INTO memories(member_id, key, value, updated_at) VALUES (?,?,?,?) "
           "ON CONFLICT(member_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
           member_id, key, value, db.now_iso())


def mask_policy(policy_id: str) -> str:
    return f"policy ending in {policy_id[-4:]}"


def first_name(name: str) -> str:
    return name.split()[0]


def open_claims(member_id: str) -> list[dict]:
    q = ",".join("?" * len(OPEN_STATUSES))
    return db.all_(f"SELECT c.*, p.name_en AS procedure, ip.name AS patient, ip.relation "
                   f"FROM claims c LEFT JOIN procedures p ON p.code = c.procedure_code "
                   f"LEFT JOIN insured_persons ip ON ip.id = c.insured_person_id "
                   f"WHERE c.member_id = ? AND c.status IN ({q}) ORDER BY c.updated_at DESC",
                   member_id, *OPEN_STATUSES)


def block(member_id: str, session_reason: str | None = None) -> str:
    m = db.one("SELECT * FROM members WHERE id = ?", member_id)
    if not m:
        return "Unknown caller. Ask for their registered phone number."
    fam = db.all_("SELECT name, relation, age FROM insured_persons WHERE member_id = ?", member_id)
    claims = open_claims(member_id)
    reminders = db.all_("SELECT due_at, text FROM reminders WHERE member_id = ? AND sent = 0 ORDER BY due_at", member_id)
    lines = [
        f"Caller: {m['name']} (member_id {m['id']}), {mask_policy(m['policy_id'])}, lives near {m['area']} ({m['pincode']}).",
        f"Preferred language: {get(member_id, 'language') or m['preferred_language']}.",
        "Insured family: " + "; ".join(f"{f['name']} ({f['relation']}, {f['age']})" for f in fam) + ".",
    ]
    if claims:
        lines.append("Open claims: " + "; ".join(
            f"{c['id']} for {c['relation']} {c['patient']}, {c['procedure']}, status {c['status']}"
            + (f", pending {', '.join(c['pending_docs'])}" if c["pending_docs"] else "") for c in claims) + ".")
    else:
        lines.append("Open claims: none.")
    if reminders:
        lines.append("Pending reminders: " + "; ".join(f"{r['due_at']}: {r['text']}" for r in reminders) + ".")
    last = get(member_id, "last_call_summary")
    if last:
        lines.append(f"Last call: {last}")
    if session_reason:
        lines.append(f"This call: {session_reason}")
    lines.append(f"Now: {db.now().strftime('%A %d %b %Y, %I:%M %p')} IST.")
    return "\n".join(lines)
