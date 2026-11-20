# Bima Saathi: demo script

About 5 minutes plus Q&A. Trim scene 1's estimate line or the MVP+ beats if your slot is shorter.

## Before you go on stage

- [ ] Open `/admin` and press **Reset demo DB**, or run `make reset` locally.
- [ ] Open `/admin` in a second tab. Scene 3 needs it.
- [ ] Have your phone ready, with the Telegram chat with **@bima_saathi_demo_bot** open and the sound on.
- [ ] Wear headphones with a mic, or use a quiet corner. Room noise is the biggest risk to speech recognition.
- [ ] If the app is deployed, open the page a minute early to wake the server; Render's free tier sleeps.
- [ ] Have a photo of a "discharge summary" on your phone or laptop for scene 3. Any image named `discharge_summary.jpg` works.

## Opening (30 s)

> "In India, most people who have health insurance only find out what it covers when they're already at the hospital: waiting periods, room-rent caps, co-pays, which hospitals are cashless. The helpline is an IVR menu in English. Bima Saathi is a voice agent that a policyholder can just *talk* to, in Hindi, English or Hinglish, and it doesn't just answer. It checks cover, books the hospital, files the cashless pre-auth and follows up."

Point at the screen: **Talk** button on the left, **What the agent is doing** in the middle (every tool call appears here live), the **demo phone** on the right, and the **claim tracker** at the bottom.

## Scene 1: Priya asks about cover (Hinglish, ~1.5 min)

Select **Priya Sharma**, type any 4-digit OTP, and press **Talk**. The agent greets her by name.

| You say | What happens / what to point at |
|---|---|
| "Meri mummy ka knee replacement covered hai kya?" | A `check_coverage` card appears. It knows "mummy" is Sunita, 56, on Priya's family floater policy. Answer: covered, 10% co-pay, room-rent cap ₹5,000/day. |
| "Roughly kitna kharcha aayega?" | `estimate_out_of_pocket` runs and gives a range in lakh/hazaar. Point out: *"These numbers come from the policy rules in the database, not from the model's imagination. The agent isn't allowed to say an amount a tool didn't give it."* |
| *(MVP+)* "Room rent limit ke baare mein policy kya kehti hai?" | `search_policy_wording` quotes **clause 4.2** from the policy document. |

Talking point: *"Notice the filler line, 'ek second, check karti hoon', when it calls a tool. There's no dead air on a phone call."*

## Scene 2: book and file pre-auth (Hinglish, ~1.5 min)

| You say | What happens |
|---|---|
| "Whitefield ke paas ortho hospital batao" | `find_network_hospitals` lists network, cashless hospitals by distance: Sahyadri Ortho, Brookfield Bone & Joint. |
| "Pehla wala, mummy ke liye Saturday morning" | The agent says what it will book and **asks for a yes**. |
| "Haan" | `book_appointment` runs, and a real **Google Calendar** event is created. |
| "Cashless pre-auth bhi start kar do" | It asks for confirmation again. |
| "Haan" | `initiate_preauth` gives a **PA-xxxx** id. A new card appears in the claim tracker, and the **summary lands on your phone in Telegram**. Hold the phone up. |

Talking point: *"Actions that change something need a spoken yes, and that's enforced on the server, not just in the prompt. One yes allows one action. If the model tries to book without a yes, the server blocks it, and you'd see a 'needs yes' card."*

Optionally interrupt the agent mid-sentence to show barge-in.

## Scene 3: the agent calls Ramesh back (Hindi, ~1.5 min)

1. Press **End call**. Select **Ramesh Verma**.
2. In the `/admin` tab, set **CLM-1042** (his father's cataract claim) to **Query**.
3. Back on the main page, it **rings**: an incoming call from Bima Saathi. Press **Answer**.

> Talking point: *"The insurer raised a query, so instead of a letter that arrives in a week, the agent calls proactively."*

| You say / do | What happens |
|---|---|
| (agent opens in Hindi) | It explains that the claim needs the **discharge summary**. The tracker turns amber. |
| "कल याद दिला देना" or "Kal yaad dila dena" | The agent offers a reminder and waits for a yes. |
| "हाँ" | `set_reminder` runs. The reminder will arrive on Telegram. |
| Open `/upload/CLM-1042` (the link is also sent to the phone), or send the photo to the bot | The document is checked, the pending item clears, and the claim moves from QUERY to SUBMITTED. |

Talking point: *"It remembers Ramesh prefers Hindi, and it remembers his open claim from the insurer's records. Every call starts with that memory."*

## Optional beats (if time allows)

- **Kannada (Kavya):** "Claim reject kyun hua? Kisi insaan se baat karao" escalates to a human. A **ticket** is posted to the **Claims Desk** Telegram group with a callback slot, and the agent replies in Kannada.
- **Guardrail:** "Kya mujhe surgery karwani chahiye?" The agent declines medical advice and says the treating doctor decides.

## Close (20 s)

> "Bima Saathi runs on Agora's Conversational AI: Sarvam for Indian-language speech recognition, Sarvam's LLM for reasoning, and Murf Falcon for the voice. Thirteen tools behind it cover coverage, hospitals, pre-auth, claims, documents, reminders and escalation. All the data here is invented, but the flows mirror real IRDAI cashless timelines. It's built for the 500 million Indians who'd rather just talk."

## If something breaks

| Problem | Fallback |
|---|---|
| Speech recognition mishears | Repeat once, slower. Then tick **Type instead of speaking** and type the same line; it uses the same tool loop. |
| Agent goes silent or "dikkat aa gayi" | That's a rate limit. Wait 2 seconds and repeat. |
| Telegram is slow | Point at the on-screen **demo phone**, which shows the same message. |
| Wrong state from rehearsal | `/admin` → **Reset demo DB**. |

## Likely questions

- **Is this real insurer data?** No. Every member, policy, hospital and claim is invented. The rules (waiting periods, room-rent proportional deduction, senior co-pay, 1-hour cashless decision) follow how Indian policies typically work.
- **How do you stop hallucinated amounts?** Amounts only come from tool results. Actions need a server-checked yes. Tool results are put back into the prompt each turn so ids aren't lost.
- **Latency?** The LLM's first token arrives in about 0.3–0.6 s, and Murf's first audio in about 0.15 s. The filler line covers tool calls.
- **What would production need?** WhatsApp or SMS instead of Telegram, the insurer's real APIs instead of SQLite, SIP dial-in for feature phones, and consent and audit logging.
