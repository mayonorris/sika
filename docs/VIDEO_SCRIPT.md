# Demo video script — Sika

**Target: 2:45. Hard cap: 3:00.** Language: English. Voiceover is REQUIRED and must
explicitly cover three things the judges check for: what you built, **how you used
Codex**, and **how you used GPT-5.6**. Shots 5 and 6 exist specifically to satisfy that.

**Demo URL:** https://sika-7gud.onrender.com
**Codex session ID:** 019f7414-fa13-7ab0-a63c-7f22a2c289cb

Tools: OBS Studio (free) or Xbox Game Bar (Win+G) to record. Clipchamp (built into
Windows 11) to assemble. Record voice separately if that's easier.

---

## Before you press record

- [ ] Open the demo URL and ask one question to **wake the free instance** (cold start is 50s+)
- [ ] Disable the Grammarly extension — its icon floats over the "Demander" button
- [ ] Close other tabs, hide the bookmarks bar, full-screen the browser at 1920x1080
- [ ] Have the questions in a notepad to paste, so you never fumble typing on camera
- [ ] Mic test 10 seconds, no fan noise, phone silenced
- [ ] Open a second window with: AGENTS.md, the docs/ folder, a Codex session, and `git log --oneline`

**Where to record:** use the deployed URL, not localhost. It proves the thing is live, and
the no-API answers are fully cited and correct — no risk of an API quota failure mid-take.
If you'd rather show LLM-composed prose, run locally with the key active, but do a full dry
run first and keep at least 8 requests in reserve for retakes.

---

## Shot list

### 1. The problem (0:00–0:18)
*Screen: an INSEED PDF bulletin, scrolling through dense tables.*

> "This is how West Africa's official economic data lives today. Rigorous, public, and
> buried in PDF bulletins. I know, because I helped produce them. I'm Mayo Kadanga, a
> statistician-economist from Togo, and I spent two years inside the national statistics
> office watching researchers and journalists hunt for numbers page by page."

### 2. What I built (0:18–0:32)
*Screen: Sika home, clean, suggestion chips visible.*

> "So I built Sika. Ask West Africa's economy anything, and get the official answer, cited
> to the exact document and page. One thousand and fifty-one observations, from nine
> official publications, extracted and made queryable."

### 3. Core demo (0:32–1:05)
*Click the "Inflation au Togo" chip. Let it render fully. Hover a chart point to show the tooltip.*

> "Ask in French. Sika answers with the official figures — inflation at 0.1 percent in June
> 2026, down from 0.4 in May. Every number carries its source. Hover any point on the chart:
> document, and page. Nothing here was typed by hand, and nothing was invented."

### 4. The trust feature (1:05–1:28)
*Type: "Quelle est l'inflation en 2025 ?" — the honest refusal.*

> "And here's the feature I'm proudest of. 2025 isn't in the corpus yet. So Sika says so.
> It doesn't reach for the nearest number and hope you won't check. A figure without a
> source is worthless, and that rule is enforced in the database schema itself — source
> document and page are NOT NULL on every single observation. An uncited number cannot
> physically exist in Sika."

### 5. How I used Codex (1:28–2:05)
*Screen: split — AGENTS.md and the docs/ folder on one side, a Codex session on the other.
Then `git log --oneline` scrolling through 26 commits.*

> "Here's how I used Codex. I didn't prompt it ad hoc. I wrote the specifications first: a
> product requirements doc, five user personas, a data specification, and AGENTS.md — an
> agent contract that Codex reads at the start of every session. It sets hard rules: never
> invent a number, SELECT-only SQL, no new frameworks mid-build, small imperative commits.
> Then I wrote eighteen tickets, each with its own definition of done, and Codex shipped
> against them. Twenty-six commits. The extraction pipeline, the API, the tests, the
> deployment — that's the receipt, and it's all in the repo."

### 6. How I used GPT-5.6 (2:05–2:32)
*Screen: the Codex model selector showing 5.6, then the inflation answer again.*

> "And here's how I used GPT-5.6 specifically. Two ways. First, it's the model running
> inside Codex that wrote this code. Second, it does the hard reading — parsing French
> statistical tables out of PDFs, normalizing formats like 'one space two three four comma
> five' into real numbers, and mapping inconsistent labels onto canonical indicators.
> It also caught a bug I'd have shipped: the inflation indicator was silently mixing the
> national headline index with seventy-four sub-category rows. GPT-5.6 diagnosed it, and
> the fix corrected a figure that was wrong on screen."

### 7. Impact and close (2:32–2:45)
*Screen: back to Sika, slow scroll over the Sources panel.*

> "Eight countries, one central bank, a hundred and forty million people share these
> statistics. Sika makes them usable at the speed people actually work. From evidence to
> decisions. Thank you."

---

## After recording

- [ ] Watch the whole thing once, with audio, start to finish
- [ ] Confirm the voiceover says the words "Codex" and "GPT-5.6" clearly and separately
- [ ] Export 1080p
- [ ] Upload to YouTube as **Unlisted** (public also fine — private is NOT)
- [ ] Wait for processing to finish, then open the link in an incognito window
- [ ] Paste into Devpost, then confirm the project shows **Submitted**, not draft

## If you're short on time or energy

Shots 1, 3, 5 and 6 are the ones that matter — problem, demo, Codex, GPT-5.6. Shots 2, 4
and 7 can be trimmed to a sentence each. A 2-minute video that clearly answers the three
required questions beats a polished 3-minute one that doesn't.

Record each shot separately. A fumbled take costs you one shot, not the whole video.
