"""Send messages to Slack via an Incoming Webhook.

Usage from another script:

    import slack
    slack.send("Hello from the launch assistant!")

The webhook URL is read from the SLACK_WEBHOOK_URL environment variable
(which is loaded automatically from your .env file).
"""

import logging
import os

import requests
from dotenv import load_dotenv

# Read .env into the environment (does nothing if the file is missing).
load_dotenv()

log = logging.getLogger(__name__)

# How long to wait for Slack to respond before giving up, in seconds.
TIMEOUT = 10


def send(text, blocks=None):
    """Post a message to Slack.

    Arguments:
        text:   The plain-text message. Slack also uses this as the
                notification/fallback text when `blocks` is given.
        blocks: Optional list of Slack "block kit" dicts for richer
                formatting. Leave as None for a simple text message.

    Returns:
        True if Slack accepted the message, False otherwise.

    This function never raises: if anything goes wrong it logs the
    problem and returns False, so a failed Slack post can't crash the
    script that called it.
    """
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        log.error("SLACK_WEBHOOK_URL is not set — cannot send Slack message.")
        return False

    payload = {"text": text}
    if blocks is not None:
        payload["blocks"] = blocks

    try:
        response = requests.post(webhook_url, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        log.error("Failed to send Slack message: %s", error)
        return False

    return True
