import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_folder_names import check_name  # noqa: E402

SCRIPT = REPO_ROOT / "check_folder_names.py"

# Top-level directories that are not plan folders.
NOT_PLAN_FOLDERS = {"tests", "__pycache__"}


@pytest.mark.parametrize("name", [
    "2026-10-19",
    "2023-05-20-day-calib",
    "2024-02-29",          # leap day
    "2026-11-15-extra-suffix",
])
def test_valid_names(name):
    assert check_name(name)


@pytest.mark.parametrize("name", [
    "2026-10-19 copy",     # trailing text without a dash
    "x2026-10-19",         # prefix
    "2026-13-45",          # not a real date
    "2026-02-30",          # not a real date
    "2025-02-29",          # not a leap year
    "2026-1-19",           # not zero-padded
    "2026-10-19-",         # empty suffix
    "2026-10-19\n",        # trailing newline
    "2026-10-1",
    ".",
    "",
    "tests",
    "README",
])
def test_invalid_names(name):
    assert not check_name(name)


def test_every_existing_top_level_folder_passes():
    folders = [p.name for p in REPO_ROOT.iterdir()
               if p.is_dir()
               and not p.name.startswith(".")
               and p.name not in NOT_PLAN_FOLDERS]
    assert folders, "expected some plan folders in the repo"
    bad = [name for name in folders if not check_name(name)]
    assert bad == []


def run_script(arg):
    return subprocess.run([sys.executable, str(SCRIPT), arg],
                          capture_output=True, text=True)


def test_cli_accepts_valid_name():
    assert run_script("2023-05-20-day-calib").returncode == 0


@pytest.mark.parametrize("name", ["2026-10-19 copy", "x2026-10-19",
                                  "2026-13-45", "."])
def test_cli_rejects_invalid_name(name):
    result = run_script(name)
    assert result.returncode != 0
    assert "not valid" in result.stderr
