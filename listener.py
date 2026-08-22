"""Listen for people in LA who might want Linqx, and ping Slack.

What it does, in plain English:
  1. Reads plan.yaml to learn which subreddits to watch (listen_subs)
     and what words to look for (listen_keywords).
  2. Pulls the newest posts from each of those subreddits (read-only).
  3. Skips any post we've already looked at (seen_post_ids in state.json).
  4. Checks each post's title and body for any of the keywords
     (case-insensitive). A post is a hit if it matches any keyword.
  5. Sorts the "yes" posts newest-first, flags anything under 2 hours old
     as worth jumping on right now, and sends ONE compact Slack message
     showing which keyword matched so you can judge relevance at a glance.
  6. Remembers every post it saw, so the next run doesn't repeat itself.

Try it out:
    python listener.py --dry-run     # preview — sends nothing, saves nothing
    python listener.py               # run for real
"""

import datetime
import os

import yaml
from dotenv import load_dotenv

import cli
import reddit
import slack
import state

# Load .env so SLACK_WEBHOOK_URL is available.
load_dotenv()

# plan.yaml lives next to this script, so cron can find it from anywhere.
PLAN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.yaml")

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


def match_keyword(post, keywords):
    """Return the first keyword that appears in the post, or None.

    Checks the title and body text (selftext), case-insensitive.
    """
    haystack = f"{post.get('title', '')}\n{post.get('selftext', '')}".lower()
    for keyword in keywords:
        if keyword.lower() in haystack:
            return keyword
    return None


def build_message(hits):
    """Turn the list of hits into one compact Slack message."""
    lines = [f"👂 *{len(hits)} LA post(s) worth a look:*", ""]
    for hit in hits:
        post = hit["post"]
        flag = "🔥 *hit this now* " if hit["hit_now"] else ""
        lines.append(
            f"{flag}• *{post['title']}*  "
            f"(r/{post['subreddit']}, {age_label(post['created_utc'])} old)\n"
            f"    matched: “{hit['keyword']}”\n"
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

    # 2) Keep posts whose title or body matches one of the keywords.
    hits = []
    for post in fresh:
        keyword = match_keyword(post, listen_keywords)
        if keyword:
            hits.append(
                {
                    "post": post,
                    "keyword": keyword,
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
        print(f"Looked at {len(fresh)} new post(s); none matched a keyword.")

    # 5) Remember every post we looked at, so we don't repeat next run.
    def remember():
        data["seen_post_ids"] = sorted(seen)
        state.save(data)

    cli.act(args, "save the seen post IDs to state.json", remember)


if __name__ == "__main__":
    main()
