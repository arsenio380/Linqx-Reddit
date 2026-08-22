"""Listen for people in LA who might want Linqx, and ping Slack.

What it does, in plain English:
  1. Reads plan.yaml to learn which subreddits to watch (listen_subs)
     and what kinds of things to look for (listen_keywords).
  2. Pulls the newest posts from each of those subreddits (read-only).
  3. Skips any post we've already looked at (seen_post_ids in state.json).
  4. Asks Claude, one post at a time, "is this someone looking to meet
     people, new to LA, or struggling socially?" -> yes/no + one reason.
  5. Sorts the "yes" posts newest-first, flags anything under 2 hours old
     as worth jumping on right now, and sends ONE compact Slack message.
  6. Remembers every post it saw, so the next run doesn't repeat itself.

Try it out:
    python listener.py --dry-run     # preview — sends nothing, saves nothing
    python listener.py               # run for real

Needs an Anthropic API key in the environment (or your .env file):
    ANTHROPIC_API_KEY=sk-ant-...
"""

import datetime
import os

import yaml
from dotenv import load_dotenv

import cli
import reddit
import slack
import state

# Load .env so ANTHROPIC_API_KEY (and SLACK_WEBHOOK_URL) are available.
load_dotenv()

# plan.yaml lives next to this script, so cron can find it from anywhere.
PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.yaml")

# The Claude model that judges each post.
MODEL = "claude-sonnet-4-6"

# How many newest posts to pull from each subreddit per run.
POSTS_PER_SUB = 15

# Posts newer than this many hours get the "hit this now" flag.
HIT_NOW_HOURS = 2


def load_plan():
    """Read plan.yaml and return (listen_subs, listen_keywords)."""
    with open(PLAN_FILE, "r", encoding="utf-8") as handle:
        plan = yaml.safe_load(handle)
    return plan.get("listen_subs", []), plan.get("listen_keywords", [])


def hours_old(created_utc):
    """How many hours ago the post was created (0.0 if unknown)."""
    if not created_utc:
        return 0.0
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return max(0.0, (now - created_utc) / 3600.0)


def age_label(created_utc):
    """A short human age like '35m', '3h', or '2d'."""
    hours = hours_old(created_utc)
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 24:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


def judge(client, post, keywords):
    """Ask Claude if this post is a good fit. Returns (is_hit, reason).

    On any error we return (False, ...) so one bad post can't crash the run.
    """
    prompt = (
        "You help a small app called Linqx that helps people in Los Angeles "
        "make friends. Read this Reddit post and decide if the author is "
        "someone looking to meet people, new to LA, or struggling socially. "
        f"Signals to watch for: {', '.join(keywords)}.\n\n"
        f"Subreddit: r/{post.get('subreddit')}\n"
        f"Title: {post.get('title')}\n\n"
        "Answer on ONE line in exactly this format:\n"
        "YES - <short reason>   (if they'd likely want Linqx)\n"
        "NO - <short reason>    (otherwise)"
    )
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = response.content[0].text.strip()
    except Exception as error:  # noqa: BLE001 — never let one post crash us
        return False, f"(couldn't judge: {error})"

    is_hit = answer.upper().startswith("YES")
    # Strip the leading "YES -" / "NO -" so we're left with just the reason.
    reason = answer.split("-", 1)[1].strip() if "-" in answer else answer
    return is_hit, reason


def build_message(hits):
    """Turn the list of hits into one compact Slack message."""
    lines = [f"👂 *{len(hits)} LA post(s) worth a look:*", ""]
    for hit in hits:
        post = hit["post"]
        flag = "🔥 *hit this now* " if hit["hit_now"] else ""
        lines.append(
            f"{flag}• *{post['title']}*  "
            f"(r/{post['subreddit']}, {age_label(post['created_utc'])} old)\n"
            f"    {hit['reason']}\n"
            f"    {post['permalink']}"
        )
    return "\n".join(lines)


def main():
    args = cli.parse_args("Listen for LA posts worth replying to and ping Slack.")

    listen_subs, listen_keywords = load_plan()
    data = state.load()
    seen = set(data["seen_post_ids"])

    # 1) Pull newest posts from each watched sub, skipping ones we've seen.
    fresh = []
    for sub in listen_subs:
        for post in reddit.new_posts(sub, limit=POSTS_PER_SUB):
            if post["id"] and post["id"] not in seen:
                seen.add(post["id"])  # dedupe within this run too
                fresh.append(post)

    if not fresh:
        print("No new posts since last run.")
        return

    # 2) Ask Claude about each fresh post.
    from anthropic import Anthropic  # imported here so --help works without it

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — add it to your .env and try again.")
        return
    client = Anthropic()

    hits = []
    for post in fresh:
        is_hit, reason = judge(client, post, listen_keywords)
        if is_hit:
            hits.append(
                {
                    "post": post,
                    "reason": reason,
                    "hit_now": hours_old(post["created_utc"]) < HIT_NOW_HOURS,
                }
            )

    # 3) Sort hits newest-first.
    hits.sort(key=lambda h: h["post"]["created_utc"] or 0, reverse=True)

    # 4) Send one compact Slack message (or preview it in dry-run).
    if hits:
        message = build_message(hits)
        if args.dry_run:
            print("[dry-run] Would post to Slack:\n")
            print(message)
        else:
            slack.send(message)
    else:
        print(f"Looked at {len(fresh)} new post(s); none were a fit.")

    # 5) Remember every post we looked at, so we don't repeat next run.
    def remember():
        data["seen_post_ids"] = sorted(seen)
        state.save(data)

    cli.act(args, "save the seen post IDs to state.json", remember)


if __name__ == "__main__":
    main()
