import sys
from pathlib import Path

from astropy.coordinates import SkyCoord

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_coordinates import problems, targets  # noqa: E402

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
