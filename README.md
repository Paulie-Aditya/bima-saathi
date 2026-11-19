# Bima Saathi

A voice agent for Indian health insurance. A policyholder talks to it in Hindi, English or Hinglish. It checks
cover, finds a network hospital, books a slot, files a cashless pre-auth, tracks the claim and sends a summary
to their phone. Built for Build with Agora, Bengaluru (Track 5, Voice AI for Bharat).

Agora ConvoAI runs the voice loop (Sarvam ASR in, Murf Falcon TTS out). This repo's FastAPI server is the
agent's "LLM": Agora calls its OpenAI-compatible `/chat/completions` each turn. The server adds member memory,
runs the tools, pushes action cards to the page and streams the reply back.

```
browser mic ──Agora RTC──► ConvoAI ─(Sarvam ASR)─► POST /s/{session}/chat/completions ─► tool loop ─► SQLite
browser ◄──Agora RTC──── ConvoAI ◄(Murf TTS)──── SSE text                                    │
browser ◄──────────────── WebSocket /ws/{member}: transcript, action cards, claim updates ◄──┘
                                                       Telegram + Google Calendar ◄── tools
```

## Quick start

```bash
make setup                      # Python venv + web deps
cp .env.example .env            # fill in keys (see below)
make test                       # 36 tests, no keys needed
make run                        # API on :8000, web on :3000
make tunnel                     # in another terminal; put the https URL in PUBLIC_BASE_URL and restart
```

Open http://localhost:3000, pick a demo member, enter any 4-digit OTP, press **Talk**.
Demo controls (claim status flips, reset, feature flags) are at http://localhost:3000/admin.

Without Agora keys the page falls back to **text mode**, which runs the exact same tool loop. Without an LLM key
the agent replies that the key is missing; everything else (tracker, admin, callback, document upload) still works.

## Keys

| Variable | Where from | Needed for |
| --- | --- | --- |
| `AGORA_APP_ID`, `AGORA_APP_CERT` | Agora Console project (enable Conversational AI) | voice |
| `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET` | Agora Console → RESTful API | starting/stopping the agent |
| `SARVAM_API_KEY` | Sarvam dashboard | Hindi/Hinglish ASR |
| `MURF_API_KEY`, `MURF_VOICE_ID` | Murf API dashboard; e.g. `hi-IN-poonam` (Hindi, English and Kannada) | TTS |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | any OpenAI-compatible endpoint with tool calling; the Sarvam key works (`https://api.sarvam.ai/v1`, `sarvam-105b-conversations`) | the agent's reasoning |
| `VISION_MODEL`, `VISION_API_KEY`, `VISION_BASE_URL` | optional OpenAI-compatible vision model for document photos; blank = guess type from file name | MVP+ document check |
| `LLM_SHARED_SECRET` | make one up | Agora → server auth (Bearer) |
| `PUBLIC_BASE_URL` | your tunnel or deploy URL (HTTPS) | Agora must reach `/chat/completions` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_DEMO_CHAT_ID`, `TELEGRAM_DESK_CHAT_ID` | @BotFather; chat ids of the demo phone and desk group | summaries, reminders, escalations |
| `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_CALENDAR_ID` | service account shared (edit) on a demo calendar | real Calendar events on booking |

For document photos over Telegram, point the bot's webhook at `{PUBLIC_BASE_URL}/telegram/webhook`.
Every Telegram message is also mirrored to the on-screen demo phone, which is the fallback if Telegram fails.

## Demo run sheet (3 scenes)

1. `make reset`. Select **Priya**. Talk: "Meri mummy ka knee replacement covered hai kya?" → "Roughly kitna kharcha aayega?"
   MVP+: "Room rent limit ke baare mein policy kya kehti hai?" (quotes clause 4.2).
2. "Whitefield ke paas ortho hospital batao" → "Pehla wala, Saturday morning" → "Haan" → "Cashless pre-auth bhi start kar do" → "Haan".
   Summary lands on the phone; agent offers a reminder.
3. End the call, select **Ramesh**. On `/admin` set CLM-1042 to **Query** → the page rings → **Answer**. The agent opens in
   Hindi with the update. "Kal yaad dila dena" → "Haan". Upload a discharge summary at `/upload/CLM-1042` (or send the
   photo to the bot) → the claim's pending item clears.

## What is where

| Path | What it does |
| --- | --- |
| `server/llm.py` | `/chat/completions` tool loop: memory, filler line, ≤4 tool rounds, SSE back to Agora |
| `server/tools/registry.py` | 13 tool schemas, dispatch, server-side confirm gate, feature flags |
| `server/tools/*.py` | policy/coverage/estimate, hospitals/booking, claims/pre-auth/documents, reminders/summary/escalation |
| `server/agora.py` | AccessToken2 builder (RTC + RTM) and ConvoAI join/leave |
| `server/memory.py` | the memory block put into every turn; language detection |
| `server/notify.py` | Telegram, Google Calendar, reminder scheduler |
| `server/rag.py` | BM25 over numbered policy clauses (MVP+) |
| `server/docs.py` | vision model reads a document photo (MVP+) |
| `data/seed.json` | invented members, family, 15 Bengaluru hospitals, 25 procedures with Hindi aliases, 4 claims |
| `web/app/page.tsx` | talk button, transcript, action cards, demo phone, claim tracker, incoming call |
| `web/app/admin/page.tsx` | claim status flips, reset, flags, reminders, stats |
| `scripts/matrix.py` | the PRD's utterance matrix through the real model (`make matrix`) |

## Choices that differ from the PRD

- **Session id travels in the LLM URL path** (`/s/{session}/chat/completions`), not in `llm.headers`, so nothing
  depends on Agora forwarding custom headers.
- **WebSocket is per member** (`/ws/{member_id}`), not per session, so the proactive callback reaches the page
  between calls.
- **Confirm-before-action is enforced on the server**, not just in the prompt: a state-changing tool only runs if the
  user's latest turn is a clear yes, and one yes covers one action. A blocked call shows as a "needs yes" card.
- **Tool results are re-injected each turn.** Agora's history keeps only text, so ids from earlier turns
  (hospital, pre-auth) would be lost; the server keeps a per-call tool log in the prompt.
- **Murf `model` defaults to `FALCON`**, the value in Agora's Murf page; set `MURF_MODEL` if Falcon 2.1 needs another.
- **Policy wording is an invented sample** (`data/sample_policy_wording.md`). Drop a public PDF in as
  `data/sample_policy_wording.pdf` and it is used instead.
- **Not built:** the "block any ₹ amount not in tool results" post-check from the risk table, SIP dial-in, caregiver mode.

## Verified against live services

- Agora: ConvoAI join with Sarvam ASR + Murf TTS params is accepted and the agent reaches RUNNING; leave works.
- `/chat/completions` through a Cloudflare tunnel with the shared secret (401 without it), streaming SSE.
- Sarvam `sarvam-105b-conversations` as the LLM: `make matrix` 22/22 (11 scenarios x 2), replies in ~0.3-1.5 s.
- Murf Falcon voices; Google Calendar event creation.

Not yet: the spoken round trip (Gate 0), Telegram delivery, and a vision model for document photos.
Sarvam's free tier rate-limits bursts; the agent says a short "please repeat" line instead of going silent.
All data is invented. No real insurer, policy or patient data.
