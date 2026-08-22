"""Daily launch coach — one casual Slack nudge about what to do today.

Run me once a day (cron). I:
  1. Read plan.yaml (the 14-day schedule) and state.json.
  2. Work out today's plan day from the calendar.
  3. If the account is shadowbanned (flag set by health.py), say stop
     posting and appeal instead of handing out today's task.
  4. Send ONE friendly, lowercase Slack message: today's task + the date.

Preview without touching Slack:
    python coach.py --dry-run
"""

import datetime

import cli
import slack
import state

import yaml

# plan.yaml lives next to this script, same as state.json does.
import os
PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.yaml")

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


def resolve_day(plan, today, start_date):
    """Figure out which plan day we're on, straight off the calendar.

    Returns a dict describing today: the plan entry plus flags for the
    special cases (before launch, past the end).
    """
    days = plan["days"]
    last_day = len(days)

    # Calendar day: day 1 is start_date itself.
    nominal_day = (today - start_date).days + 1

    prelaunch = nominal_day < 1
    finished = nominal_day > last_day

    # Clamp into the real range so we can always show *something* useful.
    shown_day = min(max(nominal_day, 1), last_day)
    entry = days[shown_day - 1]

    return {
        "start_date": start_date,
        "today": today,
        "shown_day": shown_day,
        "last_day": last_day,
        "entry": entry,
        "prelaunch": prelaunch,
        "finished": finished,
    }


def format_entry(entry):
    """Build the day's detail block: goal, where, post (if any), when (if any).

    Everything stays lowercase and casual — except the post text, which is
    printed exactly as written in plan.yaml so it can be copy-pasted straight
    to reddit.
    """
    lines = [f"goal: {entry['goal']}"]

    where = entry.get("where")
    if where:
        lines.append(f"where: {where}")

    post = entry.get("post")
    if post:
        # Print the post verbatim — no lowercasing, no trimming.
        lines.append("")
        lines.append(post.rstrip("\n"))

    when = entry.get("when")
    if when:
        lines.append("")
        lines.append(f"when: {when}")

    return "\n".join(lines)


def build_message(day, data):
    """Compose the one casual, lowercase Slack message for today."""
    # Shadowban overrides everything — kill the task, send them to appeal.
    if data.get("shadowbanned"):
        return (
            "⚠️ hold up — looks like the account got shadowbanned.\n\n"
            "stop posting and commenting right now, it's all invisible anyway.\n"
            f"go to r/ShadowBan and file an appeal: {SHADOWBAN_SUB}\n\n"
            "don't touch today's task til this clears. i'll keep an eye out."
        )

    date_str = day["today"].isoformat()
    details = format_entry(day["entry"])

    if day["prelaunch"]:
        return (
            f"hey! we haven't officially started yet 🌱 ({date_str})\n\n"
            f"day 1 kicks off {day['start_date']}. here's the plan for it:\n\n"
            f"{details}"
        )
    if day["finished"]:
        return (
            f"that's a wrap on the plan 🎉 nice work. ({date_str})\n\n"
            f"today (day {day['shown_day']}, keeping it going):\n\n"
            f"{details}"
        )
    return (
        f"morning! day {day['shown_day']} of the reddit grind 🌱 ({date_str})\n\n"
        f"{details}"
    )


def add_arguments(parser):
    parser.add_argument(
        "--skip-if-done",
        action="store_true",
        help=(
            "Don't send anything if today's plan day is already marked "
            "done (in completed_days). Used by the evening reminder run."
        ),
    )


def main():
    args = cli.parse_args(
        "Send today's casual Reddit-launch nudge to Slack.", add_arguments
    )

    plan = load_plan()
    data = state.load()
    today = datetime.date.today()
    start_date = parse_start_date(plan)

    day = resolve_day(plan, today, start_date)

    # Evening reminder: stay quiet if today's task is already done.
    if args.skip_if_done and day["shown_day"] in (data.get("completed_days") or []):
        print(f"day {day['shown_day']} already marked done — staying quiet.")
        return

    message = build_message(day, data)

    # Preview the exact message so --dry-run is actually useful.
    print(message)
    print("-" * 40)

    cli.act(args, "post today's nudge to Slack", lambda: slack.send(message))


if __name__ == "__main__":
    main()
