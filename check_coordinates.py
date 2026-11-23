"""Check that the target coordinates in ACP plan files roughly match a catalog.

Usage: python check_coordinates.py PLAN.txt [PLAN.txt ...]

A target line is "name<TAB>ra<TAB>dec". To accept a target the catalogs do
not know (or a deliberate offset), put a comment containing "coords-ok"
somewhere between the previous target and this one.

Every target must also be within MATCH_RADIUS of a row of feder_object_list.csv,
the list used to name the images after the night. That check needs no network.
"""
import os
import re
import sys

import astropy.units as u
from astropy.coordinates import SkyCoord

TOLERANCE = 2 * u.arcmin
OBJECT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feder_object_list.csv")
MATCH_RADIUS = 20 * u.arcmin   # msumastro names an image after the list row this close to it


def targets(text, with_excused=False):
    """Yield (line number, name, ra, dec) for each target line to be checked.

    with_excused=True also yields the coords-ok targets, and adds the flag as a fifth item.
    """
    excused = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(";"):
            excused = excused or "coords-ok" in line
        elif "\t" in line and not line.startswith("#"):
            name, ra, dec = (field.strip() for field in (line.split("\t") + ["", ""])[:3])
            if with_excused and not name.upper().startswith("MP "):
                yield number, name, ra, dec, excused
            elif not excused and not name.upper().startswith("MP "):
                yield number, name, ra, dec
            excused = False


def catalog_position(name):
    """Look the name up: TIC at MAST, anything else (Gaia DR3 included) via Sesame."""
    if match := re.fullmatch(r"TIC[ _-]?(\d+)(\.\d+)?", name, re.IGNORECASE):
        from astroquery.mast import Catalogs
        rows = Catalogs.query_criteria(catalog="Tic", ID=match[1])
        if not rows:
            raise LookupError("not in the TIC")
        row = rows[0]
        return SkyCoord(row["ra"], row["dec"], unit="deg")
    name = re.sub(r"Gaia[ _-]?DR3[ _-]?", "Gaia DR3 ", name, flags=re.IGNORECASE)
    try:
        return SkyCoord.from_name(name)
    except Exception:
        return SkyCoord.from_name(re.sub("[_-]", " ", name))


def read_object_list(path=OBJECT_LIST):
    """Return the list as (names, positions); raise ValueError if msumastro could not use it."""
    from astropy.table import Table
    rows = Table.read(path, format="ascii.csv")
    names = [str(name) for name in rows["object"]]
    try:
        positions = SkyCoord(rows["ra"], rows["dec"], unit=(u.hourangle, u.deg))
    except Exception as exc:
        raise ValueError(f"cannot read the coordinates: {exc}") from None
    first, second, separation, _ = positions.search_around_sky(positions, MATCH_RADIUS)
    close = [f"{names[i]} and {names[j]} ({sep.to(u.arcmin):.1f})"
             for i, j, sep in zip(first, second, separation) if i < j]
    if close:
        raise ValueError(f"rows less than {MATCH_RADIUS:.0f} apart: " + "; ".join(close))
    return names, positions


def list_row(name, position):
    """The line to add to the object list for this target."""
    if match := re.fullmatch(r"TIC[ _-]?(\d+)(\.\d+)?", name, re.IGNORECASE):
        name = f"TIC-{match[1]}"
    ra = position.ra.to_string(unit=u.hourangle, sep=":", precision=2, pad=True)
    dec = position.dec.to_string(sep=":", precision=1, pad=True, alwayssign=True)
    return f"{name.replace('_', ' ')},{ra},{dec}"


def problems(text, lookup=catalog_position, object_list=None):
    """Yield (line number, message) for each target that does not check out.

    object_list is the (names, positions) from read_object_list, or None to skip that check.
    """
    for number, name, ra, dec, excused in targets(text, with_excused=True):
        try:
            plan = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        except Exception:
            if not excused:
                yield number, (f"{name}: cannot read the coordinates {ra} {dec} "
                               "(separate name, RA and Dec with TABs)")
            continue
        best = plan   # the position for a new list row: the catalog's, when there is one
        if not excused:
            try:
                best = catalog = lookup(name)
            except Exception as exc:
                yield number, f"{name}: no catalog position ({exc})"
            else:
                separation = plan.separation(catalog)
                if separation > TOLERANCE:
                    yield number, (f"{name}: the plan has {ra} {dec}, but the catalog has "
                                   f"{catalog.to_string('hmsdms', sep=':', precision=1)}, "
                                   f"{separation.to(u.arcmin):.1f} away")
                    continue   # fix the coordinates first
        if object_list is not None and plan.separation(object_list[1]).min() >= MATCH_RADIUS:
            source = "catalog" if best is not plan else "plan"
            yield number, (f"{name}: not in the object list. Add this line ({source} coordinates) "
                           f"to feder_object_list.csv: {list_row(name, best)}")


if __name__ == "__main__":
    messages = []
    try:
        object_list = read_object_list()
    except Exception as exc:   # keep going: the catalog check does not need the list
        object_list = None
        messages.append(f"{os.path.basename(OBJECT_LIST)}: {exc}")
        print(messages[-1], flush=True)
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8-sig") as plan_file:
            for number, message in problems(plan_file.read(), object_list=object_list):
                messages.append(f"{path}:{number}: {message}")
                print(messages[-1], flush=True)
    if messages and "GITHUB_STEP_SUMMARY" in os.environ:    # shown on the run's Summary tab
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write("### Coordinate and object list problems\n\n" + "".join(f"- `{m}`\n" for m in messages))
    sys.exit(bool(messages))
