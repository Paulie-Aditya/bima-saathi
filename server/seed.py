"""Reset the SQLite demo DB from data/seed.json. Run: python -m server.seed"""
from __future__ import annotations

import json
import random
from datetime import timedelta

from . import config, db

SLOT_TIMES = [(9, 0), (10, 30), (12, 0), (15, 0), (16, 30), (18, 0)]


def reset() -> dict:
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    for suffix in ("-wal", "-shm"):
        p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
        if p.exists():
            p.unlink()
    db.init()
    seed = json.loads((config.DATA_DIR / "seed.json").read_text())
    rng = random.Random(42)
    today = db.now().replace(hour=0, minute=0, second=0, microsecond=0)

    with db.tx() as c:
        for p in seed["policies"]:
            c.execute("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?)",
                      (p["id"], p["name"], p["insurer"], p["sum_insured"], p["room_rent_cap"],
                       p["copay_pct"], p["senior_copay_pct"], p["start_date"]))
        for m in seed["members"]:
            c.execute("INSERT INTO members VALUES (?,?,?,?,?,?,?)",
                      (m["id"], m["name"], m["phone"], m["preferred_language"], m["policy_id"], m["area"], m["pincode"]))
        for ip in seed["insured_persons"]:
            c.execute("INSERT INTO insured_persons VALUES (?,?,?,?,?)",
                      (ip["id"], ip["member_id"], ip["name"], ip["relation"], ip["age"]))
        for pr in seed["procedures"]:
            c.execute("INSERT INTO procedures VALUES (?,?,?,?,?,?,?,?)",
                      (pr["code"], pr["name_en"], pr["name_hi"], json.dumps(pr["aliases"], ensure_ascii=False),
                       pr["specialty"], pr["cost_low"], pr["cost_high"], pr["stay_days"]))
            for pol in seed["policies"]:
                covered = pr.get("covered", True)
                waiting = pr.get("waiting", 0)
                sub_limit = pr.get("sub_limit", 0)
                # The retail policy covers maternity only after 24 months; the group policy from day 1 of the 9-month wait.
                if pol["id"] == "POL-IN-30028820" and pr["code"] in ("DEL", "CSE"):
                    covered, note = False, "Maternity is not part of this individual plan"
                else:
                    note = pr.get("note", "")
                c.execute("INSERT INTO coverage_rules VALUES (?,?,?,?,?,?)",
                          (pol["id"], pr["code"], int(covered), waiting, sub_limit, note))
        for h in seed["hospitals"]:
            c.execute("INSERT INTO hospitals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (h["id"], h["name"], h["area"], h["pincode"], h["address"], h["lat"], h["lng"],
                       json.dumps(h["specialties"]), int(h["network"]), int(h["cashless"]), h["tier"]))
            for d in range(0, 10):
                day = today + timedelta(days=d)
                for hh, mm in SLOT_TIMES:
                    start = day.replace(hour=hh, minute=mm)
                    if start <= db.now() + timedelta(hours=2):
                        continue
                    booked = 1 if rng.random() < 0.25 else 0
                    c.execute("INSERT INTO slots(hospital_id, start_at, booked) VALUES (?,?,?)",
                              (h["id"], start.isoformat(timespec="seconds"), booked))
        for cl in seed["claims"]:
            updated = (db.now() - timedelta(days=min(cl["days_ago"], 3) if cl["status"] == "QUERY" else cl["days_ago"]))
            created = db.now() - timedelta(days=cl["days_ago"])
            c.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cl["id"], cl["member_id"], cl["insured_person_id"], cl["hospital_id"], cl["procedure_code"],
                       cl["type"], cl["status"], json.dumps(cl["pending_docs"]), cl["amount"], cl["admission_date"],
                       "", created.isoformat(timespec="seconds"), updated.isoformat(timespec="seconds")))
        for mem in seed["memories"]:
            c.execute("INSERT INTO memories VALUES (?,?,?,?)", (mem["member_id"], mem["key"], mem["value"], db.now_iso()))
        for m in seed["members"]:
            lang = m["preferred_language"]
            c.execute("INSERT OR IGNORE INTO memories VALUES (?,?,?,?)", (m["id"], "language", lang, db.now_iso()))

    counts = {t: db.one(f"SELECT COUNT(*) n FROM {t}")["n"] for t in
              ("members", "insured_persons", "hospitals", "procedures", "claims", "slots")}
    return counts


if __name__ == "__main__":
    print(reset())
