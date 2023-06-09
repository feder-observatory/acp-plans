import argparse
from pathlib import Path
import re

SKIPS = ['.', '.github/workflows']

def check_name(name):
    """Check whether the plan name is valid."""
    if any(skip == name for skip in SKIPS):
        return True
    return re.match(r"\d{4}-\d{2}-\d{2}", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the validity of a folder name.")
    parser.add_argument("folder", type=Path, help="The folder containing the plan set.")
    args = parser.parse_args()

    if not check_name(str(args.folder)):
        raise RuntimeError(f"Folder name {args.folder} is not valid.")