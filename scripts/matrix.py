"""Run the PRD's utterance matrix through the real model in text mode and log pass/fail.

Each scenario is a short scripted conversation; the check looks at which tools fired and their status.
Usage: python scripts/matrix.py [--base http://localhost:8000] [--runs 3]
Rows 5, 8, 9 and the voice-only checks (interruption, latency, audio) still need a spoken run."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

SCENARIOS = [
    # (row, member, turns, expected tool (status done) after the last turn, extra check)
    (1, "M001", ["Meri mummy ka knee replacement covered hai kya?"], "check_coverage", None),
    (2, "M001", ["Ghutna badalwana hai papa ka"], "check_coverage", None),
    (3, "M001", ["Meri mummy ka knee replacement covered hai kya?", "Roughly kitna kharcha aayega?"], "estimate_out_of_pocket", "₹|hazaar|lakh"),
    (4, "M001", ["Whitefield ke paas ortho hospital batao"], "find_network_hospitals", None),
    (5, "M001", ["Whitefield ke paas ortho hospital batao", "Pehla wala, mummy ke liye Saturday morning", "Haan"], "book_appointment", None),
    (6, "M001", ["Whitefield ke paas ortho hospital batao", "Pehla wala, mummy ke liye Saturday morning", "Haan",
                 "Mummy ke knee replacement ka cashless pre-auth bhi start kar do", "Haan, cashless start kar do"], "initiate_preauth", None),
    (7, "M002", ["मेरे पापा का क्लेम कहाँ तक पहुँचा?"], "get_claim_status", "[ऀ-ॿ]"),
    (8, "M002", ["Mere papa ka claim kahan tak pahuncha?", "Kal yaad dila dena", "Haan"], "set_reminder", None),
    (10, "M001", ["Kya mujhe surgery karwani chahiye?"], None, "doctor|डॉक्टर"),
    (11, "M003", ["Claim reject kyun hua? Kisi insaan se baat karao", "Haan"], "escalate_to_human", None),
    (12, "M001", ["Room rent limit ke baare mein policy kya kehti hai?"], "search_policy_wording", r"4\.2"),
]


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def run_one(base: str, row, member, turns, tool, check) -> tuple[bool, str, float]:
    import re
    s = post(base, "/api/session/start", {"member_id": member, "mode": "text"})
    sid = s["sessionId"]
    reply, t_last = "", 0.0
    try:
        for t in turns:
            t0 = time.time()
            reply = post(base, "/api/dev/chat", {"sessionId": sid, "text": t})["reply"]
            t_last = time.time() - t0
        events = json.loads(urllib.request.urlopen(f"{base}/api/events/{sid}").read())
    finally:
        post(base, "/api/session/stop", {"sessionId": sid})
    done = [e["name"] for e in events if e["type"] == "tool" and e["status"] == "done"]
    ok = True
    why = []
    if tool and tool not in done:
        ok = False
        why.append(f"missing {tool}")
    if tool is None and any(n in done for n in ("book_appointment", "initiate_preauth")):
        ok = False
        why.append("took an action")
    if check and not re.search(check, reply, re.I):
        ok = False
        why.append(f"reply lacks /{check}/")
    return ok, f"tools={done} reply={reply[:140]!r}" + (f" FAIL: {', '.join(why)}" if why else ""), t_last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--rows", default="")
    a = ap.parse_args()
    rows = {int(x) for x in a.rows.split(",") if x}
    total = passed = 0
    for run in range(a.runs):
        post(a.base, "/admin/reset", {})
        for row, member, turns, tool, check in SCENARIOS:
            if rows and row not in rows:
                continue
            ok, detail, secs = run_one(a.base, row, member, turns, tool, check)
            total += 1
            passed += ok
            print(f"run {run + 1} row {row:>2} {'PASS' if ok else 'FAIL'} ({secs:.1f}s last turn) {detail}")
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
