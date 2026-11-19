"""Network hospital search and appointment booking."""
from __future__ import annotations

from datetime import timedelta

from .. import db, notify
from ..events import hub
from .common import (Ctx, ToolError, fmt_dt, km, norm, parse_iso, parse_when, resolve_area, resolve_person,
                     resolve_specialty, similarity)


def _next_slot(hospital_id: str) -> dict | None:
    return db.one("SELECT * FROM slots WHERE hospital_id = ? AND booked = 0 AND start_at > ? ORDER BY start_at LIMIT 1",
                  hospital_id, db.now_iso())


def find_network_hospitals(ctx: Ctx, area: str | None = None, specialty: str | None = None, limit: int = 2) -> dict:
    area_name, centre = resolve_area(area, ctx.member["area"])
    spec = resolve_specialty(specialty)
    rows = db.all_("SELECT * FROM hospitals WHERE network = 1")
    found = []
    for h in rows:
        if spec and spec not in h["specialties"]:
            continue
        d = km(centre["lat"], centre["lng"], h["lat"], h["lng"])
        found.append((not h["cashless"], round(d, 1), h))
    found.sort(key=lambda x: (x[0], x[1]))
    limit = max(1, min(int(limit or 2), 5))
    out = []
    for _, d, h in found[:limit]:
        s = _next_slot(h["id"])
        out.append({"hospital_id": h["id"], "name": h["name"], "area": h["area"], "distance_km": d,
                    "cashless": bool(h["cashless"]), "next_free_slot": fmt_dt(parse_iso(s["start_at"])) if s else None})
    if not out:
        return {"hospitals": [], "summary": f"No network {spec or ''} hospital near {area_name}"}
    return {"near": area_name, "specialty": spec, "hospitals": out,
            "summary": " | ".join(f"{h['name'].split(',')[0]} {h['distance_km']} km" + (" · cashless" if h["cashless"] else "")
                                  for h in out)}


def resolve_hospital(ref: str) -> dict:
    h = db.one("SELECT * FROM hospitals WHERE id = ?", (ref or "").upper())
    if h:
        return h
    best = max(db.all_("SELECT * FROM hospitals"), key=lambda r: similarity(ref, r["name"]))
    if similarity(ref, best["name"]) < 0.5:
        raise ToolError(f"Unknown hospital '{ref}'. Call find_network_hospitals first.")
    return best


async def book_appointment(ctx: Ctx, hospital_id: str, slot: str, insured_person: str | None = None) -> dict:
    h = resolve_hospital(hospital_id)
    person = resolve_person(ctx.member_id, insured_person)
    start, window_end = parse_when(slot, default_hour=9)
    if window_end is None:
        # exact time asked: take that slot or the nearest within 45 minutes
        cand = db.one("SELECT * FROM slots WHERE hospital_id = ? AND booked = 0 AND start_at BETWEEN ? AND ? "
                      "ORDER BY start_at LIMIT 1", h["id"],
                      (start - timedelta(minutes=45)).isoformat(timespec="seconds"),
                      (start + timedelta(minutes=45)).isoformat(timespec="seconds"))
    else:
        cand = db.one("SELECT * FROM slots WHERE hospital_id = ? AND booked = 0 AND start_at >= ? AND start_at < ? "
                      "ORDER BY start_at LIMIT 1", h["id"], start.isoformat(timespec="seconds"),
                      window_end.isoformat(timespec="seconds"))
    if not cand:
        alts = db.all_("SELECT start_at FROM slots WHERE hospital_id = ? AND booked = 0 AND start_at >= ? "
                       "ORDER BY start_at LIMIT 3", h["id"], max(start, db.now()).isoformat(timespec="seconds"))
        return {"booked": False, "reason": "That slot is taken or not available.",
                "next_free_slots": [fmt_dt(parse_iso(a["start_at"])) for a in alts],
                "summary": "Slot taken · next free " + (fmt_dt(parse_iso(alts[0]["start_at"])) if alts else "none")}

    booking_id = db.next_id("BKG", "bookings")
    db.run("UPDATE slots SET booked = 1 WHERE id = ?", cand["id"])
    when = parse_iso(cand["start_at"])
    db.run("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?)", booking_id, ctx.member_id, person["id"], h["id"],
           cand["id"], cand["start_at"], None, db.now_iso())
    link = await notify.calendar_event(
        summary=f"{person['name']} – consultation at {h['name']}",
        start=when, minutes=30,
        description=f"Booked by Bima Saathi ({booking_id}). Carry policy card, ID proof and previous reports.",
        location=f"{h['name']}, {h['address']}, Bengaluru {h['pincode']}")
    db.run("UPDATE bookings SET calendar_link = ? WHERE id = ?", link, booking_id)
    await hub.emit(ctx.member_id, {"type": "booking", "id": booking_id, "hospital": h["name"],
                                   "patient": person["name"], "start_at": when.isoformat(), "calendar_link": link},
                   ctx.session_id)
    return {"booked": True, "booking_id": booking_id, "slot": fmt_dt(when), "patient": person["name"],
            "hospital": h["name"], "hospital_id": h["id"], "address": f"{h['address']}, Bengaluru {h['pincode']}",
            "cashless": bool(h["cashless"]),
            "calendar_event": "created" if link else "not configured",
            "summary": f"Booked {booking_id} · {fmt_dt(when)}" + (" · Calendar ✓" if link else "")}
