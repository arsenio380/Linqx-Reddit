"""Load and save the launch assistant's state to state.json.

The state file remembers where the launch is up to between cron runs.
If the file doesn't exist yet, load() creates it with sensible defaults.

Typical use:

    import state
    data = state.load()
    data["current_day"] += 1
    state.save(data)
"""

import datetime
import json
import logging
import os

log = logging.getLogger(__name__)

# The file lives next to this script so cron can find it regardless of
# the working directory it happens to run from.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def _defaults():
    """Return a fresh state dict for a brand-new launch."""
    today = datetime.date.today().isoformat()  # e.g. "2026-08-21"
    return {
        # The day the launch started (ISO date string).
        "start_date": today,
        # Which day of the launch plan we're on (1, 2, 3, ...).
        "current_day": 1,
        # List of karma snapshots over time, e.g.
        #   [{"date": "2026-08-21", "total_karma": 170}, ...]
        "karma_history": [],
        # Day numbers that have been fully completed.
        "completed_days": [],
        # Reddit post IDs we've already processed, so we don't repeat.
        "seen_post_ids": [],
        # ISO timestamp of the last successful health check, or None.
        "last_health_check": None,
    }


def load():
    """Return the current state, creating defaults if the file is missing.

    If state.json is missing or unreadable, a fresh default state is
    created, saved, and returned — the caller always gets a usable dict.
    """
    if not os.path.exists(STATE_FILE):
        log.info("No state file found; creating a fresh one.")
        data = _defaults()
        save(data)
        return data

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        log.error("Couldn't read %s (%s); starting from defaults.", STATE_FILE, error)
        data = _defaults()
        save(data)
        return data

    # Make sure any newly added keys exist even in an older state file.
    for key, value in _defaults().items():
        data.setdefault(key, value)
    return data


def save(data):
    """Write the state dict back to state.json.

    Returns True on success, False if the file couldn't be written.
    """
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as error:
        log.error("Couldn't write %s: %s", STATE_FILE, error)
        return False
    return True
