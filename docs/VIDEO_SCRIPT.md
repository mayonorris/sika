# Demo video script — Sika (target: 2:50, hard cap 3:00)

Language: English (judges). Voice: calm, confident, no rush. Record screen at 1920x1080,
hide bookmarks bar, close other tabs, dark UI full screen. Tool: OBS Studio (free) or
Xbox Game Bar (Win+G). Record voice separately if easier, then assemble in Clipchamp
(free, built into Windows 11). Upload to YouTube as UNLISTED, test link in a private window.

Before recording: seed DB is purged of fixtures; API key with FRESH quota (reserve key);
run through all shots once WITHOUT recording to warm caches and confirm quota.

---

## Shot list

### 1. The problem (0:00 - 0:20) — screen: an INSEED PDF bulletin, scrolling tables
> "This is how West Africa's official economic data lives today: rigorous, public,
> and buried in PDF bulletins. I know, because I helped produce them. I'm Mayo Kadanga,
> a statistician-economist from Togo, and I spent two years inside the national
> statistics office watching researchers and journalists hunt for numbers page by page."

### 2. The reveal (0:20 - 0:35) — screen: Sika home, clean, example chips visible
> "So during Build Week I built Sika: ask West Africa's economy anything,
> and get the official answer, cited to the exact page."

### 3. Core demo, French (0:35 - 1:05) — type GQ1: "Quelle est l'évolution de l'inflation au Togo ?"
Let the answer render fully; hover the chart to show per-point source tooltips.
> "Ask in French: inflation in Togo. Sika answers with the official figures from the
> June 2026 INSEED release, every number cited, and draws the series on the fly.
> Hover any point: document and page."

### 4. Core demo, English (1:05 - 1:25) — type GQ2: "What is the latest industrial production index for Togo?"
> "Ask in English, it answers in English. Eleven years of industrial production history,
> extracted from official workbooks with zero transcription error."

### 5. The trust feature (1:25 - 1:45) — type GQ7: "Quel sera le PIB du Togo en 2030 ?"
> "And here is the feature I'm proudest of: Sika refuses to invent. No forecast in the
> official data means no forecast in the answer. A number without a source is worthless."

### 6. Brief + sources (1:45 - 2:05) — click "Générer un brief" (inflation), then Sources panel
> "One click turns the data into a professional economic brief, every figure sourced.
> And the Sources panel shows exactly which official publications power every answer."

### 7. Built with Codex (2:05 - 2:35) — screen: split between AGENTS.md/docs in editor and a Codex session; then git log
> "Sika was built in four days with Codex and GPT-5.6. I wrote the specs: a PRD, a data
> specification, an agent contract in AGENTS.md. Codex shipped the tickets: the extraction
> pipeline, the API hardening, the tests, the resilience against free-tier rate limits.
> The commit history is the receipt."

### 8. Impact + close (2:35 - 2:55) — screen: back to Sika, slow zoom on tagline
> "Eight countries, one central bank, one hundred and forty million people share these
> statistics. Sika makes them usable at digital speed, for researchers, journalists,
> fintechs and students. From evidence to decisions. Thank you."

---

## Recording checklist

- [ ] Fixtures purged, `validate.py` clean, golden questions pass live
- [ ] Fresh/reserve API key in .env (quota intact), one warm-up question done
- [ ] Browser zoom 110%, French keyboard ready, questions in a notepad to paste
- [ ] Mic test 10 s, no fan noise, phone silenced
- [ ] Record shots separately; a failed take costs one question of quota, keep retakes for shots 3, 4, 6
- [ ] Assemble, export 1080p, watch once fully
- [ ] Upload YouTube UNLISTED, open link in private window, then paste into Devpost

## Fallback plan

If API quota dies mid-recording: record shots 3-6 with the no-API fallback mode
(cited rows + charts still work) and say "powered by cached extractions" instead of
hiding it; honesty on constraints is part of this project's story.
