import subprocess
import sys
from pathlib import Path

import pytest
from astropy.coordinates import SkyCoord

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_coordinates import list_row, main, notes, problems, read_object_list, targets  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "check_coordinates.py"

PLAN = (
    "; a comment\n"
    "#FILTER B,V\n"
    "ey-uma\t09:02:20.8\t+49:49:09.3\n"
    "\n"
    "MP 12345\t01:00:00\t+02:00:00\n"
    "; coords-ok: mosaic pane\n"
    "#COUNT 1\n"
    "M31\t00:44:10.0\t+41:36:00.0\n"
    "M31\t00:42:44.3\t+41:16:09\n"
    "#chain next.txt\n"
)

EY_UMA = SkyCoord("09:02:20.8 +49:49:09.3", unit=("hourangle", "deg"))


def position(text):
    return SkyCoord(text, unit=("hourangle", "deg"))


def no_lookup(name):
    raise AssertionError("a coords-ok target is not looked up")


def test_targets_skips_directives_and_minor_planets_and_flags_excused_lines():
    assert list(targets(PLAN)) == [
        (3, "ey-uma", "09:02:20.8", "+49:49:09.3", False),
        (8, "M31", "00:44:10.0", "+41:36:00.0", True),
        (9, "M31", "00:42:44.3", "+41:16:09", False),
    ]


def test_minor_planet_lines_are_not_checked():
    assert list(problems("MP 12345\t01:00:00\tfoo\n", no_lookup, LISTED)) == []


def test_flat_configuration_lines_are_not_targets():
    assert list(targets("10,B,1\n10,V,1\n")) == []


def test_matching_coordinates_have_no_problems():
    assert list(problems("ey-uma\t09:02:21\t+49:49:30\n", lambda name: EY_UMA)) == []


def test_coordinates_far_from_the_catalog_are_reported():
    [(number, message)] = problems("ey-uma\t09:12:20.8\t+49:49:09.3\n", lambda name: EY_UMA)
    assert number == 1
    assert "ey-uma" in message and "away" in message


def test_unreadable_coordinates_are_reported():
    [(number, message)] = problems("ey-uma\t09:02:20.8\tfoo\n", lambda name: EY_UMA)
    assert "cannot read" in message


def test_unreadable_coordinates_are_reported_even_with_coords_ok():
    [(number, message)] = problems("; coords-ok\ney-uma\t09:02:20.8\tfoo\n", no_lookup, LISTED)
    assert number == 2
    assert "cannot read the coordinates" in message


def test_failed_lookup_is_reported():
    def lookup(name):
        raise ValueError("unknown object")

    [(number, message)] = problems("xyzzy\t09:02:20.8\t+49:49:09.3\n", lookup)
    assert "unknown object" in message


def test_failed_lookup_of_a_target_missing_from_the_list_suggests_plan_coordinates():
    def lookup(name):
        raise ValueError("unknown object")

    [(_, lookup_message), (_, list_message)] = problems("xyzzy\t12:00:00\t+10:00:00\n", lookup, LISTED)
    assert "unknown object" in lookup_message
    assert "plan coordinates" in list_message
    assert list_message.endswith("feder_object_list.csv: xyzzy,12:00:00.00,+10:00:00.0")


def test_one_tab_line_is_reported_not_raised():
    [(number, message)] = problems("M31\t00:42:44.3 +41:16:09\n", lambda name: EY_UMA)
    assert number == 1
    assert "cannot read the coordinates" in message and "TABs" in message


LISTED = (["ey uma"], SkyCoord(["09:02:20.8"], ["+49:49:09.3"], unit=("hourangle", "deg")))


def test_target_near_a_list_row_has_no_problems():
    # 15 arcmin from the row: inside msumastro's 20 arcmin
    nearby = position("09:02:20.8 +50:04:09.3")
    assert list(problems("ey-uma\t09:02:20.8\t+50:04:09.3\n", lambda name: nearby, LISTED)) == []


def test_target_missing_from_the_list_gets_a_row_with_catalog_coordinates():
    far = position("12:00:00.04 +10:00:00.4")
    [(number, message)] = problems("TIC_123.01\t12:00:00\t+10:00:00\n", lambda name: far, LISTED)
    assert number == 1
    assert message.endswith("feder_object_list.csv: TIC-123,12:00:00.04,+10:00:00.4")
    assert "catalog coordinates" in message


def test_coords_ok_target_missing_from_the_list_gets_a_row_with_plan_coordinates():
    plan = "; coords-ok: from the discovery paper\nnew_thing\t12:00:00\t-00:30:00\n"
    [(number, message)] = problems(plan, no_lookup, LISTED)
    assert number == 2
    assert message.endswith("feder_object_list.csv: new thing,12:00:00.00,-00:30:00.0")
    assert "plan coordinates" in message


def test_wrong_coordinates_are_reported_without_a_list_row():
    [(number, message)] = problems("ey-uma\t12:00:00\t+10:00:00\n", lambda name: EY_UMA, LISTED)
    assert "away" in message and "object list" not in message


def test_no_row_is_suggested_that_would_be_too_close_to_a_list_row():
    # the plan is 20.5 arcmin from the row, the catalog 19 arcmin: a row there would clash
    listed = (["m31"], SkyCoord(["00:42:44.3"], ["+41:16:09"], unit=("hourangle", "deg")))
    catalog = position("00:42:44.3 +41:35:09")
    [(number, message)] = problems("blob\t00:42:44.3\t+41:36:39\n", lambda name: catalog, listed)
    assert "not within 20 arcmin" in message
    assert 'would be 19.0 arcmin from the list row "m31"' in message
    assert "Add this line" not in message


def test_nearby_missing_targets_get_one_suggested_row():
    plan = "; coords-ok\nfirst\t12:00:00\t+10:00:00\n; coords-ok\nsecond\t12:00:00\t+10:10:00\n"
    [(_, first), (number, second)] = problems(plan, no_lookup, LISTED)
    assert first.endswith("feder_object_list.csv: first,12:00:00.00,+10:00:00.0")
    assert number == 4
    assert "suggested above for first covers it" in second
    assert "Add this line" not in second


def test_suggested_rows_are_shared_across_plans():
    suggested = []
    plan = "; coords-ok\nfirst\t12:00:00\t+10:00:00\n"
    [(_, first)] = problems(plan, no_lookup, LISTED, suggested)
    [(_, again)] = problems(plan, no_lookup, LISTED, suggested)
    # 21 arcmin from the first plan position, but only 19 from the first suggestion
    catalog = position("12:00:00 +10:19:00")
    [(_, clash)] = problems("second\t12:00:00\t+10:21:00\n", lambda name: catalog, LISTED, suggested)
    assert "Add this line" in first
    assert "suggested above for first covers it" in again
    assert "would be 19.0 arcmin from the line suggested above for first" in clash


def test_target_under_another_name_gets_a_note_not_a_problem():
    plan = "; coords-ok\nTIC_999\t09:02:20.8\t+49:59:09.3\n"
    assert list(problems(plan, no_lookup, LISTED)) == []
    [(number, message)] = notes(plan, LISTED)
    assert number == 2
    assert message == 'TIC_999 is 10.0 arcmin from the list row "ey uma"; its images will be named ey uma'


def test_no_note_when_the_names_match_once_normalized():
    listed = (["ey uma", "TIC-123"],
              SkyCoord(["09:02:20.8", "12:00:00"], ["+49:49:09.3", "+10:00:00"], unit=("hourangle", "deg")))
    plan = "ey-uma\t09:02:20.8\t+49:49:09.3\nEY_UMA\t09:02:20.8\t+49:49:09.3\nTIC_123.01\t12:00:00\t+10:00:00\n"
    assert list(notes(plan, listed)) == []


def test_the_object_list_in_the_repository_is_usable():
    names, positions = read_object_list()
    assert len(names) == len(positions) > 200


def test_list_rows_too_close_together_are_rejected(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("object,ra,dec\nm31,00:42:44.3,+41:16:09\nm31-pane,00:42:44.3,+41:31:09\n")
    with pytest.raises(ValueError, match=r"m31 and m31-pane \(15.0 arcmin\)"):
        read_object_list(path)


@pytest.mark.parametrize("row, message", [
    ("m31,00:42:44.3,", r"line 3 \(m31\): empty dec"),
    ("m31", r"line 3 \(m31\): empty ra and dec"),
    (",00:42:44.3,+41:16:09", r"line 3: empty object"),
])
def test_list_rows_with_empty_fields_are_rejected(tmp_path, row, message):
    path = tmp_path / "list.csv"
    path.write_text(f"object,ra,dec\nchi and,01:39:21,+44:23:10.2\n{row}\n")
    with pytest.raises(ValueError, match=message):
        read_object_list(path)


def test_a_list_without_rows_is_rejected(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("object,ra,dec\n")
    with pytest.raises(ValueError, match="the list has no rows"):
        read_object_list(path)


def test_comments_header_case_and_byte_order_mark_are_accepted(tmp_path):
    path = tmp_path / "list.csv"
    path.write_bytes("﻿# the list\nObject,RA,Dec\n# M31\nm31,00:42:44.3,+41:16:09\n".encode())
    names, positions = read_object_list(path)
    assert names == ["m31"]
    assert positions[0].separation(position("00:42:44.3 +41:16:09")).arcsec < 0.1


def test_a_missing_column_is_named(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("name,ra,dec\nm31,00:42:44.3,+41:16:09\n")
    with pytest.raises(ValueError, match="no object column"):
        read_object_list(path)


def test_list_row_keeps_hyphens_and_turns_underscores_into_spaces():
    assert list_row("V_1405-Cas", EY_UMA) == "V 1405-Cas,09:02:20.80,+49:49:09.3"


def write_files(tmp_path, plan, object_list="object,ra,dec\ney uma,09:02:20.8,+49:49:09.3\n"):
    (tmp_path / "list.csv").write_text(object_list)
    (tmp_path / "plan.txt").write_text(plan)
    return str(tmp_path / "plan.txt"), str(tmp_path / "list.csv")


def test_main_checks_the_plans_even_when_the_list_is_unreadable(tmp_path, capsys):
    plan, object_list = write_files(tmp_path, "; coords-ok\ney-uma\t09:02:20.8\tfoo\n", "object,ra,dec\n")
    assert main([plan], object_list) == 1
    output = capsys.readouterr().out
    assert "list.csv: the list has no rows" in output
    assert f"{plan}:2: ey-uma: cannot read the coordinates" in output


def test_main_exits_0_on_a_clean_run(tmp_path, capsys):
    plan, object_list = write_files(tmp_path, "; coords-ok\ney-uma\t09:02:20.8\t+49:49:09.3\n")
    assert main([plan], object_list) == 0
    assert capsys.readouterr().out == ""


def test_main_exits_0_with_only_notes_and_puts_them_in_the_summary(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    plan, object_list = write_files(tmp_path, "; coords-ok\nTIC_999\t09:02:20.8\t+49:49:09.3\n")
    assert main([plan], object_list) == 0
    assert f'{plan}:2: note: TIC_999 is 0.0 arcmin from the list row "ey uma"' in capsys.readouterr().out
    text = summary.read_text()
    assert "### Notes" in text and "problems" not in text


def test_the_script_runs_on_the_real_list(tmp_path):
    plan = tmp_path / "plan.txt"
    plan.write_text("; coords-ok\nchi and\t01:39:21\t+44:23:10.2\n")
    result = subprocess.run([sys.executable, str(SCRIPT), str(plan)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
