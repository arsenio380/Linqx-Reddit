# Linqx-Reddit

A small Reddit **launch assistant**: plain Python 3.11 scripts, run by cron.
No web framework, no database — just files.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then paste your real Slack webhook URL into .env
```

## The building blocks

| File          | What it does                                                             |
|---------------|--------------------------------------------------------------------------|
| `slack.py`    | `send(text, blocks=None)` — post to a Slack webhook; never crashes.      |
| `reddit.py`   | Read-only public Reddit JSON (no login): new/hot posts, karma, shadowban.|
| `state.py`    | `load()` / `save()` the launch state in `state.json`.                    |
| `cli.py`      | Shared `--dry-run` flag + logging for your scripts.                      |
| `example_script.py` | Template showing how the pieces fit together.                     |

`.env` and `state.json` are git-ignored — they never get committed.

## Writing a new cron script

Copy `example_script.py` and adjust it. Every script supports a dry run:

```bash
python your_script.py --dry-run   # preview — sends nothing, changes nothing
python your_script.py             # run for real
```

## Scheduling with cron

Point cron at the venv's Python and the script's full path, e.g. daily at 9am:

```cron
0 9 * * *  cd /path/to/linqx-reddit && .venv/bin/python daily_summary.py >> cron.log 2>&1
```
