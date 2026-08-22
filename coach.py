"""Daily launch coach — one casual Slack nudge about what to do today.

Run me once a day (cron). I:
  1. Read plan.yaml (the 14-day schedule) and state.json (where we're up to).
  2. Work out today's plan day from the calendar, then bend it by the rules:
       * behind on karma  -> don't advance; repeat the karma task and shift
         the rest of the plan back a day.
       * shadowbanned      -> throw out the task; say stop posting and appeal.
  3. Nudge if yesterday never got marked done.
  4. Pull ~10 fresh "rising" threads from the karma subs to comment on.
  5. Send ONE friendly, lowercase Slack message with all of it.

Preview without touching Slack or state:
    python coach.py --dry-run
"""

import datetime
import time

import cli
import reddit
import slack
import state

import yaml

# plan.yaml lives next to this script, same as state.json does.
import os
PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.yaml")

# Rising-thread filters. "roughly 50-200 upvotes" and under 2 hours old —
# the sweet spot where a thread is climbing but not yet saturated.
MIN_SCORE = 50
MAX_SCORE = 200
MAX_AGE_SECONDS = 2 * 60 * 60  # 2 hours
WANT_THREADS = 10

# Where to appeal a shadowban.
SHADOWBAN_SUB = "https://www.reddit.com/r/ShadowBan/"


def load_plan():
    """Read plan.yaml into a dict."""
    with open(PLAN_FILE, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_start_date(plan):
    """Return the launch start_date as a datetime.date.

    PyYAML already turns an ISO date like 2026-08-22 into a date object,
    but tolerate a plain string too.
    """
    value = plan["start_date"]
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def current_karma(account, data):
    """Best guess at total karma right now.

    Try Reddit live; if that fails, fall back to the most recent snapshot
    in karma_history; if there's nothing at all, return None (unknown).
    """
    live = reddit.user_karma(account)
    if live is not None:
        return live["total_karma"]

    history = data.get("karma_history") or []
    if history:
        return history[-1].get("total_karma")
    return None


def pick_rising_threads(subs):
    """Gather rising threads across the karma subs and keep the good ones.

    Keeps threads that are under 2 hours old and sitting in the ~50-200
    upvote range, then returns the top WANT_THREADS by score (busiest
    first). Skips anything we can't read.
    """
    now = time.time()
    keepers = []
    for sub in subs:
        for post in reddit.rising_posts(sub):
            created = post.get("created_utc")
            score = post.get("score")
            if created is None or score is None:
                continue
            if now - created > MAX_AGE_SECONDS:
                continue
            if not (MIN_SCORE <= score <= MAX_SCORE):
                continue
            keepers.append(post)

    keepers.sort(key=lambda p: p["score"], reverse=True)
    return keepers[:WANT_THREADS]


def resolve_day(plan, data, today, start_date):
    """Figure out which plan day we're actually on.

    Returns a dict describing today: the plan entry, karma numbers, and
    flags for the special cases (before launch, past the end, behind on
    karma). Does NOT mutate state — that's decided separately so --dry-run
    stays side-effect free.
    """
    days = plan["days"]
    last_day = len(days)

    # Calendar day: day 1 is start_date itself.
    nominal_day = (today - start_date).days + 1

    # Slip the schedule back by however many days we've fallen behind.
    offset = data.get("day_offset", 0)
    effective_day = nominal_day - offset

    prelaunch = nominal_day < 1
    finished = effective_day > last_day

    # Clamp into the real range so we can always show *something* useful.
    shown_day = min(max(effective_day, 1), last_day)
    entry = days[shown_day - 1]

    target = entry.get("karma_target", 0)
    karma = current_karma(plan["account"], data)
    # Unknown karma -> don't accuse them of being behind.
    behind = karma is not None and karma < target and not prelaunch and not finished

    return {
        "start_date": start_date,
        "nominal_day": nominal_day,
        "effective_day": effective_day,
        "shown_day": shown_day,
        "last_day": last_day,
        "entry": entry,
        "target": target,
        "karma": karma,
        "behind": behind,
        "prelaunch": prelaunch,
        "finished": finished,
    }


def overnight_hits(data, today):
    """Return listener hits recorded since yesterday morning.

    The listener writes into state["listener_hits"] with a "found_at"
    ISO timestamp. "Overnight" = anything from the last ~24 hours. Hits
    without a timestamp are included (better to over-report a lead).
    """
    cutoff = datetime.datetime.combine(today, datetime.time()) - datetime.timedelta(hours=12)
    fresh = []
    for hit in data.get("listener_hits") or []:
        stamp = hit.get("found_at")
        if not stamp:
            fresh.append(hit)
            continue
        try:
            when = datetime.datetime.fromisoformat(stamp)
        except ValueError:
            fresh.append(hit)
            continue
        if when >= cutoff:
            fresh.append(hit)
    return fresh


def build_message(day, threads, hits, data):
    """Compose the one casual, lowercase Slack message."""
    # Shadowban overrides everything — kill the task, send them to appeal.
    if data.get("shadowbanned"):
        return (
            "⚠️ hold up — looks like the account got shadowbanned.\n\n"
            "stop posting and commenting right now, it's all invisible anyway.\n"
            f"go to r/ShadowBan and file an appeal: {SHADOWBAN_SUB}\n\n"
            "don't touch today's task til this clears. i'll keep an eye out."
        )

    lines = []

    # Greeting + which day.
    if day["prelaunch"]:
        lines.append("hey! we haven't officially started yet 🌱")
        lines.append(f"day 1 kicks off {day['start_date']}. here's the plan for it:")
        lines.append("")
        lines.append(f"day 1: {day['entry']['goal']}")
    elif day["finished"]:
        lines.append("that's a wrap on the 14-day plan 🎉 nice work.")
        lines.append(f"today (day {day['shown_day']}, keeping it going): {day['entry']['goal']}")
    else:
        lines.append(f"morning! day {day['shown_day']} of the reddit grind 🌱")
        lines.append("")
        lines.append(f"today: {day['entry']['goal']}")

    # Karma vs target.
    lines.append("")
    if day["karma"] is None:
        lines.append("couldn't read your karma right now — check manually if you can")
    elif day["behind"]:
        short = day["target"] - day["karma"]
        lines.append(
            f"karma: {day['karma']} / need {day['target']} — {short} short 😬"
        )
        lines.append(
            "not advancing today. keep hammering the karma task and we'll "
            "push the rest of the plan back a day til you catch up."
        )
    else:
        lines.append(f"karma: {day['karma']} / {day['target']} ✅ you're good")

    # Nudge if yesterday never got marked done.
    prev_day = day["shown_day"] - 1
    if prev_day >= 1 and prev_day not in (data.get("completed_days") or []):
        lines.append("")
        lines.append(f"(btw didn't see day {prev_day} marked done — circle back if you skipped it)")

    # Rising threads to jump on.
    lines.append("")
    if threads:
        lines.append(f"{len(threads)} rising threads to comment on (all fresh, climbing):")
        for post in threads:
            lines.append(f"• {post['title']}  (r/{post['subreddit']}) → {post['permalink']}")
    else:
        lines.append("no rising threads in the sweet spot right now — try again in an hour")

    # Overnight listener hits.
    if hits:
        lines.append("")
        lines.append("overnight the listener caught a few leads:")
        for hit in hits:
            kw = hit.get("keyword")
            tag = f'"{kw}" — ' if kw else ""
            sub = hit.get("subreddit", "?")
            title = hit.get("title", "(no title)")
            link = hit.get("permalink", "")
            lines.append(f"• {tag}{title}  (r/{sub}) → {link}")

    lines.append("")
    lines.append("go get em 🚀")
    return "\n".join(lines)


def main():
    args = cli.parse_args("Send today's casual Reddit-launch nudge to Slack.")

    plan = load_plan()
    data = state.load()
    today = datetime.date.today()
    start_date = parse_start_date(plan)

    day = resolve_day(plan, data, today, start_date)

    # Only bother fetching threads when there's a task to do (skip on a
    # shadowban — we're telling them to stop posting).
    if data.get("shadowbanned"):
        threads, hits = [], []
    else:
        threads = pick_rising_threads(plan["karma_subs"])
        hits = overnight_hits(data, today)

    message = build_message(day, threads, hits, data)

    # Preview the exact message so --dry-run is actually useful.
    print(message)
    print("-" * 40)

    cli.act(args, "post today's nudge to Slack", lambda: slack.send(message))

    def remember():
        today_iso = today.isoformat()

        # Record a karma snapshot (once per day).
        if day["karma"] is not None:
            history = data.setdefault("karma_history", [])
            if not history or history[-1].get("date") != today_iso:
                history.append({"date": today_iso, "total_karma": day["karma"]})

        # If we're behind, slip the schedule back a day — but only once
        # per calendar day, so re-running can't compound the slip.
        if day["behind"] and data.get("last_offset_date") != today_iso:
            data["day_offset"] = data.get("day_offset", 0) + 1
            data["last_offset_date"] = today_iso

        state.save(data)

    cli.act(args, "save updated state", remember)


if __name__ == "__main__":
    main()
