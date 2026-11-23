"""Check that the target coordinates in ACP plan files roughly match a catalog.

Usage: python check_coordinates.py PLAN.txt [PLAN.txt ...]

A target line is "name<TAB>ra<TAB>dec"; minor planet ("MP ...") lines are skipped.
To accept a target the catalogs do not know (or a deliberate offset), put a
comment containing "coords-ok" somewhere between the previous target and this
one. coords-ok waives only the catalog lookup: the coordinates must still be
readable, and the target must still be in the object list.

Every target must be within MATCH_RADIUS of a row of feder_object_list.csv,
the list used to name the images after the night; that check needs no network.
For a target that is not, the script suggests a line to add, unless that line
would be too close to a row already in the list (or to a line suggested for an
earlier target in the same run), which needs a person to sort out.

A target that matches a list row with a different name gets a note saying what
its images will be called. Notes do not fail the check.
"""
import os
import re
import sys

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord

TOLERANCE = 2 * u.arcmin
OBJECT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feder_object_list.csv")
MATCH_RADIUS = 20 * u.arcmin   # msumastro names an image after the list row this close to it
TIC = re.compile(r"TIC[ _-]?(\d+)(\.\d+)?", re.IGNORECASE)


def targets(text):
    """Yield (line number, name, ra, dec, excused) for each target line; excused means coords-ok."""
    excused = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(";"):
            excused = excused or "coords-ok" in line
        elif "\t" in line and not line.startswith("#"):
            name, ra, dec = (field.strip() for field in (line.split("\t") + ["", ""])[:3])
            if not name.upper().startswith("MP "):
                yield number, name, ra, dec, excused
            excused = False


def catalog_position(name):
    """Look the name up: TIC at MAST, anything else (Gaia DR3 included) via Sesame."""
    if match := TIC.fullmatch(name):
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
    with open(path, encoding="utf-8-sig") as list_file:
        lines = list_file.read().splitlines()
    rows = Table.read(lines, format="ascii.csv", comment="#")
    columns = {column.lower(): column for column in rows.colnames}   # any letter case, as msumastro
    for column in ("object", "ra", "dec"):
        if column not in columns:
            raise ValueError(f"no {column} column (the header is {','.join(rows.colnames)})")
    if not len(rows):
        raise ValueError("the list has no rows")
    # the file line of each row: the lines that are not blank or comments, after the header
    numbers = [number for number, line in enumerate(lines, start=1)
               if line.strip() and not line.lstrip().startswith("#")][1:]
    for number, row in zip(numbers, rows):
        empty = [column for column in ("object", "ra", "dec") if row[columns[column]] is np.ma.masked]
        if empty:
            name = "" if "object" in empty else f" ({row[columns['object']]})"
            raise ValueError(f"line {number}{name}: empty {' and '.join(empty)}")
    names = [str(name) for name in rows[columns["object"]]]
    try:
        positions = SkyCoord(rows[columns["ra"]], rows[columns["dec"]], unit=(u.hourangle, u.deg))
    except Exception as exc:
        raise ValueError(f"cannot read the coordinates: {exc}") from None
    close = [f"{names[j]} and {names[i]} ({separation.to(u.arcmin):.1f})"
             for i in range(len(names))
             for j, separation in enumerate(positions[i].separation(positions[:i]))
             if separation < MATCH_RADIUS]
    if close:
        raise ValueError(f"rows less than {MATCH_RADIUS:.0f} apart: " + "; ".join(close))
    return names, positions


def list_row(name, position):
    """The line to add to the object list for this target."""
    if match := TIC.fullmatch(name):
        name = f"TIC-{match[1]}"
    ra = position.ra.to_string(unit=u.hourangle, sep=":", precision=2, pad=True)
    dec = position.dec.to_string(sep=":", precision=1, pad=True, alwayssign=True)
    return f"{name.replace('_', ' ')},{ra},{dec}"


def plain_name(name):
    """The name as compared with a list row: lower case, no spaces, _ or -, no TIC suffix."""
    if match := TIC.fullmatch(name):
        name = f"TIC-{match[1]}"
    return re.sub(r"[\s_-]", "", name.lower())


def problems(text, lookup=catalog_position, object_list=None, suggested=None):
    """Yield (line number, message) for each target that does not check out.

    object_list is the (names, positions) from read_object_list, or None to skip that check.
    suggested collects the (name, position) of each list line suggested so far: pass the
    same list for every plan of a run, so that no two suggestions clash.
    """
    suggested = [] if suggested is None else suggested
    for number, name, ra, dec, excused in targets(text):
        try:
            plan = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        except Exception:
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
        if object_list is None:
            continue
        names, positions = object_list
        if plan.separation(positions).min() < MATCH_RADIUS:
            continue
        if covering := [earlier for earlier, position in suggested
                        if plan.separation(position) < MATCH_RADIUS]:
            yield number, (f"{name}: not in the object list; the line suggested above "
                           f"for {covering[0]} covers it once it is added")
            continue
        separations = best.separation(positions)
        clashes = [(f'the list row "{names[separations.argmin()]}"', separations.min())]
        clashes += [(f"the line suggested above for {earlier}", best.separation(position))
                    for earlier, position in suggested]
        clashes = [(row, separation) for row, separation in clashes if separation < MATCH_RADIUS]
        source = "catalog" if best is not plan else "plan"
        if clashes:
            row, separation = clashes[0]
            yield number, (f"{name}: not within {MATCH_RADIUS:.0f} of a row of the object list, "
                           f"but a row for it at the {source} position would be "
                           f"{separation.to(u.arcmin):.1f} from {row}, closer than msumastro "
                           "allows. Sort this out by hand.")
            continue
        suggested.append((name, best))
        yield number, (f"{name}: not in the object list. Add this line ({source} coordinates) "
                       f"to feder_object_list.csv: {list_row(name, best)}")


def notes(text, object_list):
    """Yield (line number, message) for each target that matches a list row with another name.

    These are not problems: the images will just be named after the list row.
    """
    names, positions = object_list
    for number, name, ra, dec, excused in targets(text):
        try:
            plan = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        except Exception:
            continue   # problems() reports it
        separations = plan.separation(positions)
        row = separations.argmin()
        if separations[row] < MATCH_RADIUS and plain_name(name) != plain_name(names[row]):
            yield number, (f'{name} is {separations[row].to(u.arcmin):.1f} from the list row '
                           f'"{names[row]}"; its images will be named {names[row]}')


def main(paths, object_list_path=OBJECT_LIST):
    """Check the plans and print what is found; return 1 if there is any problem, else 0."""
    messages, remarks, suggested = [], [], []
    try:
        object_list = read_object_list(object_list_path)
    except Exception as exc:   # keep going: the catalog check does not need the list
        object_list = None
        messages.append(f"{os.path.basename(object_list_path)}: {exc}")
        print(messages[-1], flush=True)
    for path in paths:
        with open(path, encoding="utf-8-sig") as plan_file:
            text = plan_file.read()
        for number, message in problems(text, object_list=object_list, suggested=suggested):
            messages.append(f"{path}:{number}: {message}")
            print(messages[-1], flush=True)
        for number, message in notes(text, object_list) if object_list is not None else []:
            remarks.append(f"{path}:{number}: note: {message}")
            print(remarks[-1], flush=True)
    if (messages or remarks) and "GITHUB_STEP_SUMMARY" in os.environ:   # the run's Summary tab
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            if messages:
                summary.write("### Coordinate and object list problems\n\n"
                              + "".join(f"- `{m}`\n" for m in messages) + "\n")
            if remarks:
                summary.write("### Notes (these do not fail the check)\n\n"
                              + "".join(f"- `{m}`\n" for m in remarks) + "\n")
    return 1 if messages else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
