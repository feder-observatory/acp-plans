import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import plan_check  # noqa: E402

REAL_FOLDER = REPO_ROOT / "2026-11-15"


@pytest.fixture
def plan_set(tmp_path):
    folder = tmp_path / "2026-11-15"
    shutil.copytree(REAL_FOLDER, folder)
    return folder


def test_valid_folder_has_no_problems(plan_set):
    assert plan_check.find_problems(plan_set) == []


def test_unused_plan(plan_set):
    (plan_set / "extra.txt").write_text("#autofocus\n#shutdown\n")
    problems = plan_check.find_problems(plan_set)
    assert len(problems) == 1
    assert "extra.txt is not used" in problems[0]


def test_chain_to_missing_plan(plan_set):
    (plan_set / "ey_uma.txt").unlink()
    problems = plan_check.find_problems(plan_set)
    assert any("NGC7790.txt chains to a non-existent plan" in p for p in problems)


def test_missing_flat_config(plan_set):
    (plan_set / "DFlat.txt").unlink()
    problems = plan_check.find_problems(plan_set)
    assert any("ScreenFlats.txt calls a non-existent flat configuration file" in p
               for p in problems)


def test_no_plan_starting_with_1_is_a_problem(plan_set):
    (plan_set / "1-ngc_7331.txt").rename(plan_set / "ngc_7331.txt")
    problems = plan_check.find_problems(plan_set)
    assert len(problems) == 1
    assert "No plan in the plan set starts with 1" in problems[0]


def test_upper_case_extension_is_a_plan(plan_set):
    (plan_set / "extra.TXT").write_text("#autofocus\n#shutdown\n")
    problems = plan_check.find_problems(plan_set)
    assert len(problems) == 1
    assert "extra.TXT is not used" in problems[0]


def test_chain_to_upper_case_extension(plan_set):
    (plan_set / "ey_uma.txt").rename(plan_set / "ey_uma.TXT")
    nxt = plan_set / "NGC7790.txt"
    nxt.write_text(nxt.read_text().replace("#chain ey_uma.txt", "#chain ey_uma.TXT"))
    assert plan_check.find_problems(plan_set) == []


def test_utf8_bom_is_ignored(plan_set):
    (plan_set / "fang_star.txt").write_text("﻿#chain calibration.txt\n", encoding="utf-8")
    assert plan_check.find_problems(plan_set) == []


def test_symlinks_and_directories_are_not_read_as_plans(plan_set):
    # check_objects.py reports these; plan_check must not follow or open them
    (plan_set / "link.txt").symlink_to("/etc/hosts")
    (plan_set / "dir.txt").mkdir()
    assert plan_check.find_problems(plan_set) == []


def test_chain_to_a_symlink_does_not_crash(plan_set):
    (plan_set / "ey_uma.txt").rename(plan_set / "real.txt")
    (plan_set / "ey_uma.txt").symlink_to("real.txt")
    problems = plan_check.find_problems(plan_set)
    assert problems == ["Plan " + str(plan_set / "real.txt") + " is not used."]


def replace_in(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


@pytest.mark.parametrize("directive", ["#CHAIN", "#Chain", "#chain\t", "#chain  "])
def test_chain_directive_any_case_and_whitespace(plan_set, directive):
    replace_in(plan_set / "fang_star.txt", "#chain ", directive + ("" if directive[-1].isspace() else " "))
    assert plan_check.find_problems(plan_set) == []


def test_flats_directive_any_case(plan_set):
    replace_in(plan_set / "ScreenFlats.txt", "#screenflats", "#SCREENFLATS")
    assert plan_check.find_problems(plan_set) == []


def test_shutdown_any_case():
    plan = plan_check.PlanCheck.__new__(plan_check.PlanCheck)
    plan.plan_contents = ["#autofocus\n", "#SHUTDOWN\n"]
    assert plan.has_shutdown()


def test_empty_plan_is_a_readable_problem(plan_set):
    (plan_set / "fang_star.txt").write_text("; only a comment\n\n")
    problems = plan_check.find_problems(plan_set)
    assert ("Plan " + str(plan_set / "fang_star.txt") + " is empty: it has no lines other "
            "than comments and blank lines.") in problems


def test_chain_without_file_name_is_a_readable_problem(plan_set):
    replace_in(plan_set / "fang_star.txt", "#chain calibration.txt", "#chain")
    problems = plan_check.find_problems(plan_set)
    assert ("Plan " + str(plan_set / "fang_star.txt") + " has a #chain line with no plan "
            "file name.") in problems
    assert not any("fang_star.txt chains to a non-existent plan" in p for p in problems)


def test_chained_name_with_a_space_is_unchanged(plan_set):
    # behaviour kept: only the first word after #chain is the file name
    (plan_set / "my plan.txt").write_text("#shutdown\n")
    replace_in(plan_set / "fang_star.txt", "#chain calibration.txt", "#chain my plan.txt")
    problems = plan_check.find_problems(plan_set)
    assert any("fang_star.txt chains to a non-existent plan" in p for p in problems)


def run_cli(folder):
    return subprocess.run([sys.executable, str(REPO_ROOT / "plan_check.py"), str(folder)],
                          capture_output=True, text=True)


def test_cli_valid(plan_set):
    result = run_cli(plan_set)
    assert result.returncode == 0
    assert "Plan set is valid." in result.stdout


def test_cli_invalid_fails_and_lists_problems(plan_set):
    (plan_set / "extra.txt").write_text("#shutdown\n")
    result = run_cli(plan_set)
    assert result.returncode != 0
    assert "Plan set is not valid" in result.stderr
    assert "extra.txt is not used" in result.stderr


def test_cli_no_first_plan_fails(plan_set):
    (plan_set / "1-ngc_7331.txt").rename(plan_set / "ngc_7331.txt")
    result = run_cli(plan_set)
    assert result.returncode != 0
    assert "No plan in the plan set starts with 1" in result.stderr
