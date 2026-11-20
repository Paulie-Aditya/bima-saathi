# Bima Saathi: project summary

**What it is:** a voice agent for Indian health-insurance policyholders. You talk to it in Hindi, English, Hinglish or Kannada, and it checks cover, estimates cost, finds a network hospital, books a slot, files a cashless pre-auth, tracks the claim, takes document uploads, sets reminders, hands off to a human, and calls the member back when a claim changes.

**Built for:** Build with Agora, Bengaluru, Track 5 (Voice AI for Bharat). Solo build.

## How a spoken turn flows

```
You speak ──► Agora RTC (browser mic) ──► Agora Conversational AI agent
                                            │
                                            ├─ Sarvam ASR: speech → text (Hindi/Hinglish, hi-IN)
                                            │
                                            ├─ POST {PUBLIC_BASE_URL}/s/{session}/chat/completions   ← our FastAPI server
                                            │      ├─ adds the member's memory + earlier tool results to the prompt
                                            │      ├─ calls Sarvam LLM (sarvam-105b-conversations) with 13 tools
                                            │      ├─ runs tools against the SQLite "insurer", ≤4 rounds
                                            │      ├─ pushes cards/transcript to the page over WebSocket
                                            │      └─ streams the reply text back (SSE)
                                            │
                                            └─ Murf Falcon TTS: text → voice (hi-IN-poonam)
You hear ◄── Agora RTC ◄────────────────────┘
```

## What is used where

| Technology | Role | Where in the code |
|---|---|---|
| **Agora Conversational AI** | Runs the real-time voice loop: joins the channel, handles turn-taking and barge-in, wires speech recognition → LLM → speech | `server/agora.py` (join/leave through the REST API) |
| **Agora RTC Web SDK** (`agora-rtc-sdk-ng`) | Browser mic and speaker, with echo cancellation, noise suppression and volume meter | `web/lib/agora.ts` |
| **Agora AccessToken2** | RTC + RTM tokens, signed on the server with the App Certificate | `server/agora.py` → `build_token` |
| **Sarvam ASR** | Speech-to-text for Indian languages (configured inside the Agora join) | `server/agora.py` → `asr` block |
| **Sarvam LLM** (`sarvam-105b-conversations`) | The agent's reasoning and tool calling, through Sarvam's OpenAI-compatible API | `server/llm.py`, `.env` `LLM_*` |
| **Murf Falcon TTS** | The agent's voice, Poonam (Hindi, English and Kannada), 24 kHz | `server/agora.py` → `tts` block |
| **FastAPI** (Python) | The custom LLM endpoint Agora calls, plus the app API, WebSocket and admin | `server/main.py` |
| **SQLite** | Mock insurer: policies, members, family, 25 procedures, 15 Bengaluru hospitals, slots, claims, reminders, tickets, memory | `server/db.py`, `server/seed.py`, `data/seed.json` |
| **BM25 retrieval** | Quotes numbered clauses from the policy wording (MVP+) | `server/rag.py`, `data/sample_policy_wording.md` |
| **Telegram Bot API** | Messages to the member's phone (call summary, reminders, upload link), escalation tickets to the Claims Desk group, document photos sent to the bot | `server/notify.py`, `/telegram/webhook` in `main.py` |
| **Google Calendar API** (service account) | A real calendar event for every booking | `server/notify.py` → `calendar_event` |
| **Next.js 15 + React 19** | The member app: talk button, live transcript, tool cards, demo phone, claim tracker, incoming call, OTP; plus `/admin` and `/upload/[claim]` | `web/app/*`, `web/components/ui.tsx` |
| **Cloudflare Tunnel / Render** | A public HTTPS URL so Agora can reach the server | `make tunnel`, `Dockerfile` |
| **Vercel** | Hosts the web app | `web/` |

## The 13 tools

| Tool | What it does | Needs a spoken yes? |
|---|---|---|
| `get_member_profile` | Policy, family, sum insured, room-rent cap | |
| `check_coverage` | Covered or not, with waiting period, exclusions and senior co-pay | |
| `estimate_out_of_pocket` | Cost range: room-rent proportional deduction, non-payables, sub-limits, co-pay | |
| `find_network_hospitals` | Cashless network hospitals by area and specialty, with the next free slot | |
| `book_appointment` | Books a slot (or offers alternatives) and creates a Calendar event | ✅ |
| `initiate_preauth` | Files a cashless pre-auth (PA-xxxx) and lists missing documents | ✅ |
| `get_claim_status` | Status, pending documents, expected turnaround (IRDAI timelines) | |
| `send_document_link` | Sends an upload link to the phone | |
| `check_document` | Reads an uploaded document and clears the pending item | |
| `set_reminder` | Telegram reminder at a parsed time ("kal shaam", "Friday 7 baje") | ✅ |
| `send_summary` | Call recap to the phone, in the member's language | |
| `escalate_to_human` | Ticket + callback slot, posted to the Claims Desk group | ✅ |
| `search_policy_wording` *(MVP+)* | Quotes the matching policy clause | |

## Design choices worth mentioning

- **Server-enforced confirmation.** State-changing tools run only if the user's latest turn is a clear yes (haan, ha, yes, ji, हाँ…). One yes allows one action; otherwise the card shows "needs yes".
- **Tool memory across turns.** Agora's history keeps only text, so the server keeps a per-call log of tool results and puts it back into the prompt each turn. That keeps hospital ids and pre-auth ids from being lost.
- **Member memory.** Each call starts with the member's language preference, open claims and summaries of past calls.
- **No dead air.** A filler line ("ek second, check karti hoon") is spoken as soon as the model decides to call a tool. If the LLM fails or is rate-limited, the agent says a short "please repeat" line instead of going silent.
- **Proactive callback.** When `/admin` flips a claim to Query, the page rings, and the agent opens the call in the member's language with the update.
- **Session id in the URL path.** Agora calls `/s/{session}/chat/completions`, so nothing depends on custom headers being forwarded. Requests are authenticated with a Bearer shared secret.
- **Feature flags.** RAG, callback, document check and Kannada can each be switched on or off live from `/admin`.

## Demo data (all invented)

- **Priya Sharma** (Whitefield, Hinglish): family floater with ₹5L sum insured, ₹5,000/day room-rent cap and 10% co-pay. Covers her mother Sunita (56) and father Rajesh (59).
- **Ramesh Verma** (Jayanagar, Hindi): individual ₹3L policy, 20% senior co-pay. His father Shyam Lal (78) has claim **CLM-1042** (cataract), waiting on a discharge summary.
- **Kavya Rao** (HSR Layout, Kannada): new policy still in its waiting periods. Claim CLM-1077 is in Query.

## Tested

- 36 automated tests (`make test`), no keys needed.
- `make matrix` runs the PRD's utterance matrix through the real model: **22/22** passed (11 scenarios × 2 runs).
- Live: the Agora agent join (RUNNING), the tunnel with auth, Sarvam LLM streaming, Murf voices, Google Calendar events, and Telegram delivery to both the phone and the group.

## Not built (by choice)

- A post-check that blocks any ₹ amount not in tool results; the prompt and tools cover this for now.
- SIP dial-in for feature phones.
- Caregiver mode.
- Production messaging would use WhatsApp or SMS instead of Telegram.
