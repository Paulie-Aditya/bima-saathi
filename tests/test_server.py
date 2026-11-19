"""Runs without any API keys: tools hit the seeded SQLite DB, the LLM is a scripted fake."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace as NS

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["LLM_API_KEY"] = "test"
os.environ["LLM_SHARED_SECRET"] = "s3cret"
for k in ("TELEGRAM_BOT_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "AGORA_APP_ID", "AGORA_CUSTOMER_ID"):
    os.environ[k] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server import agora, config, db, llm, rag, seed  # noqa: E402
from server.tools import registry  # noqa: E402
from server.tools.common import Ctx, inr, is_affirmative, match_procedure, parse_when  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    seed.reset()
    db.run("INSERT INTO sessions VALUES ('S1','M001','c','',  'text', NULL, ?, NULL, 0)", db.now_iso())
    db.run("INSERT INTO sessions VALUES ('S2','M002','c','',  'text', NULL, ?, NULL, 0)", db.now_iso())
    llm._tool_log.clear()
    yield


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def call(name, args, member="M001", said="", session="S1"):
    return run(registry.run(name, args, Ctx(session_id=session, member_id=member, last_user_text=said)))


# ---------- helpers ----------

def test_inr_indian_grouping():
    assert inr(120000) == "₹1,20,000"
    assert inr(5000) == "₹5,000"
    assert inr(10000000) == "₹1,00,00,000"


@pytest.mark.parametrize("text,code", [
    ("knee replacement", "TKR"), ("ghutna badalwana hai papa ka", "TKR"), ("घुटना बदलना", "TKR"),
    ("motiyabind", "CAT"), ("cataract ka operation", "CAT"), ("pitt ki pathri", "GBL"), ("bawasir", "PIL"),
])
def test_procedure_aliases(text, code):
    p, score, _ = match_procedure(text)
    assert p and p["code"] == code, (text, score)


def test_procedure_unclear_returns_top3():
    p, _, top3 = match_procedure("xyzzy plugh")
    assert p is None and len(top3) == 3


@pytest.mark.parametrize("text,yes", [
    ("haan", True), ("Haan, cashless start kar do", True), ("हाँ, कर दो", True), ("ji haan", True), ("ok", True),
    ("Pehla wala, Saturday morning", False), ("nahi abhi mat karo", False), ("kar do na", True), ("Kal yaad dila dena", False),
])
def test_affirmative(text, yes):
    assert is_affirmative(text) is yes


def test_parse_when():
    now = db.now()
    s, end = parse_when("Saturday morning")
    assert s.weekday() == 5 and s.hour == 6 and end.hour == 12 and s > now
    s, _ = parse_when("in 2 minutes")
    assert 60 < (s - now).total_seconds() < 180
    s, _ = parse_when("kal 10 baje")
    assert (s.date() - now.date()).days == 1 and s.hour == 10
    s, _ = parse_when("शाम 7 बजे")
    assert s.hour == 19


# ---------- tools ----------

def test_coverage_scene1_mother_knee():
    st, r = call("check_coverage", {"insured_person": "mummy", "procedure": "knee replacement"})
    assert st == "done" and r["covered"] is True
    assert r["copay_pct"] == 10 and r["room_rent_cap_per_day"] == "₹5,000" and r["waiting_period_complete"]


def test_coverage_waiting_period_and_exclusion():
    st, r = call("check_coverage", {"insured_person": "self", "procedure": "hernia"}, member="M003")
    assert r["covered"] is False and "waiting_period_ends" in r
    st, r = call("check_coverage", {"insured_person": "self", "procedure": "lasik"}, member="M003")
    assert r["covered"] is False and r["exclusions_hit"]


def test_senior_copay():
    st, r = call("check_coverage", {"insured_person": "papa", "procedure": "cataract"}, member="M002")
    assert r["copay_pct"] == 20 and r["sub_limit"] == "₹40,000"


def test_estimate_has_range_and_drivers():
    st, r = call("estimate_out_of_pocket", {"insured_person": "mother", "procedure": "TKR", "room_type": "single"})
    assert st == "done"
    lo = int(r["you_pay_low"].strip("₹").replace(",", ""))
    hi = int(r["you_pay_high"].strip("₹").replace(",", ""))
    assert 0 < lo < hi
    assert any("co-pay" in d for d in r["drivers"])


def test_find_hospitals_whitefield_ortho():
    st, r = call("find_network_hospitals", {"area": "Whitefield", "specialty": "ortho"})
    assert st == "done" and len(r["hospitals"]) == 2
    assert all(h["cashless"] for h in r["hospitals"])
    assert r["hospitals"][0]["distance_km"] <= r["hospitals"][1]["distance_km"]


def test_booking_needs_yes_then_books():
    st, r = call("book_appointment", {"hospital_id": "H01", "slot": "Saturday morning", "insured_person": "mother"},
                 said="Pehla wala, Saturday morning")
    assert st == "confirm"
    st, r = call("book_appointment", {"hospital_id": "H01", "slot": "Saturday morning", "insured_person": "mother"}, said="haan")
    assert st == "done" and r["booked"] and r["booking_id"].startswith("BKG-")


def test_one_yes_one_action():
    ctx = Ctx(session_id="S1", member_id="M001", last_user_text="haan")
    st1, _ = run(registry.run("set_reminder", {"when": "in 2 minutes", "text": "x"}, ctx))
    st2, _ = run(registry.run("set_reminder", {"when": "in 3 minutes", "text": "y"}, ctx))
    assert (st1, st2) == ("done", "confirm")


def test_preauth_creates_claim_with_missing_docs():
    st, r = call("initiate_preauth", {"hospital_id": "H01", "procedure": "knee replacement", "insured_person": "mother"},
                 said="Haan, cashless start kar do")
    assert st == "done" and r["preauth_id"].startswith("PA-") and len(r["missing_documents"]) == 3
    c = db.one("SELECT * FROM claims WHERE id = ?", r["preauth_id"])
    assert c["status"] == "SUBMITTED"


def test_preauth_refused_at_non_cashless():
    st, r = call("initiate_preauth", {"hospital_id": "H03", "procedure": "delivery", "insured_person": "self"}, said="haan")
    assert st == "done" and r["filed"] is False


def test_claim_status_scene3():
    st, r = call("get_claim_status", {}, member="M002", session="S2")
    assert r["claims"][0]["claim_id"] == "CLM-1042" and "discharge summary" in r["summary"]


def test_reminder_escalation_and_summary():
    st, r = call("set_reminder", {"when": "in 2 minutes", "text": "Discharge summary bhejna"}, said="haan")
    assert st == "done"
    st, r = call("escalate_to_human", {"reason": "disputes rejection"}, said="haan please")
    assert st == "done" and r["ticket_id"].startswith("TKT-")
    st, r = call("send_summary", {})
    assert st == "done" and "Bima Saathi" in r["message"]
    assert db.one("SELECT COUNT(*) n FROM notifications")["n"] >= 2


def test_policy_wording_search():
    hits = rag.search("room rent limit kya hai")
    assert hits and hits[0]["clause"] == "4.2"
    hits = rag.search("cataract waiting period")
    assert "5.3" in [h["clause"] for h in hits]


def test_flags_hide_tools():
    db.set_flag("FEATURE_RAG", False)
    assert "search_policy_wording" not in [s["function"]["name"] for s in registry.schemas()]
    db.set_flag("FEATURE_RAG", True)
    assert "search_policy_wording" in [s["function"]["name"] for s in registry.schemas()]


def test_access_token_shape():
    config.AGORA_APP_ID, config.AGORA_APP_CERT = "a" * 32, "b" * 32
    try:
        t = agora.build_token("bima-x", 12345)
        assert t.startswith("007") and len(t) > 100
    finally:
        config.AGORA_APP_ID = config.AGORA_APP_CERT = ""


def test_join_body_shape():
    b = agora.join_body("abc", "bima-abc", 4242, "Namaste", "hinglish")
    p = b["properties"]
    assert p["llm"]["vendor"] == "custom" and p["llm"]["url"].endswith("/s/abc/chat/completions")
    assert p["asr"]["vendor"] == "sarvam" and p["tts"]["vendor"] == "murf"
    assert p["remote_rtc_uids"] == ["4242"]


# ---------- full loop with a scripted fake model ----------

def _chunk(content=None, tool=None):
    tcs = None
    if tool:
        tcs = [NS(index=0, id="call_1", function=NS(name=tool[0], arguments=json.dumps(tool[1])))]
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tcs))])


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        self.it = iter(self.chunks)
        return self

    async def __anext__(self):
        try:
            return next(self.it)
        except StopIteration:
            raise StopAsyncIteration


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = NS(completions=NS(create=self.create))

    async def create(self, **kw):
        self.calls.append(kw)
        return FakeStream(self.script.pop(0))


def test_sse_tool_loop(monkeypatch):
    fake = FakeLLM([
        [_chunk(tool=("check_coverage", {"insured_person": "mummy", "procedure": "knee replacement"}))],
        [_chunk("Haan, covered hai. "), _chunk("Co-pay 10% lagega.")],
    ])
    monkeypatch.setattr(llm, "client", lambda: fake)
    from server.main import app
    with TestClient(app) as tc:
        db.run("INSERT OR IGNORE INTO sessions VALUES ('S9','M001','c','', 'voice', NULL, ?, NULL, 0)", db.now_iso())
        r = tc.post("/s/S9/chat/completions", headers={"Authorization": "Bearer s3cret"},
                    json={"model": "x", "stream": True, "messages": [
                        {"role": "user", "content": "Meri mummy ka knee replacement covered hai kya?"}]})
        assert r.status_code == 200
        body = r.text
        assert body.strip().endswith("data: [DONE]")
        text = "".join(json.loads(l[6:])["choices"][0]["delta"].get("content", "")
                       for l in body.splitlines() if l.startswith("data: {"))
        assert "covered hai" in text and text.lower().startswith(("ek second", "haan ji", "ek second."))
        # the tool result went back to the model, and the card events were stored
        assert fake.calls[1]["messages"][-1]["role"] == "tool"
        kinds = [json.loads(e["payload"])["status"] for e in
                 db.all_("SELECT payload FROM events WHERE session_id='S9' AND type='tool'")]
        assert kinds == ["running", "done"]
        # bad key is rejected
        assert tc.post("/s/S9/chat/completions", json={"messages": []}).status_code == 401


def test_admin_flip_emits_incoming_call():
    from server.main import app
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/M002") as ws:
            r = tc.post("/admin/claims/CLM-1042/status", json={"status": "APPROVED"})
            assert r.json()["pending_docs"] == []
            types = [json.loads(ws.receive_text())["type"] for _ in range(3)]
            assert types == ["claim", "phone", "incoming_call"]
