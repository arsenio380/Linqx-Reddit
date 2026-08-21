"""Example cron script — a template for the real ones you'll write next.

It shows how the pieces fit together:
  * cli.py      -> the --dry-run flag and logging
  * reddit.py   -> read public Reddit data
  * state.py    -> remember things between runs
  * slack.py    -> send a Slack message

Try it out:
    python example_script.py --dry-run     # preview, sends nothing
    python example_script.py               # runs for real

Copy this file to start a new script (e.g. daily_summary.py).
"""

import datetime

import cli
import reddit
import slack
import state


def main():
    args = cli.parse_args("Example: report new posts in a subreddit to Slack.")

    # 1) Load state (created with defaults on first run).
    data = state.load()

    # 2) Read some public Reddit data (read-only, throttled).
    posts = reddit.new_posts("test", limit=5)
    fresh = [p for p in posts if p["id"] not in data["seen_post_ids"]]

    # 3) Build a message.
    if fresh:
        lines = [f"- {p['title']} ({p['permalink']})" for p in fresh]
        message = "New posts:\n" + "\n".join(lines)
    else:
        message = "No new posts since last run."

    # 4) Do the side effects, respecting --dry-run.
    cli.act(args, "post the update to Slack", lambda: slack.send(message))

    def remember():
        for post in fresh:
            data["seen_post_ids"].append(post["id"])
        data["last_health_check"] = datetime.datetime.now().isoformat(timespec="seconds")
        state.save(data)

    cli.act(args, "save updated state", remember)


if __name__ == "__main__":
    main()
