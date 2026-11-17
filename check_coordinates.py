"""Check that the target coordinates in ACP plan files roughly match a catalog.

Usage: python check_coordinates.py PLAN.txt [PLAN.txt ...]

A target line is "name<TAB>ra<TAB>dec". To accept a target the catalogs do
not know (or a deliberate offset), put a comment containing "coords-ok"
somewhere between the previous target and this one.
"""
import re
import sys

import astropy.units as u
from astropy.coordinates import SkyCoord

TOLERANCE = 2 * u.arcmin


def targets(text):
    """Yield (line number, name, ra, dec) for each target line to be checked."""
    excused = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith(";"):
            excused = excused or "coords-ok" in line
        elif "\t" in line and not line.startswith("#"):
            name, ra, dec = (field.strip() for field in line.split("\t")[:3])
            if not excused and not name.upper().startswith("MP "):
                yield number, name, ra, dec
            excused = False


def catalog_position(name):
    """Look the name up: TIC at MAST, Gaia DR3 at ESA, anything else via Sesame."""
    if match := re.fullmatch(r"TIC[ _-]?(\d+)(\.\d+)?", name, re.IGNORECASE):
        from astroquery.mast import Catalogs
        row = Catalogs.query_criteria(catalog="Tic", ID=match[1])[0]
        return SkyCoord(row["ra"], row["dec"], unit="deg")
    if match := re.fullmatch(r"Gaia[ _-]?DR3[ _-]?(\d+)", name, re.IGNORECASE):
        from astroquery.gaia import Gaia
        row = Gaia.launch_job("SELECT ra, dec FROM gaiadr3.gaia_source "
                              f"WHERE source_id = {match[1]}").get_results()[0]
        return SkyCoord(row["ra"], row["dec"], unit="deg")
    spellings = [name, name.replace("_", " "), re.sub("[_-]", " ", name)]
    for spelling in dict.fromkeys(spellings):       # each spelling once, in order
        try:
            return SkyCoord.from_name(spelling)
        except Exception as exc:
            error = exc
    raise error


def problems(text, lookup=catalog_position):
    """Yield (line number, message) for each target that does not check out."""
    for number, name, ra, dec in targets(text):
        try:
            plan = SkyCoord(ra, dec, unit=(u.hourangle, u.deg))
        except Exception:
            yield number, f"{name}: cannot read the coordinates {ra} {dec}"
            continue
        try:
            catalog = lookup(name)
        except Exception as exc:
            yield number, f"{name}: no catalog position ({exc})"
            continue
        separation = plan.separation(catalog)
        if separation > TOLERANCE:
            yield number, (f"{name}: the plan has {ra} {dec}, but the catalog has "
                           f"{catalog.to_string('hmsdms', sep=':', precision=1)}, "
                           f"{separation.to(u.arcmin):.1f} away")


if __name__ == "__main__":
    failed = False
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8-sig") as plan_file:
            for number, message in problems(plan_file.read()):
                print(f"{path}:{number}: {message}")
                failed = True
    sys.exit(failed)
