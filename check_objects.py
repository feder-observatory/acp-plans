"""Check the ACP plan files changed on a branch.

    python check_objects.py [--base REF] [--staged] [--verified-ref REF]
                            [--offline-ok] [--cache-dir DIR]

For every dated plan folder touched by the change this runs the chaining check
(plan_check.py) and checks the format of every plan line. In strict folders
(dated on or after STRICT_FROM) every target line of an added or changed plan
file must also agree with a catalog position for its name, be "already
verified" by the same target in an earlier strict plan in --verified-ref, or
carry a `; coords-ok: <reason>` comment on its own line above it (after the
previous target; only comments, #directives and blank lines in between).
A folder this change creates is strict whatever its date.

Exit status 0 = no errors, 1 = errors. Standard library only; the behaviour is
specified by tests/test_check_objects.py.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import check_folder_names
import plan_check

# Folders dated on or after this are strict: their problems are errors and their
# targets are looked up. PLACEHOLDER: set this to the day the check goes live
# (the merge date of the Part A PR) when the PR is opened.
STRICT_FROM = date(2026, 11, 16)

_SEXA = re.compile(r"^([+-]?)(\d{1,2}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def _sexagesimal(text):
    """Return (sign, a, b, c) for a 'a:b:c' string, or None."""
    m = _SEXA.match(text)
    if not m:
        return None
    sign, a, b, c = m.groups()
    a, b, c = int(a), int(b), float(c)
    if b >= 60 or c >= 60:
        return None
    return (-1 if sign == "-" else 1), a, b, c


def parse_ra(text):
    """Parse an RA 'h:m:s' string; return degrees or None if invalid."""
    parts = _sexagesimal(text)
    if parts is None or text.startswith(("+", "-")):
        return None
    _, h, m, s = parts
    if h >= 24:
        return None
    return (h + m / 60 + s / 3600) * 15


def parse_dec(text):
    """Parse a Dec '[+-]d:m:s' string; return degrees or None if invalid."""
    parts = _sexagesimal(text)
    if parts is None:
        return None
    sign, d, m, s = parts
    value = d + m / 60 + s / 3600
    if value > 90:
        return None
    return sign * value


def separation_arcsec(ra1, dec1, ra2, dec2):
    """Great-circle separation of two positions in degrees, in arcsec (haversine)."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = (math.sin((d2 - d1) / 2) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2)
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600


def format_separation(arcsec):
    if arcsec < 60:
        return f"{arcsec:.1f} arcsec"
    if arcsec < 3600:
        return f"{arcsec / 60:.1f} arcmin"
    return f"{arcsec / 3600:.1f} deg"


def format_position(ra_deg, dec_deg):
    """Format degrees as 'hh:mm:ss.ss +dd:mm:ss.s'."""
    centis = round((ra_deg % 360) / 15 * 360000) % (24 * 360000)
    h, rest = divmod(centis, 360000)
    m, cs = divmod(rest, 6000)
    sign = "-" if dec_deg < 0 else "+"
    tenths = round(abs(dec_deg) * 36000)
    d, rest = divmod(tenths, 36000)
    dm, ds = divmod(rest, 600)
    return f"{h:02d}:{m:02d}:{cs / 100:05.2f} {sign}{d:02d}:{dm:02d}:{ds / 10:04.1f}"


def name_key(name):
    """Comparison key: lower-case, drop a trailing .NN, remove spaces, '_' and '-'."""
    key = re.sub(r"\.\d\d$", "", name.strip().lower())
    return re.sub(r"[\s_\-]", "", key)


# ---------------------------------------------------------------- plan lines

@dataclass
class PlanLine:
    kind: str                 # blank, comment, directive, mp, flat, target, bad
    text: str = ""
    number: int = 0
    name: str = None
    ra_text: str = None
    dec_text: str = None
    ra: float = None          # degrees
    dec: float = None         # degrees
    problem: str = None       # for kind == "bad"
    override: str = None      # coords-ok reason ("" if empty), None if absent
    stray: bool = False       # a coords-ok comment that reaches no target


_FLAT_LINE = re.compile(r"\d+,\w+,\d+")      # as plan_check.PlanCheck.is_flat_config
_MP_LINE = re.compile(r"^MP\s+\S+$")
_SPACED_TARGET = re.compile(r"^(\S.*?)\s+(\S*:\S*)\s+(\S*:\S*)$")
_OVERRIDE = re.compile(r"^\s*;\s*coords-ok\s*:(.*)$", re.IGNORECASE)
TARGET_FORMAT = "`name<TAB>RA<TAB>Dec`"
# minus sign, hyphens and dashes that word processors put in place of "-"
UNICODE_DASHES = set("\u2212\u2010\u2011\u2012\u2013\u2014\u2015\ufe63\uff0d")


def classify_line(text):
    stripped = text.strip()
    if not stripped:
        return PlanLine("blank", text)
    if stripped.startswith(";"):
        return PlanLine("comment", text)
    if stripped.startswith("#"):
        return PlanLine("directive", text)
    if _MP_LINE.match(stripped):
        return PlanLine("mp", text)
    if _FLAT_LINE.match(text):
        return PlanLine("flat", text)
    fields = [f.strip() for f in text.rstrip().split("\t")]
    if len(fields) == 3 and fields[0] and " " not in fields[1] + fields[2]:
        name, ra_text, dec_text = fields
        ra, dec = parse_ra(ra_text), parse_dec(dec_text)
        problems = []
        if ra is None:
            problems.append(f"RA `{ra_text}` is not a valid coordinate "
                            "(use hours:minutes:seconds, for example `22:37:04.1`)")
        if dec is None and dec_text[:1] in UNICODE_DASHES:
            problems.append(f"Dec `{dec_text}` starts with a dash that is not the plain minus "
                            "sign. Delete it and type the `-` key instead")
        elif dec is None:
            problems.append(f"Dec `{dec_text}` is not a valid coordinate "
                            "(use degrees:minutes:seconds, for example `+34:24:56.8` or `-0:32:00.9`)")
        line = PlanLine("target", text, name=name, ra_text=ra_text, dec_text=dec_text,
                        ra=ra, dec=dec)
        if problems:
            line.kind = "bad"
            line.problem = "; ".join(problems) + "."
        return line
    if _SPACED_TARGET.match(stripped):
        return PlanLine("bad", text, problem=(
            "This looks like a target line, but its fields are separated by spaces. "
            f"Target lines must be {TARGET_FORMAT} with a TAB character between the "
            "name, the RA and the Dec (spaces are allowed only inside the name)."))
    return PlanLine("bad", text, problem=(
        f"Not a valid plan line. Expected a target line {TARGET_FORMAT} "
        "(separated by TAB characters), a `#directive`, or a `; comment`."))


def parse_plan_text(text):
    """Classify every line of a plan; attach coords-ok overrides to targets.

    A `; coords-ok: <reason>` comment applies to the next target line below it.
    Only comments, #directives and blank lines may lie in between; any other
    line ends its reach, and a marker that reaches no target is marked stray.
    The marker closest to the target wins.
    """
    lines = []
    for number, raw in enumerate(text.replace("\r", "").split("\n"), 1):
        line = classify_line(raw)
        line.number = number
        lines.append(line)
    pending = []        # markers waiting for a target
    for line in lines:
        if line.kind == "comment":
            if _OVERRIDE.match(line.text):
                pending.append(line)
            continue
        if line.kind in ("blank", "directive"):
            continue
        if line.kind in ("target", "bad") and line.name is not None and pending:
            line.override = _OVERRIDE.match(pending[-1].text).group(1).strip()
        else:
            for marker in pending:
                marker.stray = True
        pending = []
    for marker in pending:
        marker.stray = True
    return lines


def stray_overrides(lines):
    return [line for line in lines if line.stray]


OVERRIDE_PLACEMENT = "on its own line anywhere above the target line, after the previous target"


FLAT_CONFIG_NAMES = {"dflat.txt", "screenflats.txt"}


def is_flat_config(filename, text):
    """Flat-config files are exempt; same rule as plan_check, plus by name."""
    if filename.lower() in FLAT_CONFIG_NAMES:
        return True
    content = [ln for ln in text.replace("\r", "").split("\n")
               if ln.strip() and not ln.startswith(";")]
    return all(_FLAT_LINE.match(ln) for ln in content)


# ---------------------------------------------------------------- catalog responses

class ServiceError(Exception):
    """A catalog service failed or sent something other than the expected answer."""


@dataclass(frozen=True)
class Position:
    ra: float      # degrees, J2000
    dec: float
    source: str


_RESOLVER_LABELS = {"simbad": "SIMBAD", "ned": "NED", "vizier": "VizieR"}


def parse_sesame(text, mirror):
    """Positions in a Sesame XML answer ([] = name not known); ServiceError if not Sesame XML."""
    if "<Sesame" not in text:
        raise ServiceError("answer was not a Sesame XML document: " + _snippet(text))
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError as exc:
        raise ServiceError(f"could not parse Sesame XML: {exc}") from None
    positions = []
    for res in root.iter("Resolver"):
        ra, dec = res.findtext("jradeg"), res.findtext("jdedeg")
        if ra is None or dec is None:
            continue
        m = re.match(r"\s*\w+\s*=\s*([A-Za-z]+)", res.get("name", ""))
        label = _RESOLVER_LABELS.get(m.group(1).lower(), m.group(1)) if m else "Sesame"
        positions.append(Position(float(ra), float(dec), f"{label} via Sesame at {mirror}"))
    return positions


def _load_json(text):
    try:
        return json.loads(text)
    except ValueError:
        raise ServiceError("answer was not JSON: " + _snippet(text)) from None


def _snippet(text):
    return " ".join(text[:120].split()) or "(empty answer)"


def parse_mast(text, tic_id):
    data = _load_json(text)
    if not isinstance(data, dict) or data.get("status") not in ("COMPLETE", None):
        raise ServiceError(f"MAST status {data.get('status')!r}: {data.get('msg')!r}"
                           if isinstance(data, dict) else "unexpected MAST answer")
    return [Position(float(row["ra"]), float(row["dec"]), "TESS Input Catalog at MAST")
            for row in data.get("data") or []
            if _same_id(row.get("ID"), tic_id) and row.get("ra") is not None]


def _same_id(a, b):
    """Compare catalog ids as integers, so TIC_0123 is TIC 123."""
    try:
        return int(str(a)) == int(str(b))
    except ValueError:
        return False


def parse_gaia(text):
    data = _load_json(text)
    if not isinstance(data, dict) or "data" not in data:
        raise ServiceError("unexpected Gaia archive answer: " + _snippet(text))
    names = [col.get("name") for col in data.get("metadata", [])]
    positions = []
    for row in data["data"]:
        values = dict(zip(names, row))
        if values.get("ra2000") is not None:
            positions.append(Position(float(values["ra2000"]), float(values["dec2000"]),
                                      "Gaia DR3 (epoch 2000)"))
        elif values.get("ra") is not None:
            positions.append(Position(float(values["ra"]), float(values["dec"]),
                                      "Gaia DR3 (epoch 2016)"))
    return positions


def parse_vsx(text):
    data = _load_json(text)
    if not isinstance(data, dict) or "VSXObject" not in data:
        raise ServiceError("unexpected VSX answer: " + _snippet(text))
    obj = data["VSXObject"]
    if not isinstance(obj, dict) or "RA2000" not in obj:
        return []
    return [Position(float(obj["RA2000"]), float(obj["Declination2000"]), "AAVSO VSX")]


# ---------------------------------------------------------------- routing

_TIC = re.compile(r"^TIC[ _-]?(\d+)(?:\.\d+)?$", re.IGNORECASE)
_GAIA = re.compile(r"^Gaia[ _-]?DR3[ _-]?(\d+)$", re.IGNORECASE)


def route(name):
    """('tic', id), ('gaia', id) or ('sesame', None)."""
    m = _TIC.match(name)
    if m:
        return "tic", m.group(1)
    m = _GAIA.match(name)
    if m:
        return "gaia", m.group(1)
    return "sesame", None


def name_candidates(name):
    """Ordered name spellings to try (the normalisations of catalog-experiment/resolve.py)."""
    out = []

    def add(text):
        text = re.sub(r"\s+", " ", text).strip()
        if text and text not in out:
            out.append(text)

    add(name)
    add(name.replace("_", "-"))                       # ASASSN_26be -> ASASSN-26be
    m = re.match(r"^(.*\d)[ _-]([b-h])$", name)       # kelt-16-b -> kelt-16 b
    if m:
        add(f"{m.group(1)} {m.group(2)}")
    spaced = re.sub(r"[_-]", " ", name)
    add(spaced)                                       # ey-uma -> ey uma
    add(re.sub(r"^([A-Za-z]+)(\d)", r"\1 \2", spaced))  # NGC7331 -> NGC 7331
    m = re.match(r"^[vV]0*(\d+)\s+([A-Za-z]{3})$", spaced)  # v0512 cam -> V512 Cam
    if m:
        add(f"V{m.group(1)} {m.group(2).capitalize()}")
    return out


# ---------------------------------------------------------------- requests

STAR_TOLERANCE = 20.0       # arcsec, for TIC, Gaia and VSX answers
SESAME_TOLERANCE = 120.0    # arcsec, for Sesame (SIMBAD/NED/VizieR) answers

SESAME_MIRRORS = {
    "CDS": "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?",
    "CfA": "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?",
}
MAST_URL = "https://mast.stsci.edu/api/v0/invoke"
GAIA_URL = "https://gea.esac.esa.int/tap-server/tap/sync"
VSX_URL = "https://www.aavso.org/vsx/index.php?view=api.object&format=json&ident="


@dataclass(frozen=True)
class Request:
    service: str               # for messages, e.g. "Sesame at CDS"
    url: str
    data: str                  # POST body, or None for GET
    tolerance: float
    parse: object = field(compare=False, repr=False)


def sesame_request(mirror, query):
    return Request(f"Sesame at {mirror}", SESAME_MIRRORS[mirror] + urllib.parse.quote(query),
                   None, SESAME_TOLERANCE, lambda text: parse_sesame(text, mirror))


def vsx_request(query):
    return Request("AAVSO VSX", VSX_URL + urllib.parse.quote(query), None, STAR_TOLERANCE,
                   parse_vsx)


def mast_request(tic_id):
    tic_id = str(int(tic_id))
    req = {"service": "Mast.Catalogs.Filtered.Tic", "format": "json",
           "params": {"columns": "ID,ra,dec,pmRA,pmDEC",
                      "filters": [{"paramName": "ID", "values": [tic_id]}]}}
    return Request("MAST", MAST_URL, urllib.parse.urlencode({"request": json.dumps(req)}),
                   STAR_TOLERANCE, lambda text: parse_mast(text, tic_id))


def gaia_request(source_id):
    prop = "EPOCH_PROP_POS(ra,dec,parallax,pmra,pmdec,radial_velocity,ref_epoch,2000)"
    query = (f"SELECT source_id, ra, dec, COORD1({prop}) AS ra2000, COORD2({prop}) AS dec2000 "
             f"FROM gaiadr3.gaia_source WHERE source_id={int(source_id)}")
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                   "QUERY": query})
    return Request("Gaia archive", GAIA_URL, body, STAR_TOLERANCE, parse_gaia)


# ---------------------------------------------------------------- catalog client

def _service_error(request, exc):
    detail = str(exc) if isinstance(exc, ServiceError) else f"{type(exc).__name__}: {exc}"
    return ServiceError(f"{request.service}: {detail}")


class Catalogs:
    """Asks catalog services through an injected fetch(url, data) -> str.

    Each distinct request is made at most once per run (answers and failures are
    remembered), and well-formed answers are kept in cache_dir if one is given.
    A service whose transport fails (timeout, connection or HTTP error, after the
    fetcher's retry) is not asked again in the run, so an outage costs one
    timeout rather than one per name. A malformed answer fails only its request.
    """

    def __init__(self, fetch, cache_dir=None):
        self.fetch = fetch
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.memo = {}
        self.down = {}       # service -> its first failure; not asked again this run

    def _cache_file(self, request):
        key = hashlib.sha256(f"{request.url}|{request.data or ''}".encode()).hexdigest()[:32]
        return self.cache_dir / f"{key}.txt"

    def ask(self, request):
        """Positions from one request ([] = not known); raises ServiceError on failure."""
        key = (request.url, request.data)
        if key not in self.memo:
            self.memo[key] = self._ask(request)
        result = self.memo[key]
        if isinstance(result, ServiceError):
            raise result
        return result

    def _ask(self, request):
        cached = self._cache_file(request) if self.cache_dir else None
        if cached and cached.exists():
            return request.parse(cached.read_text(encoding="utf-8"))
        if request.service in self.down:
            return self.down[request.service]
        try:
            text = self.fetch(request.url, request.data)
        except Exception as exc:   # transport failure: the service is down for this run
            self.down[request.service] = _service_error(request, exc)
            return self.down[request.service]
        try:
            positions = request.parse(text)
        except Exception as exc:   # a malformed answer: only this request failed
            return _service_error(request, exc)
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
        return positions


# ---------------------------------------------------------------- verdicts

@dataclass
class Verdict:
    status: str                 # confirmed, mismatch, notfound, unreachable
    confirmed_by: Position = None
    nearest: Position = None
    nearest_sep: float = None   # arcsec
    nearest_tolerance: float = None
    failures: list = field(default_factory=list)


def check_target(name, ra, dec, catalogs):
    """Compare a plan position (degrees) with the catalogs, stopping at the first confirmation."""
    verdict = Verdict("notfound")

    def try_request(request):
        """Ask one source; record its answer or failure. Returns (confirmed, name_known)."""
        try:
            positions = catalogs.ask(request)
        except ServiceError as exc:
            if str(exc) not in verdict.failures:
                verdict.failures.append(str(exc))
            return False, False
        for pos in positions:
            sep = separation_arcsec(ra, dec, pos.ra, pos.dec)
            if sep <= request.tolerance:
                verdict.status, verdict.confirmed_by = "confirmed", pos
                verdict.nearest, verdict.nearest_sep = pos, sep
                verdict.nearest_tolerance = request.tolerance
                return True, True
            if verdict.nearest_sep is None or sep < verdict.nearest_sep:
                verdict.nearest, verdict.nearest_sep = pos, sep
                verdict.nearest_tolerance = request.tolerance
        return False, bool(positions)

    kind, ident = route(name)
    if kind in ("tic", "gaia"):
        request = mast_request(ident) if kind == "tic" else gaia_request(ident)
        if try_request(request)[0]:
            return verdict
    else:
        candidates = name_candidates(name)
        for query in candidates:
            found = False
            for mirror in SESAME_MIRRORS:
                confirmed, known = try_request(sesame_request(mirror, query))
                if confirmed:
                    return verdict
                found = found or known
            if found:
                break
        for query in candidates[1:] or candidates:
            confirmed, known = try_request(vsx_request(query))
            if confirmed:
                return verdict
            if known:
                break
    if verdict.failures:
        verdict.status = "unreachable"
    elif verdict.nearest is not None:
        verdict.status = "mismatch"
    return verdict


# ---------------------------------------------------------------- network

USER_AGENT = "acp-plans-coordinate-check (python-urllib)"
TIMEOUT = 30          # seconds per request
MIN_INTERVAL = 1.0    # seconds between requests, to be polite to the services
RETRY_DELAY = 3.0     # seconds before the single retry


class UrllibFetcher:
    """fetch(url, data) -> text over HTTP(S); one retry; raises on failure."""

    def __init__(self, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic):
        self.urlopen, self.sleep, self.clock = urlopen, sleep, clock
        self.last = None

    def _once(self, url, data):
        now = self.clock()
        if self.last is not None and now - self.last < MIN_INTERVAL:
            self.sleep(MIN_INTERVAL - (now - self.last))
        request = urllib.request.Request(url, data=data.encode() if data is not None else None,
                                         headers={"User-Agent": USER_AGENT})
        try:
            with self.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", "replace")
        finally:
            self.last = self.clock()

    def __call__(self, url, data=None):
        try:
            return self._once(url, data)
        except Exception:
            self.sleep(RETRY_DELAY)
            return self._once(url, data)


# ---------------------------------------------------------------- findings and folders

NOT_PLAN_FOLDERS = {"tests"}


@dataclass
class Finding:
    level: str          # error, warning, override, notice
    path: str
    line: int
    message: str


def folder_date(name):
    """The date in the first 10 characters of a folder name, or None."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}", name):
        return None
    try:
        return datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def is_strict(day):
    return day >= STRICT_FROM


def folder_is_strict(folder, existing_folders):
    """Strict if dated on/after STRICT_FROM, or if this change creates the folder."""
    return is_strict(folder_date(folder)) or folder not in existing_folders


def new_old_folder_warning(folder):
    return Finding("warning", folder, 0,
                   f"Folder `{folder}` is new, but its date is before "
                   f"{STRICT_FROM.isoformat()}, when this check started. Check the year in "
                   "the folder name.")


def is_plan_file_name(name):
    """A .txt file name, in any letter case."""
    return name.lower().endswith(".txt")


@dataclass
class Changes:
    folders: set = field(default_factory=set)       # dated top-level folders touched
    plan_files: set = field(default_factory=set)    # 'folder/name.txt' added or changed
    nested_files: set = field(default_factory=set)  # '.txt' files in a subfolder of a dated folder
    findings: list = field(default_factory=list)


def classify_changed_paths(paths):
    changes = Changes()
    bad_folders = set()
    for path in paths:
        parts = path.split("/")
        top = parts[0]
        if len(parts) == 1:
            if is_plan_file_name(path):
                changes.findings.append(Finding(
                    "error", path, 0,
                    f"`{path}` is a plan file at the top of the repository. Plan files must "
                    "be inside a dated folder: move these into a dated folder named "
                    "`YYYY-MM-DD` (when uploading in the web page, drag the whole folder in)."))
            continue
        if top.startswith(".") or top in NOT_PLAN_FOLDERS:
            continue
        if folder_date(top) is None:
            if top not in bad_folders:
                bad_folders.add(top)
                changes.findings.append(Finding(
                    "error", top, 0,
                    f"Folder `{top}` does not start with a date. Plan folders must be named "
                    "`YYYY-MM-DD` (for example `2026-11-20`), optionally followed by "
                    "`-something` with no spaces."))
            continue
        if not check_folder_names.check_name(top):
            if top not in bad_folders:
                bad_folders.add(top)
                changes.findings.append(Finding(
                    "error", top, 0,
                    f"`{top}` is not a valid plan folder name. Plan folders must be named "
                    "`YYYY-MM-DD` (for example `2026-11-20`), optionally followed by "
                    "`-something` with no spaces. Rename the folder."))
            continue
        changes.folders.add(top)
        if is_plan_file_name(path):
            if len(parts) == 2:
                changes.plan_files.add(path)
            else:
                changes.nested_files.add(path)
    return changes


def nested_file_finding(path, level):
    top = path.split("/", 1)[0]
    return Finding(level, path, 0,
                   f"`{path}` is in a subfolder, so it is not checked. Plan files must be "
                   f"directly inside the dated folder: move the files up into `{top}/` (this "
                   "happens when a folder is dragged into another folder while uploading).")


# ---------------------------------------------------------------- file and line checks

def check_file_format(path, text, level):
    """Format findings (at the given level) and the target/MP lines of one plan file."""
    if is_flat_config(path.rsplit("/", 1)[-1], text):
        return [], []
    findings, targets = [], []
    for line in parse_plan_text(text):
        if line.stray:
            findings.append(Finding("warning", path, line.number,
                                    "This `; coords-ok:` line does not apply to any target. "
                                    f"Put it {OVERRIDE_PLACEMENT}."))
        elif line.kind == "bad":
            findings.append(Finding(level, path, line.number, line.problem))
        elif line.kind in ("target", "mp"):
            targets.append(line)
    return findings, targets


VERIFY_TOLERANCE = 20.0   # arcsec, for "already verified" by an earlier plan


@dataclass(frozen=True)
class VerifiedEntry:
    key: str
    day: date
    path: str
    line: int
    ra: float
    dec: float
    override: str


class VerifiedIndex:
    """Targets in strict folders of a reference tree, for the 'already verified' rule."""

    def __init__(self, entries):
        self.by_key = {}
        for entry in entries:
            self.by_key.setdefault(entry.key, []).append(entry)

    @classmethod
    def from_files(cls, files):
        entries = []
        for path, text in files:
            parts = path.split("/")
            day = folder_date(parts[0]) if len(parts) == 2 else None
            if day is None or not is_strict(day) or not is_plan_file_name(path):
                continue
            if is_flat_config(parts[1], text):
                continue
            for line in parse_plan_text(text):
                if line.kind == "target":
                    entries.append(VerifiedEntry(name_key(line.name), day, path, line.number,
                                                 line.ra, line.dec, line.override))
        return cls(entries)

    def find(self, name, ra, dec, before):
        """An earlier entry with the same name key within 20 arcsec; prefers ones without override."""
        matches = [e for e in self.by_key.get(name_key(name), [])
                   if e.day < before
                   and separation_arcsec(ra, dec, e.ra, e.dec) <= VERIFY_TOLERANCE]
        matches.sort(key=lambda e: (bool(e.override), e.day, e.path, e.line))
        return matches[0] if matches else None


def check_line(path, line, day, index, catalogs, offline_ok):
    """Full check of one correctly formatted target (or MP) line in a strict changed file."""
    if line.kind == "mp":
        return [Finding("notice", path, line.number,
                        f"Minor planet line `{line.text.strip()}` is not checked against a catalog.")]
    name = line.name
    if line.override is not None:
        if not line.override:
            return [Finding("error", path, line.number,
                            f"The `; coords-ok:` line above `{name}` has no reason. Write where "
                            "the coordinates came from or why the offset is deliberate, for "
                            "example `; coords-ok: mosaic pane 2 of M31`.")]
        return [Finding("override", path, line.number,
                        f"`{name}`: coordinates accepted without a catalog check because of "
                        f"`; coords-ok: {line.override}`.")]
    earlier = index.find(name, line.ra, line.dec, day)
    if earlier is not None:
        where = f"{earlier.path} line {earlier.line}"
        if earlier.override:
            return [Finding("override", path, line.number,
                            f"`{name}`: verified by earlier plan with override ({where}, "
                            f"`; coords-ok: {earlier.override}`).")]
        return [Finding("notice", path, line.number,
                        f"`{name}`: verified by earlier plan ({where}).")]
    verdict = check_target(name, line.ra, line.dec, catalogs)
    return [verdict_finding(path, line, verdict, offline_ok)]


def _nearest_text(verdict):
    return (f"{format_separation(verdict.nearest_sep)} from the catalog position "
            f"{format_position(verdict.nearest.ra, verdict.nearest.dec)} "
            f"({verdict.nearest.source})")


def verdict_finding(path, line, verdict, offline_ok):
    name = line.name
    if verdict.status == "confirmed":
        return Finding("notice", path, line.number,
                       f"`{name}` matches {verdict.confirmed_by.source} "
                       f"({format_separation(verdict.nearest_sep)} away).")
    if verdict.status == "mismatch":
        return Finding("error", path, line.number,
                       f"`{name}` is {_nearest_text(verdict)}. The limit for that catalog is "
                       f"{format_separation(verdict.nearest_tolerance)}. Fix the coordinates, "
                       "or if the offset is deliberate add `; coords-ok: <reason>` "
                       f"{OVERRIDE_PLACEMENT}.")
    if verdict.status == "notfound":
        return Finding("error", path, line.number,
                       f"`{name}` was not found in any catalog. Check the spelling of the name; "
                       "if it is right, add `; coords-ok: <where the coordinates came from>` "
                       f"{OVERRIDE_PLACEMENT}.")
    failures = "; ".join(verdict.failures)
    if verdict.nearest is not None:
        message = (f"`{name}` is {_nearest_text(verdict)}, and the check could not reach every "
                   f"catalog service to compare with ({failures}). Check the coordinates; if "
                   "they are right, use Re-run jobs, and if it keeps failing ask an admin.")
    else:
        message = (f"Could not reach the catalog service to confirm `{name}` ({failures}). "
                   "Use Re-run jobs; if it keeps failing ask an admin.")
    return Finding("warning" if offline_ok else "error", path, line.number, message)


# ---------------------------------------------------------------- reports

LEVELS = ("error", "warning", "override", "notice")
_HEADINGS = {"error": "Errors", "warning": "Warnings", "override": "Overrides (for the reviewer)",
             "notice": "Notices"}


def _sorted(findings):
    return sorted(findings, key=lambda f: (LEVELS.index(f.level), f.path, f.line))


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def count_line(findings):
    counts = {level: sum(f.level == level for f in findings) for level in LEVELS}
    return ", ".join(_plural(counts[level], level) for level in LEVELS)


def _location(f):
    return f"{f.path}:{f.line}" if f.line else f.path


_NAMED_ESCAPES = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}


def visible(text):
    """Text with control and line-break characters shown as escapes, so it stays one line."""
    out = []
    for ch in text:
        if "\udc80" <= ch <= "\udcff":         # a non-UTF-8 byte (surrogateescape)
            out.append(f"\\x{ord(ch) - 0xdc00:02x}")
        elif "\ud800" <= ch <= "\udfff":
            out.append(f"\\u{ord(ch):04x}")
        elif ch in _NAMED_ESCAPES:
            out.append(_NAMED_ESCAPES[ch])
        elif unicodedata.category(ch) in ("Cc", "Zl", "Zp"):
            out.append(f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _bytes_visible(text):
    """Only non-UTF-8 bytes (surrogateescape) shown as \\xNN; everything else unchanged."""
    return re.sub("[\ud800-\udfff]", lambda m: visible(m.group()), text)


def format_text_report(findings):
    lines = [f"{f.level.upper():<8}{visible(_location(f))}: {visible(f.message)}"
             for f in _sorted(findings)]
    lines.append(count_line(findings))
    return "\n".join(lines)


_MD_SPECIAL = set("\\`*_[]()<>!&|~")


def _md_text(text):
    """Plain Markdown text: no markup, links, HTML or line breaks can come from it."""
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in visible(text))


def _md_code(text):
    """A code span holding text literally, whatever backticks it contains."""
    text = visible(text)
    if not text:
        return ""
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _md_message(message):
    """Render a message's `code` parts as code spans and the rest as escaped text.

    However the backticks pair up (a name may contain one), every piece is
    either escaped text or a backtick-free code span, so nothing can escape.
    """
    parts = message.split("`")
    out = []
    for i, part in enumerate(parts):
        closed = i % 2 == 1 and i < len(parts) - 1
        if closed and part:
            out.append(_md_code(part))
        elif i % 2 == 1:
            out.append(_md_text("`" + part + ("`" if closed else "")))
        else:
            out.append(_md_text(part))
    return "".join(out)


def format_markdown(findings):
    failed = any(f.level == "error" for f in findings)
    out = ["## Plan check: " + ("FAILED" if failed else "passed"), "", count_line(findings), ""]
    if failed:
        out += ["Fix each error below in the pull request's **Files changed** tab "
                "(\"...\" -> Edit file); the check runs again on every commit.", ""]
    for level in LEVELS:
        group = [f for f in _sorted(findings) if f.level == level]
        if not group:
            continue
        out += [f"### {_HEADINGS[level]}", ""]
        for f in group:
            where = _md_code(f.path) + (f" line {f.line}" if f.line else "")
            out.append(f"- {where}: {_md_message(f.message)}")
        out.append("")
    return "\n".join(out)


def _escape_data(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text):
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def format_annotations(findings):
    """GitHub Actions workflow commands; notices are left to the summary."""
    kinds = {"error": "error", "warning": "warning", "override": "notice"}
    out = []
    for f in _sorted(findings):
        if f.level not in kinds:
            continue
        props = f"file={_escape_property(_bytes_visible(f.path))}"
        if f.line:
            props += f",line={f.line}"
        out.append(f"::{kinds[f.level]} {props}::{_escape_data(visible(f.message))}")
    return out


# ---------------------------------------------------------------- git

class GitError(Exception):
    pass


def git_output(repo, *args):
    """Run git with an argument list (never a shell) and return stdout bytes."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def _split_z(output):
    return [p for p in output.decode("utf-8", "surrogateescape").split("\0") if p]


def diff_base(repo, base, staged):
    """The commit the change is compared with: HEAD when staged, else the merge-base."""
    if staged:
        return "HEAD"
    return git_output(repo, "merge-base", base, "HEAD").decode().strip()


def changed_paths(repo, base_commit, staged):
    """(added/changed paths, all touched paths including deletions)."""
    spec = ["--cached", base_commit] if staged else [base_commit, "HEAD"]
    diff = ["diff", "--name-only", "-z", "--no-renames"]
    changed = _split_z(git_output(repo, *diff, "--diff-filter=ACMRT", *spec))
    touched = _split_z(git_output(repo, *diff, *spec))
    return changed, touched


def ref_top_level_names(repo, ref):
    """Names of the entries at the top of a git ref's tree."""
    return set(_split_z(git_output(repo, "ls-tree", "-z", "--name-only", ref)))


def ref_plan_files(repo, ref):
    """(path, text) of the plan files in strict dated folders of a git ref's tree."""
    for entry in _split_z(git_output(repo, "ls-tree", "-r", "-z", ref)):
        info, path = entry.split("\t", 1)
        parts = path.split("/")
        if info.split(" ")[0] != "100644" and info.split(" ")[0] != "100755":
            continue                     # symlinks (120000) and submodules are not plans
        if len(parts) != 2 or not is_plan_file_name(path):
            continue
        day = folder_date(parts[0])
        if day is None or not is_strict(day):
            continue
        text = git_output(repo, "show", f"{ref}:{path}").decode("utf-8-sig", "replace")
        yield path, text


# ---------------------------------------------------------------- main

def parse_args(argv):
    parser = argparse.ArgumentParser(description=(
        "Check the plan files changed on this branch: plan line format, chaining, and "
        "target coordinates against catalogs."))
    parser.add_argument("--base", default="origin/main",
                        help="compare HEAD with its merge-base with this ref (default: origin/main)")
    parser.add_argument("--staged", action="store_true",
                        help="check staged changes against HEAD (pre-commit mode)")
    parser.add_argument("--verified-ref", default=None,
                        help="git ref whose tree holds the already verified plans "
                             "(default: the base ref, or HEAD with --staged)")
    parser.add_argument("--offline-ok", action="store_true",
                        help="an unreachable catalog service is a warning instead of an error")
    parser.add_argument("--cache-dir", default=None,
                        help="directory for cached catalog answers (default: a temporary directory)")
    return parser.parse_args(argv)


def chaining_findings(repo, folder, level="error"):
    try:
        problems = plan_check.find_problems(repo / folder)
    except Exception as exc:   # plan_check can crash on unusual files
        problems = [f"the chaining check could not run: {type(exc).__name__}: {exc}"]
    prefix = str(repo) + "/"
    return [Finding(level, folder, 0, "Chaining: " + p.replace(prefix, "")) for p in problems]


def write_summary(markdown):
    """Append to the GitHub Actions job summary, if there is one."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


def report_failure(message):
    """A problem that stops the check: one ERROR line on stdout and in the summary."""
    print(visible(f"ERROR   {message}"))
    write_summary(f"## Plan check: FAILED\n\nERROR {_md_text(message)}")
    return 1


def main(argv=None, fetch=None):
    args = parse_args(argv)
    try:
        return run(args, fetch)
    except Exception as exc:     # never a traceback, never a pass
        message = ("The plan check stopped because of an unexpected problem "
                   f"({type(exc).__name__}: {exc}). Use Re-run jobs; if it keeps failing "
                   "ask an admin.")
        try:
            return report_failure(message)
        except Exception:
            print(visible(f"ERROR   {message}"))
            return 1


def run(args, fetch):
    try:
        repo = Path(git_output(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
        base_commit = diff_base(repo, args.base, args.staged)
        changed, touched = changed_paths(repo, base_commit, args.staged)
        existing_folders = ref_top_level_names(repo, base_commit)
    except GitError as exc:
        return report_failure(f"could not run git: {exc}")
    changes = classify_changed_paths(changed)
    folders = changes.folders | classify_changed_paths(touched).folders
    findings = list(changes.findings)
    if not folders and not findings:
        print("Nothing to check: no plan files or plan folders changed.")
        write_summary("## Plan check: passed\n\n"
                      "Nothing to check: no plan files or plan folders changed.")
        return 0

    to_check = []
    for folder in sorted(folders):
        directory = repo / folder
        day = folder_date(folder)
        # Older folders already in the base only warn: many predate the chaining rules.
        strict = folder_is_strict(folder, existing_folders)
        findings += [nested_file_finding(path, "error" if strict else "warning")
                     for path in sorted(changes.nested_files)
                     if path.split("/", 1)[0] == folder]
        plan_paths = (sorted(p for p in directory.iterdir() if is_plan_file_name(p.name))
                      if directory.is_dir() else [])
        if not plan_paths:
            continue
        if strict and not is_strict(day):
            findings.append(new_old_folder_warning(folder))
        findings += chaining_findings(repo, folder, "error" if strict else "warning")
        for plan_path in plan_paths:
            rel = f"{folder}/{plan_path.name}"
            full = strict and rel in changes.plan_files
            level = "error" if full else "warning"
            if plan_path.is_symlink():
                findings.append(Finding("error" if strict else "warning", rel, 0,
                                        f"`{rel}` is a symbolic link. Plan files must be "
                                        "regular files, not links."))
                continue
            if not plan_path.is_file():
                findings.append(Finding("error" if strict else "warning", rel, 0,
                                        f"`{rel}` is not a regular file. Plan files must be "
                                        "regular files."))
                continue
            try:
                text = plan_path.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError:
                findings.append(Finding(level, rel, 0, "The file is not UTF-8 text. Save it "
                                        "as plain text (UTF-8)."))
                continue
            format_problems, targets = check_file_format(rel, text, level)
            findings += format_problems
            if full:
                to_check += [(rel, line, day) for line in targets]

    if to_check:
        try:
            index = VerifiedIndex([])
            if any(line.kind == "target" and line.override is None for _, line, _ in to_check):
                ref = args.verified_ref or ("HEAD" if args.staged else args.base)
                index = VerifiedIndex.from_files(ref_plan_files(repo, ref))
        except GitError as exc:
            return report_failure(f"could not read the verified plans from git: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            catalogs = Catalogs(fetch or UrllibFetcher(), args.cache_dir or tmp)
            for rel, line, day in to_check:
                findings += check_line(rel, line, day, index, catalogs, args.offline_ok)

    print(visible(f"Checked plan folders: {', '.join(sorted(folders)) or '(none)'}"))
    print(format_text_report(findings))
    if os.environ.get("GITHUB_ACTIONS"):
        print("\n".join(format_annotations(findings)))
    write_summary(format_markdown(findings))
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
