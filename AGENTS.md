# A Study Bot

Self-hostable Discord study bot (Python, `discord.ext.commands`) for HKDSE
practice. It analyzes photos of **already-marked** exam papers to detect weak
syllabus topics, records them per Discord user in SQLite, runs interactive
3-question quizzes, and shows weakness and accuracy stats.

## Current status

Feature-complete and MIT-licensed (see `README.md`). The bot consumes a
`DISCORD_TOKEN` and an `OPENROUTER_API_KEY` from `.env`; users bring their own
credentials and run their own instance anywhere. The Discord **Message Content
intent** must be enabled in the developer portal or typed quiz answers will not
be received.

## Layout

- `main.py` — builds the bot (`DseBot`), attaches shared `bot.db` / `bot.ai`, auto-loads
  every cog module found in `cogs/`, syncs the slash command tree once on ready.
- `config.py` — loads `.env` via `python-dotenv` (`DISCORD_TOKEN`, `OPENROUTER_API_KEY`,
  `DB_PATH`), fails fast when keys are missing.
- `services/ai_client.py` — **the only module that talks to models.** One OpenRouter
  client; Qwen (`qwen/qwen3-vl-30b-a3b-instruct`) is used only for
  `analyze_paper_image()`; DeepSeek (`deepseek/deepseek-chat`) handles `generate_quiz()`
  and `grade_answer()`. Model IDs are module constants.
- `services/database.py` — SQLite via `aiosqlite`. WAL mode, foreign keys, busy timeout,
  and a **fresh short-lived connection per operation** (never one long-lived shared
  connection). Tables: `users`, `topic_weakness`, `quiz_history`; weakness/history rows
  are keyed by `(user_id, subject, topic_name)` so each subject stays separate. Startup
  migrates pre-subject databases (old rows get subject `''`).
- `utils/pdf_handler.py` — image preprocessing (PNG/JPEG/WebP, EXIF orientation, resize
  to max 4096 px, re-encode, 25 MB cap). PDFs are intentionally rejected in v1.
- `utils/subjects.py` — canonical subjects (`Math`, `M2`, `ICT`, `Physics`) and
  `normalize_subject()` mapping model-detected labels onto them.
- `cogs/` — one file per command group: `analyzer.py` (`/analyze`),
  `quiz.py` (`/quiz`), `stats.py` (`/stats`).
- `cogs/analyzer.py` also registers the **"Analyze Paper"** message context menu, which
  analyzes every image attached to a message in one Qwen vision call (multi-page papers).
- `tests/test_core.py` — offline stdlib `unittest` checks (no Discord/network).

## Conventions

- Plugin-friendly: adding a cog = adding `cogs/<name>.py` containing one class that
  subclasses `commands.Cog`. The loader registers it automatically on restart.
- Shared services are reached as `self.bot.db` and `self.bot.ai`; cogs must not create
  their own AI/DB clients.
- Slash commands only (no prefix commands). `/analyze` and `/stats` reply ephemeral;
  `/quiz` stays visible in-channel because users type short answers as chat messages.
- `/analyze` (and the "Analyze Paper" context menu) now shows the detected weak topics in
  a review screen with a topic multi-select and **does not write to the database until
  the user confirms**. "Save selected" records only the ticked topics; "Discard" or a
  5-minute timeout records nothing. Tests/commands that analyze papers must handle this
  confirmation step.
- One active quiz session per user at a time, tracked in-memory in the quiz cog.
- MCQ answers use Discord buttons; short/long answers are typed in chat and graded by
  DeepSeek. After each question (and again in an end-of-quiz answer key) the bot shows the
  correct/expected answer. Typed answers require the **Message Content intent** enabled
  in the Discord developer portal.
- The end-of-quiz answer key has **Flag Q1..Q3** buttons. Clicking one opens a short form
  and saves the user's objection to the `answer_reports` table (`record_answer_report()`)
  for later review; it does not alter quiz history or stats.
- `/quiz` and `/stats` accept an optional `subject` choice; when omitted, `/quiz` uses the
  most recently analyzed subject and `/stats` shows a per-subject summary.
- AI providers return JSON; parse it only through `extract_json()` in
  `services/ai_client.py` (handles markdown fences and prose around JSON).
- Errors surfaced to users should be human-readable (`AIError`, `AttachmentError`);
  never leak stack traces into Discord messages.
- `AIClient` translates provider failures: HTTP 402 becomes "out of OpenRouter credits",
  HTTP 401 becomes "invalid API key", and other errors are prefixed with the model name
  (e.g. `Qwen (via OpenRouter)` or `DeepSeek (via OpenRouter)`).
- Follow the repo's ponytail rules: no new dependencies, abstractions, or boilerplate
  unless the change genuinely needs them; reuse the existing helpers first.

## Running locally

```sh
python -m unittest discover -s tests -v   # offline checks
python main.py                            # requires a filled-in .env
```

`.env.example` documents the variables and the default `DB_PATH`
(`data/bot.db`). Secrets and local data (`.env`, `keys`, `data/`) are
gitignored and dockerignored; never commit or copy them into an image.

## Deployment

Running the bot anywhere (local, Docker, VPS, container platform) is documented
in `README.md`.
