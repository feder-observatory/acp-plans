import argparse
import datetime
from pathlib import Path
import re
import sys

# YYYY-MM-DD, optionally followed by "-suffix" (e.g. 2023-05-20-day-calib).
NAME_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2})(-[^\s/]+)?")


def check_name(name):
    """Check whether the plan folder name is valid.

    A valid name is a real calendar date ``YYYY-MM-DD``, optionally
    followed by ``-suffix``.
    """
    match = NAME_PATTERN.fullmatch(name)
    if not match:
        return False
    try:
        datetime.date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the validity of a folder name.")
    parser.add_argument("folder",
                        type=Path,
                        help="The folder containing the plan set.")
    args = parser.parse_args()
    folder = args.folder
    if not check_name(folder.name):
        print(f"Folder name {str(folder)!r} is not valid. It should be "
              "YYYY-MM-DD, optionally followed by -suffix.", file=sys.stderr)
        sys.exit(1)
