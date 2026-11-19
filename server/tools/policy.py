"""Profile, coverage, cost estimate and policy wording tools."""
from __future__ import annotations

from datetime import date

from .. import db, memory, rag
from .common import Ctx, ToolError, inr, match_procedure, resolve_person, round_to

ROOM_RATES = {  # per day, by hospital tier
    1: {"general": 3000, "twin": 6000, "single": 9000, "deluxe": 14000},
    2: {"general": 2000, "twin": 4000, "single": 6000, "deluxe": 9000},
    3: {"general": 1500, "twin": 3000, "single": 4500, "deluxe": 7000},
}
TIER_COST = {1: 1.25, 2: 1.0, 3: 0.85}
ROOM_LINKED_SHARE = 0.7  # share of non-room charges that a room-rent cap scales down


def get_member_profile(ctx: Ctx) -> dict:
    m = ctx.member
    pol = ctx.policy
    fam = db.all_("SELECT name, relation, age FROM insured_persons WHERE member_id = ?", ctx.member_id)
    claims = memory.open_claims(ctx.member_id)
    return {
        "name": m["name"],
        "language": ctx.language,
        "policy": memory.mask_policy(pol["id"]),
        "plan": pol["name"],
        "sum_insured": inr(pol["sum_insured"]),
        "family": fam,
        "open_claims": [{"id": c["id"], "patient": c["patient"], "status": c["status"],
                         "pending_docs": c["pending_docs"]} for c in claims],
        "summary": f"{m['name']} · {len(fam)} insured · {len(claims)} open claim(s)",
    }


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


def _coverage(ctx: Ctx, insured_person: str, procedure: str) -> dict:
    person = resolve_person(ctx.member_id, insured_person)
    proc, score, top3 = match_procedure(procedure)
    if proc is None:
        return {"matched": False, "did_you_mean": top3,
                "summary": "Unclear procedure: " + " / ".join(t["name"] for t in top3)}
    pol = ctx.policy
    rule = db.one("SELECT * FROM coverage_rules WHERE policy_id = ? AND procedure_code = ?", pol["id"], proc["code"])
    start = date.fromisoformat(pol["start_date"])
    today = db.now().date()
    months = _months_between(start, today)
    waiting = rule["waiting_period_months"]
    waiting_done = months >= waiting
    y, mo = divmod(start.month - 1 + waiting, 12)
    waiting_ends = date(start.year + y, mo + 1, min(start.day, 28))
    copay = pol["senior_copay_pct"] if person["age"] >= 60 else pol["copay_pct"]
    covered = bool(rule["covered"]) and waiting_done
    return {
        "matched": True, "match_confidence": round(score, 2),
        "person": person, "proc": proc, "pol": pol, "rule": rule,
        "covered": covered, "excluded": not rule["covered"], "waiting_done": waiting_done,
        "waiting_ends": waiting_ends, "copay": copay,
    }


def check_coverage(ctx: Ctx, insured_person: str, procedure: str) -> dict:
    c = _coverage(ctx, insured_person, procedure)
    if not c["matched"]:
        return c
    pol, rule, person, proc = c["pol"], c["rule"], c["person"], c["proc"]
    out = {
        "insured_person": f"{person['name']} ({person['relation']}, {person['age']})",
        "procedure": proc["name_en"], "procedure_hi": proc["name_hi"],
        "covered": c["covered"],
        "sum_insured": inr(pol["sum_insured"]),
        "waiting_period_months": rule["waiting_period_months"],
        "waiting_period_complete": c["waiting_done"],
        "room_rent_cap_per_day": inr(pol["room_rent_cap"]) if pol["room_rent_cap"] else "no cap",
        "copay_pct": c["copay"],
        "sub_limit": inr(rule["sub_limit"]) if rule["sub_limit"] else "none",
        "exclusions_hit": [rule["note"]] if c["excluded"] and rule["note"] else [],
    }
    if c["excluded"]:
        out["summary"] = f"Not covered · {rule['note'] or 'excluded'}"
        out["next_steps"] = "Explain the exclusion plainly; offer escalate_to_human if they disagree."
    elif not c["waiting_done"]:
        out["waiting_period_ends"] = c["waiting_ends"].isoformat()
        out["summary"] = f"Waiting period till {c['waiting_ends'].strftime('%b %Y')}"
        out["next_steps"] = "Not payable until the waiting period ends; emergencies from an accident are covered."
    else:
        bits = ["Covered", f"co-pay {c['copay']}%"]
        if pol["room_rent_cap"]:
            bits.append(f"room cap {inr(pol['room_rent_cap'])}/day")
        if rule["sub_limit"]:
            bits.append(f"sub-limit {inr(rule['sub_limit'])}")
        out["summary"] = " · ".join(bits)
    return out


def _payable(bill: float, stay: int, room_rate: int, cap: int, sub_limit: int, copay: int, si: int) -> float:
    room = room_rate * stay
    other = bill
    deduction = 0.0
    if cap and room_rate > cap:
        deduction += (room_rate - cap) * stay
        deduction += (1 - cap / room_rate) * ROOM_LINKED_SHARE * other
    non_payable = 0.03 * other  # gloves, admission kit, etc.
    admissible = room + other - deduction - non_payable
    if sub_limit:
        admissible = min(admissible, sub_limit)
    insurer = admissible * (1 - copay / 100)
    return min(insurer, si)


def estimate_out_of_pocket(ctx: Ctx, insured_person: str, procedure: str, hospital_id: str | None = None,
                           room_type: str | None = None) -> dict:
    c = _coverage(ctx, insured_person, procedure)
    if not c["matched"]:
        return c
    proc, pol, rule = c["proc"], c["pol"], c["rule"]
    hosp = db.one("SELECT * FROM hospitals WHERE id = ?", hospital_id) if hospital_id else None
    tier = hosp["tier"] if hosp else 2
    room_type = (room_type or "single").lower()
    if room_type not in ROOM_RATES[tier]:
        room_type = "single"
    stay = max(1, proc["stay_days"])
    rate = ROOM_RATES[tier][room_type]
    lows, highs = proc["cost_low"] * TIER_COST[tier], proc["cost_high"] * TIER_COST[tier]

    def oop(bill: float, r: int) -> float:
        total = bill + r * stay
        if not c["covered"]:
            return total
        return total - _payable(bill, stay, r, pol["room_rent_cap"], rule["sub_limit"], c["copay"], pol["sum_insured"])

    low, high = round_to(oop(lows, rate)), round_to(oop(highs, rate))
    drivers = []
    if not c["covered"]:
        drivers.append("not covered, so the full bill is yours")
    if c["covered"] and c["copay"]:
        drivers.append(f"{c['copay']}% co-pay")
    if c["covered"] and pol["room_rent_cap"] and rate > pol["room_rent_cap"]:
        drivers.append(f"{room_type} room at {inr(rate)}/day is above the {inr(pol['room_rent_cap'])} cap, "
                       f"so other charges are cut in proportion")
    if c["covered"] and rule["sub_limit"]:
        drivers.append(f"sub-limit {inr(rule['sub_limit'])} for this procedure")
    drivers.append("about 3% non-medical items")

    out = {
        "procedure": proc["name_en"], "hospital": hosp["name"] if hosp else "typical network hospital",
        "room_type": room_type, "room_rate_per_day": inr(rate), "stay_days": stay,
        "estimated_bill": f"{inr(round_to(lows + rate * stay, 1000))} – {inr(round_to(highs + rate * stay, 1000))}",
        "you_pay_low": inr(low), "you_pay_high": inr(high), "drivers": drivers,
        "summary": f"You pay ~{inr(low)}–{inr(high)} · {room_type} room",
    }
    cap = pol["room_rent_cap"]
    if c["covered"] and cap and rate > cap:
        within = max((k for k, v in ROOM_RATES[tier].items() if v <= cap), key=lambda k: ROOM_RATES[tier][k], default=None)
        if within:
            r2 = ROOM_RATES[tier][within]
            out["if_room_within_cap"] = {"room_type": within, "you_pay_low": inr(round_to(oop(lows, r2))),
                                         "you_pay_high": inr(round_to(oop(highs, r2)))}
    return out


def search_policy_wording(ctx: Ctx, query: str) -> dict:
    hits = rag.search(query, k=3)
    if not hits:
        return {"clauses": [], "summary": "No matching clause"}
    return {
        "policy_document": rag.doc_title(),
        "clauses": [{"clause": h["clause"], "title": h["title"], "text": h["text"][:700]} for h in hits],
        "instruction": "Paraphrase in the user's language and name the clause number, e.g. 'clause 4.2 ke hisaab se'.",
        "summary": "Clause " + ", ".join(h["clause"] for h in hits) + f" · {hits[0]['title']}",
    }
