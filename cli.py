"""Shared command-line helpers for the launch-assistant scripts.

Every cron script should support a `--dry-run` flag so you can see what
it *would* do without actually posting to Slack, changing state, etc.

Reuse this in a new script like so:

    import cli

    def main():
        args = cli.parse_args("Post a daily launch summary to Slack.")

        # Wrap anything with side effects in cli.act():
        cli.act(args, "post the summary to Slack",
                lambda: slack.send("Daily summary ..."))

    if __name__ == "__main__":
        main()

Run it normally, or preview with:  python your_script.py --dry-run
"""

import argparse
import logging


def parse_args(description, add_arguments=None):
    """Build the standard parser (with --dry-run) and return parsed args.

    Arguments:
        description:   Short help text describing what the script does.
        add_arguments: Optional function that takes the argparse parser
                       and adds any extra flags your script needs.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making any changes.",
    )
    if add_arguments is not None:
        add_arguments(parser)

    args = parser.parse_args()

    # Set up simple, timestamped logging for the whole script.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return args


def act(args, description, action):
    """Run an action, or just print it when --dry-run is set.

    Arguments:
        args:        The parsed args (must have a .dry_run attribute).
        description: Human-readable label, e.g. "post to Slack".
        action:      A zero-argument function to run for real.

    Returns whatever `action()` returns, or None in dry-run mode.
    """
    if args.dry_run:
        print(f"[dry-run] Would {description}.")
        return None
    return action()
