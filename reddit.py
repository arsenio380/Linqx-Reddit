"""Read-only access to Reddit's public JSON endpoints.

IMPORTANT: This module is deliberately read-only and unauthenticated.
It never logs in, never uses credentials, and never posts anything. It
only reads the same public JSON that anyone can see in a browser by
adding ".json" to a Reddit URL.

To stay polite (and avoid rate limits) it:
  * sends a descriptive User-Agent, and
  * waits at least 2 seconds between requests.
"""

import logging
import time

import requests

log = logging.getLogger(__name__)

# Reddit asks bots to identify themselves with a descriptive User-Agent.
USER_AGENT = "linqx-reddit-launch-assistant/1.0 (read-only public-data monitor)"

# Minimum seconds to wait between two requests, so we never hammer Reddit.
REQUEST_DELAY = 2.0

# Seconds to wait for a response before giving up.
TIMEOUT = 15

# Remembers when we last made a request, to enforce REQUEST_DELAY.
_last_request_time = 0.0


def _throttle():
    """Sleep just long enough to keep 2 seconds between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.monotonic()


def _get(url):
    """Make one throttled GET request and return the Response.

    Raises requests.RequestException on network/HTTP errors so callers
    can decide how to handle them.
    """
    _throttle()
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response


def _simplify_post(child):
    """Turn Reddit's nested post JSON into a small, readable dict."""
    data = child.get("data", {})
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "author": data.get("author"),
        "subreddit": data.get("subreddit"),
        "score": data.get("score"),
        "num_comments": data.get("num_comments"),
        "created_utc": data.get("created_utc"),
        "permalink": "https://www.reddit.com" + data.get("permalink", ""),
        "url": data.get("url"),
    }


def _listing(subreddit, sort, limit):
    """Fetch a subreddit listing ("new" or "hot") as a list of posts.

    Returns an empty list if anything goes wrong.
    """
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}"
    try:
        response = _get(url)
        children = response.json().get("data", {}).get("children", [])
    except (requests.RequestException, ValueError) as error:
        log.error("Failed to fetch r/%s/%s: %s", subreddit, sort, error)
        return []
    return [_simplify_post(child) for child in children]


def new_posts(subreddit, limit=10):
    """Return the newest posts in a subreddit (list of simple dicts)."""
    return _listing(subreddit, "new", limit)


def hot_posts(subreddit, limit=10):
    """Return the hot posts in a subreddit (list of simple dicts)."""
    return _listing(subreddit, "hot", limit)


def rising_posts(subreddit, limit=25):
    """Return the rising posts in a subreddit (list of simple dicts).

    "Rising" surfaces threads that are gaining traction right now, which
    is where a comment has the best chance of being seen — ask for a
    generous limit and let the caller filter by age/score.
    """
    return _listing(subreddit, "rising", limit)


def user_karma(username):
    """Return a user's karma as a dict, or None if it can't be read.

    Example return value:
        {"link_karma": 42, "comment_karma": 128, "total_karma": 170}
    """
    url = f"https://www.reddit.com/user/{username}/about.json"
    try:
        response = _get(url)
        data = response.json().get("data", {})
    except (requests.RequestException, ValueError) as error:
        log.error("Failed to fetch karma for u/%s: %s", username, error)
        return None

    link_karma = data.get("link_karma", 0)
    comment_karma = data.get("comment_karma", 0)
    return {
        "link_karma": link_karma,
        "comment_karma": comment_karma,
        # Reddit provides total_karma; fall back to the sum if it's absent.
        "total_karma": data.get("total_karma", link_karma + comment_karma),
    }


def is_shadowbanned(username):
    """Check whether a user appears to be shadowbanned.

    Fetches the public profile with no session. A shadowbanned (or
    non-existent) account returns HTTP 404, so:

        True  -> profile is 404 (shadowbanned / not found)
        False -> profile loads normally
        None  -> we couldn't tell (network error, rate limit, etc.)
    """
    url = f"https://www.reddit.com/user/{username}/about.json"
    try:
        _throttle()
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        log.error("Failed to check shadowban for u/%s: %s", username, error)
        return None

    if response.status_code == 404:
        return True
    if response.status_code == 200:
        return False

    log.warning(
        "Unexpected status %s checking shadowban for u/%s.",
        response.status_code,
        username,
    )
    return None
