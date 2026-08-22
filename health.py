"""Daily account health check for the Reddit launch.

What it does, once per run:
  1. Reads the account name from plan.yaml.
  2. Looks up that account's current karma and runs a shadowban check
     (both read-only, via reddit.py).
  3. Appends today's karma snapshot to karma_history in state.json and
     records a health status.
  4. Sends an *urgent* Slack alert only if something is wrong:
       * the account looks shadowbanned, or
       * total karma dropped since the previous check.
     When everything is fine it stays silent — no Slack message at all.

Preview without touching Slack or state:
    python health.py --dry-run
Run for real:
    python health.py
"""

import datetime
import logging
import os

import yaml

import cli
import reddit
import slack
import state

log = logging.getLogger(__name__)

# plan.yaml lives next to this script, so cron finds it regardless of the
# working directory it happens to run from.
PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.yaml")


def load_account():
    """Return the account name from plan.yaml, or None if unavailable."""
    try:
        with open(PLAN_FILE, "r", encoding="utf-8") as handle:
            plan = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as error:
        log.error("Couldn't read %s: %s", PLAN_FILE, error)
        return None

    account = plan.get("account")
    if not account:
        log.error("No 'account' set in %s.", PLAN_FILE)
        return None
    return account


def last_karma_before_today(karma_history, today):
    """Return the most recent total_karma recorded on a day before today.

    This is our "yesterday" baseline for detecting a karma drop. Snapshots
    already written earlier *today* are skipped so repeated runs on the
    same day don't compare against themselves. Returns None if there's no
    earlier snapshot to compare against.
    """
    for snapshot in reversed(karma_history):
        if snapshot.get("date") != today and snapshot.get("total_karma") is not None:
            return snapshot["total_karma"]
    return None


def record_today(karma_history, today, total_karma):
    """Append (or update) today's karma snapshot in karma_history.

    Running twice in one day updates the existing entry instead of adding
    a duplicate, keeping the history one-snapshot-per-day.
    """
    if karma_history and karma_history[-1].get("date") == today:
        karma_history[-1]["total_karma"] = total_karma
    else:
        karma_history.append({"date": today, "total_karma": total_karma})


def build_alert(account, shadowbanned, total_karma, previous_karma, previous_date):
    """Return an urgent Slack message string, or None if all is well."""
    problems = []

    if shadowbanned:
        problems.append(
            f":rotating_light: *u/{account} appears SHADOWBANNED.*\n"
            f"The public profile returns a 404 while logged out, which is how a "
            f"shadowban looks from the outside.\n"
            f"What to do now:\n"
            f"  1. Confirm: open https://www.reddit.com/user/{account} in a "
            f"logged-out / private window. If it 404s, it's real.\n"
            f"  2. Stop posting and commenting immediately — more activity can "
            f"make an appeal harder.\n"
            f"  3. Appeal at https://www.reddit.com/appeals and post in "
            f"r/ShadowBan to double-check."
        )

    if (
        total_karma is not None
        and previous_karma is not None
        and total_karma < previous_karma
    ):
        drop = previous_karma - total_karma
        since = f" (recorded {previous_date})" if previous_date else ""
        problems.append(
            f":chart_with_downwards_trend: *Karma dropped for u/{account}.*\n"
            f"Total karma fell from {previous_karma} to {total_karma} "
            f"(down {drop}){since}.\n"
            f"What to do now:\n"
            f"  1. Check https://www.reddit.com/user/{account} for removed or "
            f"heavily downvoted posts/comments.\n"
            f"  2. If something was removed by a mod, read that sub's rules "
            f"before engaging again — don't repost the same thing.\n"
            f"  3. Slow down and keep contributions genuinely helpful to rebuild."
        )

    if not problems:
        return None

    header = f"*Reddit launch health alert — u/{account}*"
    return header + "\n\n" + "\n\n".join(problems)


def main():
    args = cli.parse_args("Check Reddit account health and alert on trouble.")

    account = load_account()
    if not account:
        # Nothing we can safely do without an account name.
        print("No account configured in plan.yaml — nothing to check.")
        return

    # 1) Read current karma and shadowban status (read-only, throttled).
    karma = reddit.user_karma(account)
    total_karma = karma["total_karma"] if karma else None
    shadowbanned = reddit.is_shadowbanned(account)

    log.info(
        "u/%s: total_karma=%s shadowbanned=%s",
        account,
        total_karma,
        shadowbanned,
    )

    # 2) Work out the "yesterday" baseline before touching the history.
    data = state.load()
    today = datetime.date.today().isoformat()
    previous_karma = last_karma_before_today(data["karma_history"], today)
    previous_date = None
    for snapshot in reversed(data["karma_history"]):
        if snapshot.get("date") != today and snapshot.get("total_karma") is not None:
            previous_date = snapshot.get("date")
            break

    # 3) Decide whether anything is wrong.
    message = build_alert(
        account, shadowbanned, total_karma, previous_karma, previous_date
    )

    if shadowbanned is None and total_karma is None:
        status = "unknown"  # Couldn't read Reddit at all.
    elif message is not None:
        status = "alert"
    else:
        status = "ok"

    # 4) Send the urgent alert only when there's a problem. Silent otherwise.
    if message is not None:
        if args.dry_run:
            print("[dry-run] Would send this urgent Slack alert:\n")
            print(message)
        else:
            slack.send(message)
    else:
        print(f"Health {status}: nothing wrong, staying silent.")

    # 5) Record the snapshot and health status in state.json.
    def remember():
        if total_karma is not None:
            record_today(data["karma_history"], today, total_karma)
        data["health"] = {
            "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "date": today,
            "account": account,
            "total_karma": total_karma,
            "shadowbanned": shadowbanned,
            "status": status,
            "alerted": message is not None,
        }
        data["last_health_check"] = data["health"]["checked_at"]
        state.save(data)

    cli.act(args, "record today's karma and health status", remember)


if __name__ == "__main__":
    main()
