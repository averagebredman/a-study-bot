# A Study Bot

A self-hostable Discord study bot for practices. Take a photo of an
**already-marked** exam paper and the bot identifies the syllabus topics where
you lost marks, records them per user in SQLite, then runs short interactive
quizzes on exactly those weak spots — and tracks how your accuracy improves
over time.

Anyone can host their own copy: it just needs your own Discord bot token and an
[OpenRouter](https://openrouter.ai) API key. 

## Features

- **"Analyze Paper"** (message context menu) — right-click any message with
  attachments and analyze all its images as one multi-page paper.
- **`/quiz`** — a 3-question quiz on your saved weak topics. Multiple-choice
  questions are answered with buttons; short/long answers are typed in chat
  and graded by AI. The expected answer is shown after each question and again
  in an end-of-quiz answer key.
- **`/stats`** — per-topic weakness counts and quiz accuracy.
- Data is stored per Discord user in SQLite (`data/bot.db` by default), and
  subjects stay separate (`Math`, `English`, `Chemistry`, `Physics`, ...).
- **`/analyze`** — upload one page image of a marked paper; the AI (via
  OpenRouter) names the weak syllabus topics with evidence.<br>
  Note: This function only works on **ONE** image. Analyze Paper function should be used for multiple images.

Models are selected in `services/ai_client.py`: Qwen's vision model reads
papers, and DeepSeek generates quizzes and grades typed answers. Both are
called through one OpenRouter client.

## Prerequisites

1. **A Discord application with a bot user**
   - Create one at the [Discord Developer Portal](https://discord.com/developers/applications).
   - Under **Bot**, create the bot and copy its **token**.
   - Under **Bot**, enable the **Message Content intent** — without it, typed
     quiz answers will never arrive.
   - Under **OAuth2 → URL Generator**, select the `bot` and
     `applications.commands` scopes and the permissions **Send Messages**,
     **Embed Links**, **Attach Files**, and **Use Application Commands**
     (plus **View Channels**). Open the generated invite URL in a server you
     administer.
2. **An OpenRouter API key** — create a free account at
   [openrouter.ai/keys](https://openrouter.ai/keys) and copy the key. The bot
   charges your OpenRouter balance per call.

## Run it yourself

### Local (Python)

Requires Python 3.11+.

```sh
python -m venv .venv
# activate: .venv\Scripts\activate on Windows, source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

Fill `DISCORD_TOKEN` and `OPENROUTER_API_KEY` in `.env`, then:

```sh
python main.py
```

### Docker

```sh
docker compose up -d --build
```

The container reads `.env`, so fill it in first (see above). Quiz results and
stats persist in the `./data` folder next to the compose file. On Linux, that
folder must be writable by the container's UID 1000:

```sh
mkdir -p data
sudo chown 1000:1000 data    # only needed if your user is not UID 1000
```

Docker Desktop on Windows/macOS handles this automatically. Change where the
database lives with the optional `DB_PATH` variable.

### Anywhere else

The bot is a plain Python app, so any always-on host works, for instance a VPS, a cloud
instance, a Raspberry Pi at home, or a container platform. Discord bots need to
stay connected, so pick something that runs 24/7 rather than a server that
sleeps. The two things you must bring are the Discord bot token and the
OpenRouter API key; both are configured purely through environment variables.

## Commands

| Command | What it does | Replies |
| --- | --- | --- |
| `/analyze image:<attachment>` | Analyze one marked-paper page | Ephemeral |
| `Analyze Paper` (context menu) | Analyze every image on a message as one paper | Ephemeral |
| `/quiz [subject]` | 3-question quiz on saved weak topics | In channel (you type answers) |
| `/stats [subject]` | Weaknesses and accuracy per subject | Ephemeral |

With no `subject`, `/quiz` drills your most recently analyzed subject and
`/stats` shows all subjects.

## Troubleshooting

- **Typed quiz answers never arrive** — enable the **Message Content intent**
  in the Discord Developer Portal, then restart the bot.
- **Slash commands don't appear** — global commands can take up to an hour to
  propagate after the first startup. Re-invite the bot if needed.
- **"Out of OpenRouter credits"** — top up your account at
  [openrouter.ai/settings/credits](https://openrouter.ai/settings/credits).
- **Image rejected** — only PNG, JPEG, and WebP photos are accepted (PDFs are
  not supported in v1); images are capped at 25 MB after resizing.
- **Nothing is saved when I analyze** — weak topics are only recorded after you
  press **Save** in the review step.

## Privacy

Paper images are sent to OpenRouter (Qwen vision model) for analysis and are
not stored beyond the SQLite weakness/quiz records. Review the detected topics
before saving so only accurate findings are recorded.

## Contributing

This project is MIT-licensed. Fork it, keep changes small, and make sure the
offline checks pass:

```sh
python -m unittest discover -s tests -v
```

The tests need no Discord account or network access.
