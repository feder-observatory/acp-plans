import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_folder_names import check_name  # noqa: E402


@pytest.mark.parametrize("name", ["2026-10-19", "2024-02-29"])
def test_valid_names(name):
    assert check_name(name)


@pytest.mark.parametrize("name", ["2026-10-19 copy", "2023-05-20-day-calib",
                                  "2026-1-19", "20261019", "2026-02-30", "2026-13-45"])
def test_invalid_names(name):
    assert not check_name(name)


def test_script_exits_nonzero_for_bad_name():
    script = Path(__file__).resolve().parent.parent / "check_folder_names.py"
    result = subprocess.run([sys.executable, str(script), "--", "2026-10-19 copy"])
    assert result.returncode != 0
