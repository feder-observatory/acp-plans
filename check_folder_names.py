import argparse
import datetime
from pathlib import Path
import re
import sys


def check_name(name):
    """Check whether the plan folder name is valid: a real calendar date, YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
        return False
    try:
        datetime.date.fromisoformat(name)
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
              "YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)
