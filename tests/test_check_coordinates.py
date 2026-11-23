import sys
from pathlib import Path

from astropy.coordinates import SkyCoord

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_coordinates import list_row, problems, read_object_list, targets  # noqa: E402

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


def test_targets_skips_directives_minor_planets_and_excused_lines():
    assert list(targets(PLAN)) == [
        (3, "ey-uma", "09:02:20.8", "+49:49:09.3"),
        (9, "M31", "00:42:44.3", "+41:16:09"),
    ]


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


def test_failed_lookup_is_reported():
    def lookup(name):
        raise ValueError("unknown object")

    [(number, message)] = problems("xyzzy\t09:02:20.8\t+49:49:09.3\n", lookup)
    assert "unknown object" in message


def test_one_tab_line_is_reported_not_raised():
    [(number, message)] = problems("M31\t00:42:44.3 +41:16:09\n", lambda name: EY_UMA)
    assert number == 1
    assert "cannot read the coordinates" in message and "TABs" in message


LISTED = (["ey uma"], SkyCoord(["09:02:20.8"], ["+49:49:09.3"], unit=("hourangle", "deg")))


def test_target_near_a_list_row_has_no_problems():
    # 15 arcmin from the row: inside msumastro's 20 arcmin
    nearby = SkyCoord("09:02:20.8 +50:04:09.3", unit=("hourangle", "deg"))
    assert list(problems("ey-uma\t09:02:20.8\t+50:04:09.3\n", lambda name: nearby, LISTED)) == []


def test_target_missing_from_the_list_gets_a_row_with_catalog_coordinates():
    far = SkyCoord("12:00:00.04 +10:00:00.4", unit=("hourangle", "deg"))
    [(number, message)] = problems("TIC_123.01\t12:00:00\t+10:00:00\n", lambda name: far, LISTED)
    assert number == 1
    assert message.endswith("feder_object_list.csv: TIC-123,12:00:00.04,+10:00:00.4")
    assert "catalog coordinates" in message


def test_coords_ok_target_missing_from_the_list_gets_a_row_with_plan_coordinates():
    def lookup(name):
        raise AssertionError("a coords-ok target is not looked up")

    plan = "; coords-ok: from the discovery paper\nnew_thing\t12:00:00\t-00:30:00\n"
    [(number, message)] = problems(plan, lookup, LISTED)
    assert number == 2
    assert message.endswith("feder_object_list.csv: new thing,12:00:00.00,-00:30:00.0")
    assert "plan coordinates" in message


def test_wrong_coordinates_are_reported_without_a_list_row():
    [(number, message)] = problems("ey-uma\t12:00:00\t+10:00:00\n", lambda name: EY_UMA, LISTED)
    assert "away" in message and "object list" not in message


def test_the_object_list_in_the_repository_is_usable():
    names, positions = read_object_list()
    assert len(names) == len(positions) > 200


def test_list_rows_too_close_together_are_rejected(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("object,ra,dec\nm31,00:42:44.3,+41:16:09\nm31-pane,00:42:44.3,+41:31:09\n")
    try:
        read_object_list(path)
    except ValueError as exc:
        assert "m31 and m31-pane (15.0 arcmin)" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_list_row_keeps_hyphens_and_turns_underscores_into_spaces():
    assert list_row("V_1405-Cas", EY_UMA) == "V 1405-Cas,09:02:20.80,+49:49:09.3"
