import argparse
from pathlib import Path
import re


def check_name(name):
    """Check whether the plan name is valid."""

    return re.match(r"\d{4}-\d{2}-\d{2}", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the validity of a folder name.")
    parser.add_argument("folder",
                        type=Path,
                        help="The folder containing the plan set.")
    args = parser.parse_args()
    folder = args.folder
    if not check_name(str(folder)):
        raise RuntimeError(f"Folder name {folder} is not valid. It should be YYYY-MM-DD.")