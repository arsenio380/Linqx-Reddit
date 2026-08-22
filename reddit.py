"""Read-only access to Reddit's public JSON endpoints.

IMPORTANT: This module is deliberately read-only and unauthenticated.
It never logs in, never uses credentials, and never posts anything. It
only reads the same public JSON that anyone can see in a browser by
adding ".json" to a Reddit URL.

To stay polite (and avoid rate limits) it:
  * sends a descriptive User-Agent,
  * waits at least 2 seconds between requests,
  * retries with exponential backoff when Reddit answers 403/429, and
  * falls back to old.reddit.com when www.reddit.com blocks us with a 403.
"""

import logging
import time
from urllib.parse import urlsplit, urlunsplit

import requests

log = logging.getLogger(__name__)

# Reddit asks bots to identify themselves with a descriptive User-Agent.
# A generic/empty UA is the most common reason for a 403 "Blocked" reply.
USER_AGENT = "linqx-reddit-assistant/1.0 (by u/arsenbuildz)"

# Minimum seconds to wait between two requests, so we never hammer Reddit.
REQUEST_DELAY = 2.0

# Seconds to wait for a response before giving up.
TIMEOUT = 15

# HTTP statuses worth retrying: 403 (Blocked) and 429 (Too Many Requests).
RETRY_STATUSES = (403, 429)

# How many attempts per host before giving up on it.
MAX_RETRIES = 3

# Base seconds for exponential backoff between retries (2s, 4s, 8s, ...).
BACKOFF_BASE = 2.0

# Hosts to try in order. old.reddit.com serves the same public JSON and is
# often reachable when www.reddit.com returns a 403.
HOSTS = ("www.reddit.com", "old.reddit.com")

# Remembers when we last made a request, to enforce REQUEST_DELAY.
_last_request_time = 0.0


def _throttle():
    """Sleep just long enough to keep 2 seconds between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    _last_request_time = time.monotonic()


def _with_host(url, host):
    """Return url with its network location swapped for host."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=host))


def _retry_wait(response, attempt):
    """Seconds to wait before the next retry.

    Honours Reddit's Retry-After header when present (common on 429),
    otherwise uses exponential backoff: 2s, 4s, 8s, ...
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            # Never wait less than BACKOFF_BASE — some block responses send
            # "Retry-After: 0", which would defeat the point of backing off.
            return max(BACKOFF_BASE, float(retry_after))
        except ValueError:
            pass
    return BACKOFF_BASE * (2 ** attempt)


def _request(url):
    """Throttled GET with retry/backoff on 403 & 429 and an old.reddit.com fallback.

    Returns a Response, which may still carry a non-2xx status if every
    attempt failed — callers decide what to do with it. Only raises
    requests.RequestException when no HTTP response could be obtained at
    all (e.g. a network error). Stays read-only and unauthenticated.
    """
    response = None
    last_error = None

    for host in HOSTS:
        host_url = _with_host(url, host)
        for attempt in range(MAX_RETRIES):
            _throttle()
            try:
                response = requests.get(
                    host_url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT,
                )
            except requests.RequestException as error:
                last_error = error
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE * (2 ** attempt))
                    continue
                break  # Give up on this host; try the next one.

            if response.status_code not in RETRY_STATUSES:
                return response  # Success or a status the caller should see.

            if attempt < MAX_RETRIES - 1:
                wait = _retry_wait(response, attempt)
                log.warning(
                    "Reddit returned %s for %s; retrying in %.0fs (attempt %d/%d).",
                    response.status_code,
                    host,
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(wait)

        # Retries for this host are exhausted. Only a 403 is worth trying
        # the next host for; anything else we return as-is.
        if response is not None and response.status_code == 403 and host != HOSTS[-1]:
            log.warning("%s blocked us (403); falling back to next host.", host)
            continue
        if response is not None:
            return response

    if response is not None:
        return response
    raise last_error  # No response from any host.


def _get(url):
    """Make one throttled, retrying GET and return the Response.

    Raises requests.RequestException on network errors or a non-2xx HTTP
    status (after retries/fallback) so callers can decide how to handle them.
    """
    response = _request(url)
    response.raise_for_status()
    return response


def _simplify_post(child):
    """Turn Reddit's nested post JSON into a small, readable dict."""
    data = child.get("data", {})
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "selftext": data.get("selftext", ""),
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
        response = _request(url)
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
