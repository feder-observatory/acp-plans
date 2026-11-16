import json
import re
import socket
import sys
import time
from pathlib import Path

import astropy.units as u
import pytest
import requests
from astropy.coordinates import SkyCoord
from astropy.table import MaskedColumn, Table

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import check_objects as co  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never use the network: any connection attempt is refused and fails the test."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise OSError("a test tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield attempts
    assert attempts == [], "a test tried to use the network"


@pytest.fixture(autouse=True)
def no_pause_between_lookups(monkeypatch):
    monkeypatch.setattr(co, "MIN_INTERVAL", 0)


def test_network_is_blocked(no_network):
    with pytest.raises(requests.ConnectionError):
        requests.get("https://example.org/", timeout=1)
    assert no_network
    no_network.clear()


def sky(ra_deg, dec_deg):
    return SkyCoord(ra_deg * u.deg, dec_deg * u.deg)


def plan_coord(ra_text, dec_text):
    return SkyCoord(co.parse_ra(ra_text), co.parse_dec(dec_text))


# ---------------------------------------------------------------- sexagesimal

@pytest.mark.parametrize("text, hours", [
    ("22:37:04.0506048", 22 + 37 / 60 + 4.0506048 / 3600),   # long decimals
    ("0:43:44", 43 / 60 + 44 / 3600),                          # unpadded hours
    ("09:02:20.8", 9 + 2 / 60 + 20.8 / 3600),
    ("23:58:24", 23 + 58 / 60 + 24 / 3600),
    ("1:2:3", 1 + 2 / 60 + 3 / 3600),
])
def test_parse_ra_valid(text, hours):
    assert co.parse_ra(text).deg == pytest.approx(hours * 15)


@pytest.mark.parametrize("text", [
    "0:43:444",        # the real typo
    "10:16:41.72s",    # trailing unit letter (real); astropy alone reads it as 0.69 s
    "24:00:00",        # hour out of range
    "25:00:00",
    "12:60:00",        # astropy alone only warns about 60 minutes or seconds
    "12:00:60",
    "12:59:60.0",
    "+12:00:00",       # RA is never signed
    "-1:00:00",
    "12:00",           # two fields
    "12.5",
    "",
    "12:00:00.",
    "12h00m00s",       # forms astropy accepts but ACP plans do not use
    "12 00 00",
    "1:2:3.5e1",
])
def test_parse_ra_invalid(text):
    assert co.parse_ra(text) is None


@pytest.mark.parametrize("text, degrees", [
    ("34:24:56.80008", 34 + 24 / 60 + 56.80008 / 3600),   # unsigned Dec
    ("+49:49:09.3", 49 + 49 / 60 + 9.3 / 3600),
    ("-0:32:00.9", -(32 / 60 + 0.9 / 3600)),              # negative zero degrees
    ("-05:23:28", -(5 + 23 / 60 + 28 / 3600)),
    ("90:00:00", 90.0),
    ("-90:00:00", -90.0),
    ("8:01:02", 8 + 1 / 60 + 2 / 3600),
])
def test_parse_dec_valid(text, degrees):
    assert co.parse_dec(text).deg == pytest.approx(degrees)


@pytest.mark.parametrize("text", [
    "+84.32.05.6",     # dots instead of colons (real)
    "23:19:24.1:",     # trailing colon (real)
    "91:00:00",        # astropy alone accepts these two
    "90:00:01",
    "-90:00:01",
    "45:60:00",
    "45:00:60",
    "--45:00:00",
    "+-1:00:00",
    "45:00",
    "45d00m00s",
    "",
])
def test_parse_dec_invalid(text):
    assert co.parse_dec(text) is None


@pytest.mark.parametrize("line, field", [
    ("M31\t0:43:444\t41:46:10", "RA `0:43:444`"),
    ("HD37387\t05:39:14.83\t23:19:24.1:", "Dec `23:19:24.1:`"),
    ("x\t10:16:41.72s\t+10:00:00", "RA `10:16:41.72s`"),
    ("x\t10:16:41.72\t+84.32.05.6", "Dec `+84.32.05.6`"),
])
def test_real_malformed_values_are_rejected_with_a_clear_message(line, field):
    parsed = co.classify_line(line)
    assert parsed.kind == "bad"
    assert f"{field} is not a valid coordinate" in parsed.problem


# ---------------------------------------------------------------- formatting

@pytest.mark.parametrize("arcsec, text", [
    (8.46, "8.5 arcsec"),
    (59.9, "59.9 arcsec"),
    (192.0, "3.2 arcmin"),
    (7200.0, "2.0 deg"),
])
def test_format_separation(arcsec, text):
    assert co.format_separation(arcsec * u.arcsec) == text


def test_format_position():
    # ey uma from Sesame: 135.5865 49.8192
    assert co.format_position(sky(135.58650, 49.81925)) == "09:02:20.76 +49:49:09.3"
    assert co.format_position(sky(0.0, -0.5336)) == "00:00:00.00 -00:32:01.0"


# ---------------------------------------------------------------- names

@pytest.mark.parametrize("name, key", [
    ("TIC_435934762.01", "tic435934762"),
    ("NGC 7331", "ngc7331"),
    ("ngc_7331", "ngc7331"),
    ("ey-uma", "eyuma"),
    ("EY UMa", "eyuma"),
    ("Gaia-DR3-1016132190708213504", "gaiadr31016132190708213504"),
    ("M31", "m31"),
])
def test_name_key(name, key):
    assert co.name_key(name) == key


# ---------------------------------------------------------------- line parsing

@pytest.mark.parametrize("text, kind", [
    ("", "blank"),
    ("   ", "blank"),
    (";", "comment"),
    ("; Object NGC 7331", "comment"),
    ("#chain NGC7790.txt", "directive"),
    ("#REPEAT 999", "directive"),
    ("MP 00018", "mp"),
    ("MP D7170", "mp"),
    ("10,ip,1", "flat"),
    ("NGC 7331\t22:37:04.0506048\t34:24:56.80008", "target"),
    ("ey-uma\t09:02:20.8\t+49:49:09.3", "target"),
    ("x\t0:43:44\t-0:32:00.9", "target"),
])
def test_classify_line_kinds(text, kind):
    assert co.classify_line(text).kind == kind


def test_classify_target_fields():
    line = co.classify_line("NGC 7331\t22:37:04.0506048\t34:24:56.80008")
    assert line.name == "NGC 7331"
    assert line.ra_text == "22:37:04.0506048"
    assert line.dec_text == "34:24:56.80008"
    assert line.coord.ra.deg == pytest.approx(339.2669, abs=1e-4)
    assert line.coord.dec.deg == pytest.approx(34.41578, abs=1e-4)
    assert line.problem is None


def test_classify_bad_ra_message():
    line = co.classify_line("M31\t0:43:444\t41:46:10")
    assert line.kind == "bad"
    assert "RA `0:43:444` is not a valid coordinate" in line.problem


def test_classify_bad_dec_message():
    line = co.classify_line("HD37387\t05:39:14.83\t23:19:24.1:")
    assert line.kind == "bad"
    assert "Dec `23:19:24.1:` is not a valid coordinate" in line.problem


@pytest.mark.parametrize("text", [
    "NGC 7331 22:37:04.05 34:24:56.8",       # all spaces
    "NGC 7331\t22:37:04.05 34:24:56.8",      # tab then space
    "ey-uma    09:02:20.8    +49:49:09.3",
])
def test_classify_spaces_instead_of_tabs(text):
    line = co.classify_line(text)
    assert line.kind == "bad"
    assert "tab" in line.problem.lower()


@pytest.mark.parametrize("text", [
    "chain NGC7790.txt",
    "NGC 7331\t22:37:04.05",
    "NGC 7331\t22:37:04.05\t34:24:56.8\textra",
    "\t22:37:04.05\t34:24:56.8",
])
def test_classify_other_garbage_is_bad(text):
    line = co.classify_line(text)
    assert line.kind == "bad"
    assert line.problem


@pytest.mark.parametrize("text", [
    "ey-uma\t09:02:20.8\t+49:49:09.3\t",
    "ey-uma\t09:02:20.8\t+49:49:09.3  ",
    "ey-uma\t09:02:20.8\t+49:49:09.3 \t ",
])
def test_trailing_whitespace_after_dec_is_accepted(text):
    line = co.classify_line(text)
    assert line.kind == "target", line.problem
    assert line.dec_text == "+49:49:09.3"


@pytest.mark.parametrize("dash", ["\u2212", "\u2013", "\u2014"])
def test_unicode_minus_in_dec_gets_its_own_message(dash):
    line = co.classify_line(f"x\t0:43:44\t{dash}0:32:00.9")
    assert line.kind == "bad"
    assert line.problem == (f"Dec `{dash}0:32:00.9` starts with a dash that is not the plain "
                            "minus sign. Delete it and type the `-` key instead.")


def test_unicode_minus_and_bad_ra_both_reported():
    line = co.classify_line("x\t0:43:444\t\u22120:32:00.9")
    assert line.problem.startswith("RA `0:43:444` is not a valid coordinate")
    assert "type the `-` key instead" in line.problem


def test_parse_plan_text_strips_cr_and_numbers_lines():
    text = "; c\r\n#autofocus\r\nNGC 7331\t22:37:04.05\t34:24:56.8\r\n"
    lines = co.parse_plan_text(text)
    assert [ln.number for ln in lines] == [1, 2, 3, 4]
    assert lines[2].kind == "target"
    assert lines[2].dec_text == "34:24:56.8"


def test_override_in_comment_block_above():
    text = ("; coords-ok: mosaic pane, offset on purpose\n"
            "; ACP plan for target M31\n"
            "M31\t0:43:44\t41:46:10\n")
    target = co.parse_plan_text(text)[2]
    assert target.override == "mosaic pane, offset on purpose"


def test_override_reaches_across_directives_and_blanks():
    # deliberate change: real plans put #directives between the comments and the target
    text = ("; coords-ok: header of the plan\n"
            "#autofocus\n"
            "M31\t0:43:44\t41:46:10\n"
            "#quitat 3:00\n"
            "; coords-ok: second\n"
            "\n"
            "#repeat 9\n"
            "M33\t1:33:51\t30:39:37\n")
    lines = co.parse_plan_text(text)
    assert lines[2].override == "header of the plan"
    assert lines[7].override == "second"
    assert co.stray_overrides(lines) == []


def test_override_applies_only_to_the_next_target():
    text = ("; coords-ok: for M31\n"
            "M31\t0:43:44\t41:46:10\n"
            "M33\t1:33:51\t30:39:37\n")
    lines = co.parse_plan_text(text)
    assert lines[1].override == "for M31"
    assert lines[2].override is None


@pytest.mark.parametrize("text, stray_line", [
    ("M31\t0:43:44\t41:46:10\n; coords-ok: after the last target\n#shutdown\n", 2),
    ("; coords-ok: mp line ends it\nMP 00018\nM31\t0:43:44\t41:46:10\n", 1),
    ("; coords-ok: garbage ends it\nchain NGC7790.txt\nM31\t0:43:44\t41:46:10\n", 1),
])
def test_override_reaching_no_target_is_stray(text, stray_line):
    lines = co.parse_plan_text(text)
    assert [ln.number for ln in co.stray_overrides(lines)] == [stray_line]
    assert all(ln.override is None for ln in lines)


def test_two_overrides_closest_wins():
    lines = co.parse_plan_text("; coords-ok: far\n; coords-ok: near\nM31\t0:43:44\t41:46:10\n")
    assert lines[2].override == "near"


def test_override_in_tapir_layout_applies():
    # the shape of 2026-11-15/fang_star.txt with an override at the top
    text = ("; coords-ok: from Tapir\n"
            "; ACP plan for target TIC_435934762.01\n"
            "; Transit is from 10/29/2026 05:55 to 10/29/2026 09:41 UTC.\n\n"
            "#autofocus\n#REPEAT 900\n#FILTER B,V,rp,ip\n"
            "; Exposure time roughly estimated based on target V = 14.10\n"
            "#INTERVAL 120,60,60,60\n#count 1,1,1,1\n"
            "Gaia-DR3-1016132190708213504\t09:06:48\t+50:00:16\n"
            "; Quit time is in UTC\n\n#quitat 12:00\n#chain calibration.txt\n")
    lines = co.parse_plan_text(text)
    (target,) = [ln for ln in lines if ln.kind == "target"]
    assert target.override == "from Tapir"


def test_stray_override_is_a_warning_in_file_format():
    findings, _ = co.check_file_format("2026-11-20/a.txt",
                                       "M31\t0:43:44\t41:46:10\n; coords-ok: late\n", "error")
    assert [(f.level, f.line, f.message) for f in findings] == [
        ("warning", 2, "This `; coords-ok:` line does not apply to any target. Put it on its own "
                       "line anywhere above the target line, after the previous target.")]


def test_override_with_empty_reason_is_empty_string():
    target = co.parse_plan_text(";coords-ok:   \nM31\t0:43:44\t41:46:10\n")[1]
    assert target.override == ""


def test_override_case_and_spacing_tolerant():
    target = co.parse_plan_text(";  Coords-OK :  from Tapir\nM31\t0:43:44\t41:46:10\n")[1]
    assert target.override == "from Tapir"


@pytest.mark.parametrize("filename, text, flat", [
    ("DFlat.txt", "; header\n10,ip,1\n10,rp,1\n", True),
    ("ScreenFlats.txt", "#screenflats DFlat.txt\n#shutdown\n", True),
    ("myflats.txt", "; c\n5,B,1\n\n5,V,2\n", True),
    ("plan.txt", "#autofocus\nM31\t0:43:44\t41:46:10\n", False),
])
def test_is_flat_config(filename, text, flat):
    assert co.is_flat_config(filename, text) is flat


# ---------------------------------------------------------------- routing

@pytest.mark.parametrize("name, route", [
    ("TIC_435934762.01", ("tic", "435934762")),
    ("TIC 282794970", ("tic", "282794970")),
    ("TIC_0282794970.01", ("tic", "282794970")),       # TIC_0123 is TIC 123
    ("tic-282794970", ("tic", "282794970")),
    ("Gaia-DR3-1016132190708213504", ("gaia", "1016132190708213504")),
    ("Gaia DR3 1016132190708213504", ("gaia", "1016132190708213504")),
    ("NGC 7331", ("sesame", None)),
    ("TICTAC", ("sesame", None)),
])
def test_route(name, route):
    assert co.route(name) == route


@pytest.mark.parametrize("name, expected", [
    ("ASASSN_26be", ["ASASSN_26be", "ASASSN-26be", "ASASSN 26be"]),
    ("kelt-16-b", ["kelt-16-b", "kelt-16 b", "kelt 16 b"]),
    ("NGC7331", ["NGC7331", "NGC 7331"]),
    ("ey-uma", ["ey-uma", "ey uma"]),
    ("v0512 cam", ["v0512 cam", "v 0512 cam", "V512 Cam"]),
    ("NGC 7331", ["NGC 7331"]),
])
def test_name_candidates_match_resolve_py(name, expected):
    assert co.name_candidates(name) == expected


# ---------------------------------------------------------------- catalog wrappers
# Each wrapper turns one library call into positions ([] = name not known) or
# an exception (the service failed). The library call is replaced by a fake
# that returns what the real one returns, built from real answers in
# tests/data (recorded on 2026-11-16), or raises the real exception types.

def fixture(name):
    return (DATA / name).read_text()


HTML_PAGE = "<!DOCTYPE html><html><body><h1>Service unavailable</h1></body></html>"


def http_response(body, status=200, url="https://example.invalid/"):
    """A real requests.Response holding body, as requests.get returns it."""
    response = requests.Response()
    response.status_code = status
    response._content = body.encode()
    response.encoding = "utf-8"
    response.url = url
    return response


class FakeGet:
    """Stands in for requests.get: records each call and returns or raises the given outcome."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.fixture
def fake_get(monkeypatch):
    def install(outcome):
        get = FakeGet(http_response(outcome) if isinstance(outcome, str) else outcome)
        monkeypatch.setattr(requests, "get", get)
        return get
    return install


def test_sesame_found_asks_one_mirror_politely(fake_get):
    get = fake_get(fixture("sesame_cds_ey_uma.xml"))
    (pos,) = co.sesame("CDS", "ey uma")
    assert pos.coord.ra.deg == pytest.approx(135.5865, abs=1e-3)
    assert pos.coord.dec.deg == pytest.approx(49.8192, abs=1e-3)
    assert pos.source == "SIMBAD via Sesame at CDS"
    (call,) = get.calls
    assert call["url"] == "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?ey%20uma"
    assert call["headers"] == {"User-Agent": "acp-plans-coordinate-check"}
    assert call["timeout"] == co.TIMEOUT + 5


def test_sesame_cfa_mirror_url(fake_get):
    get = fake_get(fixture("sesame_cfa_M39.xml"))
    (pos,) = co.sesame("CfA", "M39")
    assert get.calls[0]["url"] == "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?M39"
    assert pos.source == "SIMBAD via Sesame at CfA"


def test_sesame_mirrors_give_different_m39_centres():
    (cds,) = co.parse_sesame(fixture("sesame_cds_M39.xml"), "CDS")
    (cfa,) = co.parse_sesame(fixture("sesame_cfa_M39.xml"), "CfA")
    assert cds.coord.separation(cfa.coord).arcmin == pytest.approx(5.7, abs=0.1)


def test_sesame_nothing_found_is_not_found():
    assert co.parse_sesame(fixture("sesame_cds_Groth_notfound.xml"), "CDS") == []
    assert co.parse_sesame(fixture("sesame_cds_ey-uma_notfound.xml"), "CDS") == []


@pytest.mark.parametrize("body", [
    HTML_PAGE, "", "<Sesame><broken",
    # Sesame XML that neither gives a position nor says "Nothing found"
    "<?xml version='1.0'?><Sesame><Target><name>x</name><INFO>error</INFO></Target></Sesame>",
])
def test_sesame_answer_that_is_not_an_answer_is_service_error(body):
    with pytest.raises(co.ServiceError):
        co.parse_sesame(body, "CDS")


def test_sesame_ned_resolver_label():
    body = ("<?xml version='1.0'?><Sesame><Target><name>x</name>"
            "<Resolver name='N=NED'><jradeg>10.5</jradeg><jdedeg>-3.25</jdedeg></Resolver>"
            "</Target></Sesame>")
    (pos,) = co.parse_sesame(body, "CfA")
    assert (pos.coord.ra.deg, pos.coord.dec.deg) == (10.5, -3.25)
    assert pos.source == "NED via Sesame at CfA"


@pytest.mark.parametrize("outcome, error", [
    (http_response(HTML_PAGE, status=503), requests.HTTPError),
    (requests.ConnectionError("refused"), requests.ConnectionError),
    (requests.Timeout("slow"), requests.Timeout),
])
def test_sesame_transport_failure_raises(fake_get, outcome, error):
    fake_get(outcome)
    with pytest.raises(error):
        co.sesame("CDS", "M39")


def test_vsx_found(fake_get):
    get = fake_get(fixture("vsx_ASASSN-26be.json"))
    (pos,) = co.vsx("ASASSN-26be")
    assert pos.coord.ra.deg == pytest.approx(270.91367)
    assert pos.coord.dec.deg == pytest.approx(19.97956)
    assert pos.source == "AAVSO VSX"
    (call,) = get.calls
    assert call["url"] == "https://vsx.aavso.org/index.php"
    assert call["params"] == {"view": "api.object", "format": "json", "ident": "ASASSN-26be"}
    assert call["headers"] == {"User-Agent": "acp-plans-coordinate-check"}
    assert call["timeout"] == co.TIMEOUT + 5


def test_vsx_not_found(fake_get):
    fake_get(fixture("vsx_Groth_notfound.json"))
    assert co.vsx("Groth") == []


def test_vsx_html_page_raises_json_error(fake_get):
    fake_get(HTML_PAGE)
    with pytest.raises(requests.JSONDecodeError):
        co.vsx("Groth")


@pytest.mark.parametrize("body", ['{"VSXObject": {"Name": "x"}}', '{"other": 1}', "[]", '""'])
def test_vsx_unexpected_json_is_service_error(fake_get, body):
    fake_get(body)
    with pytest.raises(co.ServiceError):
        co.vsx("Groth")


def mast_table(answer):
    """The table astroquery's Catalogs.query_criteria returns for a recorded MAST answer."""
    from astroquery.mast.discovery_portal import _json_to_table
    return _json_to_table(answer)


MAST_ANSWER = json.loads(fixture("mast_tic_282794970.json"))
MAST_EMPTY = {**MAST_ANSWER, "data": []}


@pytest.fixture
def fake_mast(monkeypatch):
    from astroquery.mast import Catalogs
    calls = []

    def install(outcome):
        def query_criteria(**kwargs):
            calls.append(kwargs)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        monkeypatch.setattr(Catalogs, "query_criteria", query_criteria)
        return calls
    return install


def test_tic_found(fake_mast):
    calls = fake_mast(mast_table(MAST_ANSWER))
    (pos,) = co.tic("282794970")
    assert pos.coord.ra.deg == pytest.approx(199.484644701099)
    assert pos.coord.dec.deg == pytest.approx(16.4180442854519)
    assert pos.source == "TESS Input Catalog at MAST"
    assert calls == [{"catalog": "Tic", "ID": "282794970"}]


def test_tic_leading_zeros_are_dropped(fake_mast):
    calls = fake_mast(mast_table(MAST_ANSWER))
    assert len(co.tic("0282794970")) == 1
    assert calls[0]["ID"] == "282794970"


def test_tic_not_found_or_other_id(fake_mast):
    fake_mast(mast_table(MAST_EMPTY))
    assert co.tic("1") == []
    fake_mast(mast_table(MAST_ANSWER))
    assert co.tic("1") == []


@pytest.mark.parametrize("error", [
    "RemoteServiceError", "JSONDecodeError", "ConnectionError",
])
def test_tic_failures_raise(fake_mast, error):
    from astroquery.exceptions import RemoteServiceError
    errors = {"RemoteServiceError": RemoteServiceError("boom"),
              "JSONDecodeError": requests.JSONDecodeError("Expecting value", HTML_PAGE, 0),
              "ConnectionError": requests.ConnectionError("refused")}
    fake_mast(errors[error])
    with pytest.raises(type(errors[error])):
        co.tic("1")


def gaia_table(masked_epoch_2000=False):
    """The table a Gaia TAP job returns, built from a recorded answer."""
    answer = json.loads(fixture("gaia_dr3_1016132190708213504.json"))
    names = [col["name"] for col in answer["metadata"]]
    table = Table(rows=answer["data"], names=names)
    if masked_epoch_2000:
        for name in ("ra2000", "dec2000"):
            table[name] = MaskedColumn(table[name], mask=[True])
    return table


class FakeJob:
    def __init__(self, table):
        self.table = table

    def get_results(self):
        return self.table


@pytest.fixture
def fake_tap(monkeypatch):
    from astroquery.utils.tap.core import TapPlus
    calls = []

    def install(outcome):
        def launch_job(self, query, **kwargs):
            calls.append((self, query))
            if isinstance(outcome, BaseException):
                raise outcome
            return FakeJob(outcome)
        monkeypatch.setattr(TapPlus, "launch_job", launch_job)
        return calls
    return install


def test_gaia_uses_epoch_2000_position(fake_tap):
    calls = fake_tap(gaia_table())
    (pos,) = co.gaia("1016132190708213504")
    assert pos.coord.ra.deg == pytest.approx(136.70305703091927)
    assert pos.coord.dec.deg == pytest.approx(50.004432351457204)
    assert pos.source == "Gaia DR3 (epoch 2000)"
    ((_, query),) = calls
    assert "EPOCH_PROP_POS" in query and "source_id=1016132190708213504" in query


def test_gaia_without_epoch_2000_position_uses_2016(fake_tap):
    fake_tap(gaia_table(masked_epoch_2000=True))
    (pos,) = co.gaia("1016132190708213504")
    assert pos.source == "Gaia DR3 (epoch 2016)"


def test_gaia_no_row_is_not_found(fake_tap):
    fake_tap(gaia_table()[:0])
    assert co.gaia("1") == []


def test_gaia_failure_raises(fake_tap):
    fake_tap(requests.HTTPError("Error 500: Internal Server Error"))
    with pytest.raises(requests.HTTPError):
        co.gaia("1")


# ---------------------------------------------------------------- catalogs: memo, outages, deadline

def test_catalogs_memoises_answers_within_a_run():
    calls = []
    cats = co.Catalogs(lambda service, query: calls.append(query) or [])
    assert cats.ask("MAST", "1") == [] and cats.ask("MAST", "1") == []
    assert calls == ["1"]


@pytest.mark.parametrize("error", [
    requests.ConnectionError("refused"), requests.Timeout("slow"),
    requests.HTTPError("503 Server Error"), TimeoutError("slow"), OSError("no route"),
])
def test_transport_failure_marks_the_service_down_for_the_run(error):
    calls = []

    def lookup(service, query):
        calls.append(query)
        raise error
    cats = co.Catalogs(lookup)
    for query in ("a", "b"):
        with pytest.raises(co.ServiceError, match=f"^Sesame at CDS: {type(error).__name__}: "):
            cats.ask("Sesame at CDS", query)
    assert calls == ["a"]


@pytest.mark.parametrize("error", [
    co.ServiceError("answer was not a Sesame XML document"),
    requests.JSONDecodeError("Expecting value", HTML_PAGE, 0),
    ValueError("bad table"),
    KeyError("ra"),
])
def test_malformed_answer_fails_only_its_request(error):
    calls = []

    def lookup(service, query):
        calls.append(query)
        raise error
    cats = co.Catalogs(lookup)
    for query in ("a", "b", "a"):
        with pytest.raises(co.ServiceError):
            cats.ask("MAST", query)
    assert calls == ["a", "b"]           # remembered for "a", asked again for "b"


def test_remote_service_error_fails_only_its_request():
    from astroquery.exceptions import RemoteServiceError
    calls = []

    def lookup(service, query):
        calls.append(query)
        raise RemoteServiceError("bad request")
    cats = co.Catalogs(lookup)
    for query in ("a", "b"):
        with pytest.raises(co.ServiceError, match="^MAST: RemoteServiceError: bad request$"):
            cats.ask("MAST", query)
    assert calls == ["a", "b"]


def test_lookups_are_spaced_to_be_polite(monkeypatch):
    monkeypatch.setattr(co, "MIN_INTERVAL", 0.3)
    times = []
    cats = co.Catalogs(lambda service, query: times.append(time.monotonic()) or [])
    cats.ask("MAST", "a")
    cats.ask("Gaia archive", "b")
    cats.ask("MAST", "a")                # remembered: no lookup, no pause
    assert len(times) == 2 and times[1] - times[0] >= 0.29


def test_a_lookup_that_hangs_is_a_timeout_and_the_service_is_down():
    calls = []

    def lookup(service, query):
        calls.append(query)
        time.sleep(5)
    cats = co.Catalogs(lookup, timeout=0.1)
    start = time.monotonic()
    with pytest.raises(co.ServiceError, match="^MAST: TimeoutError: no answer within 0.1 s$"):
        cats.ask("MAST", "a")
    with pytest.raises(co.ServiceError):
        cats.ask("MAST", "b")
    assert time.monotonic() - start < 2
    assert calls == ["a"]


def test_default_lookups_route_to_the_wrappers(monkeypatch):
    seen = []
    for name in ("sesame", "vsx", "tic", "gaia"):
        monkeypatch.setattr(co, name, lambda *args, name=name: seen.append((name, *args)) or [])
    for service in co.TOLERANCES:
        co.lookup(service, "x")
    assert seen == [("sesame", "CDS", "x"), ("sesame", "CfA", "x"), ("vsx", "x"),
                    ("tic", "x"), ("gaia", "x")]


def test_tolerances():
    assert co.TOLERANCES == {"Sesame at CDS": 2 * u.arcmin, "Sesame at CfA": 2 * u.arcmin,
                             "AAVSO VSX": 20 * u.arcsec, "MAST": 20 * u.arcsec,
                             "Gaia archive": 20 * u.arcsec}


# ---------------------------------------------------------------- verdicts (plain fakes)

CDS, CFA, VSX, MAST, GAIA = "Sesame at CDS", "Sesame at CfA", "AAVSO VSX", "MAST", "Gaia archive"
NOT_FOUND = []

EY_UMA_ANSWER = co.parse_sesame(fixture("sesame_cds_ey_uma.xml"), "CDS")
EY_UMA_CFA_ANSWER = co.parse_sesame(fixture("sesame_cds_ey_uma.xml"), "CfA")
M39_CDS = co.parse_sesame(fixture("sesame_cds_M39.xml"), "CDS")
M39_CFA = co.parse_sesame(fixture("sesame_cfa_M39.xml"), "CfA")
ASASSN_26BE = [co.Position(sky(270.91367, 19.97956), "AAVSO VSX")]
TIC_POS = (199.484644701099, 16.4180442854519)
TIC_ANSWER = [co.Position(sky(*TIC_POS), "TESS Input Catalog at MAST")]
GAIA_ANSWER = [co.Position(sky(136.70305703091927, 50.004432351457204), "Gaia DR3 (epoch 2000)")]


class FakeLookup:
    """Stands in for the catalog services: maps (service, query) to positions or an exception.

    A question with no answer (and no explicit default) is recorded in
    `unexpected`; the autouse fixture below fails the test if any were asked.
    Tests of an outage pass default=DOWN explicitly.
    """

    instances = []

    def __init__(self, answers=None, default=None):
        self.answers = dict(answers or {})
        self.calls = []
        self.unexpected = []
        self.default = default
        FakeLookup.instances.append(self)

    def add(self, service, query, answer):
        self.answers[(service, query)] = answer

    def __call__(self, service, query):
        self.calls.append((service, query))
        answer = self.answers.get((service, query), self.default)
        if answer is None:
            self.unexpected.append((service, query))
            raise AssertionError(f"unexpected lookup {service} {query}")
        if isinstance(answer, BaseException):
            raise answer
        return answer


DOWN = requests.ConnectionError("connection refused")   # an explicit transport failure


@pytest.fixture(autouse=True)
def no_unexpected_lookups():
    FakeLookup.instances.clear()
    yield
    unexpected = [call for f in FakeLookup.instances for call in f.unexpected]
    FakeLookup.instances.clear()
    assert unexpected == [], "the code asked catalogs the test did not expect"


def test_fake_lookup_records_unexpected_questions():
    # Catalogs turns every lookup exception into a service failure, so an
    # exception alone would not make a test fail; the record does.
    lookup = FakeLookup()
    check("Groth", sky(214.8, 52.8), lookup)
    assert lookup.unexpected
    lookup.unexpected.clear()


def check(name, coord, lookup):
    return co.check_target(name, coord, co.Catalogs(lookup))


def offset(coord, arcsec):
    """Shift a position north by arcsec."""
    return sky(coord.ra.deg, coord.dec.deg + arcsec / 3600)


TIC_COORD = sky(*TIC_POS)


def test_tic_confirmed_with_one_lookup():
    lookup = FakeLookup({(MAST, "282794970"): TIC_ANSWER})
    v = check("TIC_282794970.01", offset(TIC_COORD, 8.5), lookup)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "TESS Input Catalog at MAST"
    assert len(lookup.calls) == 1


def test_tic_with_leading_zeros_is_confirmed():
    lookup = FakeLookup({(MAST, "282794970"): TIC_ANSWER})
    assert check("TIC_0282794970.01", TIC_COORD, lookup).status == "confirmed"


def test_tic_off_by_30_arcsec_is_mismatch():
    lookup = FakeLookup({(MAST, "282794970"): TIC_ANSWER})
    v = check("TIC_282794970.01", offset(TIC_COORD, 30), lookup)
    assert v.status == "mismatch"
    assert v.nearest_sep.arcsec == pytest.approx(30, abs=0.1)


def test_separation_wraps_at_ra_zero():
    lookup = FakeLookup({(MAST, "1"): [co.Position(sky(0.01, 0.0), "TESS Input Catalog at MAST")]})
    v = check("TIC_1", sky(359.99, 0.0), lookup)
    assert v.status == "mismatch"
    assert v.nearest_sep.arcsec == pytest.approx(72, rel=1e-6)


def test_tic_not_in_catalog():
    lookup = FakeLookup({(MAST, "1"): NOT_FOUND})
    assert check("TIC_1", sky(10, 10), lookup).status == "notfound"


def test_gaia_confirmed():
    lookup = FakeLookup({(GAIA, "1016132190708213504"): GAIA_ANSWER})
    # the real plan line: 09:06:48 +50:00:16 (whole seconds; ~8 arcsec off)
    v = check("Gaia-DR3-1016132190708213504", plan_coord("09:06:48", "+50:00:16"), lookup)
    assert v.status == "confirmed"


EY_UMA = plan_coord("09:02:20.8", "+49:49:09.3")


def test_sesame_tries_normalisations_and_stops_when_confirmed():
    lookup = FakeLookup({
        (CDS, "ey-uma"): NOT_FOUND, (CFA, "ey-uma"): NOT_FOUND,
        (CDS, "ey uma"): EY_UMA_ANSWER,
    })
    v = check("ey-uma", EY_UMA, lookup)
    assert v.status == "confirmed"
    assert len(lookup.calls) == 3          # CfA "ey uma" and VSX never asked


M39_PLAN = plan_coord("21:31:33.4", "+48:14:49")   # CfA centre


def test_sesame_cds_disagreeing_is_not_final_cfa_confirms():
    lookup = FakeLookup({(CDS, "M39"): M39_CDS, (CFA, "M39"): M39_CFA})
    v = check("M39", M39_PLAN, lookup)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "SIMBAD via Sesame at CfA"


def test_sesame_mismatch_asks_vsx_and_reports_nearest():
    lookup = FakeLookup({(CDS, "M39"): M39_CDS, (CFA, "M39"): M39_CFA, (VSX, "M 39"): NOT_FOUND})
    v = check("M39", offset(M39_PLAN, 300), lookup)   # 5 arcmin north of the CfA centre
    assert v.status == "mismatch"
    assert v.nearest.source == "SIMBAD via Sesame at CfA"
    assert v.nearest_sep.arcsec == pytest.approx(300, abs=1)
    assert (VSX, "M 39") in lookup.calls
    # once a spelling is found, other spellings are not tried in Sesame
    assert (CDS, "M 39") not in lookup.calls


def test_vsx_rescues_name_sesame_does_not_know():
    lookup = FakeLookup({(mirror, name): NOT_FOUND for mirror in (CDS, CFA)
                         for name in ("ASASSN_26be", "ASASSN-26be", "ASASSN 26be")})
    lookup.add(VSX, "ASASSN-26be", ASASSN_26BE)
    v = check("ASASSN_26be", plan_coord("18:03:39.29", "+19:58:46.3"), lookup)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "AAVSO VSX"


def test_not_found_anywhere():
    lookup = FakeLookup({(CDS, "Groth"): NOT_FOUND, (CFA, "Groth"): NOT_FOUND,
                         (VSX, "Groth"): NOT_FOUND})
    v = check("Groth", sky(214.8, 52.8), lookup)
    assert v.status == "notfound"
    assert v.failures == []


def test_failed_service_with_no_answer_elsewhere_is_unreachable_not_not_found():
    lookup = FakeLookup({(CDS, "Groth"): co.ServiceError("answer was not a Sesame XML document"),
                         (CFA, "Groth"): NOT_FOUND, (VSX, "Groth"): NOT_FOUND})
    v = check("Groth", sky(214.8, 52.8), lookup)
    assert v.status == "unreachable"
    assert v.failures == ["Sesame at CDS: answer was not a Sesame XML document"]


def test_one_mirror_down_other_confirms():
    lookup = FakeLookup({(CDS, "M39"): requests.Timeout("timed out"), (CFA, "M39"): M39_CFA})
    assert check("M39", M39_PLAN, lookup).status == "confirmed"


def test_mismatch_with_a_failed_source_is_unreachable_but_keeps_nearest():
    lookup = FakeLookup({(CDS, "M39"): M39_CDS, (CFA, "M39"): requests.Timeout("timed out"),
                         (VSX, "M 39"): NOT_FOUND})
    v = check("M39", M39_PLAN, lookup)
    assert v.status == "unreachable"
    assert v.nearest.source == "SIMBAD via Sesame at CDS"


def test_each_distinct_question_is_asked_once_per_run():
    lookup = FakeLookup({(MAST, "282794970"): TIC_ANSWER})
    cats = co.Catalogs(lookup)
    co.check_target("TIC_282794970.01", TIC_COORD, cats)
    co.check_target("TIC_282794970.02", offset(TIC_COORD, 60), cats)
    assert len(lookup.calls) == 1


def test_failures_are_remembered_too():
    lookup = FakeLookup({(MAST, "1"): co.ServiceError("bad answer")})
    cats = co.Catalogs(lookup)
    co.check_target("TIC_1", sky(1, 1), cats)
    co.check_target("TIC_1", sky(1, 1), cats)
    assert len(lookup.calls) == 1


def test_failed_service_is_not_asked_again_in_the_run():
    # an outage costs one timeout, not one per name; the run relies on the other sources
    lookup = FakeLookup({
        (CDS, "ey-uma"): requests.Timeout("timed out"),
        (CFA, "ey-uma"): NOT_FOUND,
        (CFA, "ey uma"): EY_UMA_CFA_ANSWER,
    })
    v = check("ey-uma", EY_UMA, lookup)
    assert v.status == "confirmed"
    assert (CDS, "ey uma") not in lookup.calls
    assert len(lookup.calls) == 3


def test_malformed_answer_fails_only_that_question():
    # an HTML page for one name says nothing about the service's other answers
    lookup = FakeLookup({
        (CDS, "ey-uma"): co.ServiceError("answer was not a Sesame XML document"),
        (CFA, "ey-uma"): NOT_FOUND,
        (CDS, "ey uma"): EY_UMA_ANSWER,
    })
    v = check("ey-uma", EY_UMA, lookup)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "SIMBAD via Sesame at CDS"


def test_unreachable_failures_are_unique_and_keep_the_original_error():
    lookup = FakeLookup({
        (CDS, "ey-uma"): TimeoutError("timed out"),
        (CFA, "ey-uma"): NOT_FOUND, (CFA, "ey uma"): NOT_FOUND, (VSX, "ey uma"): NOT_FOUND,
    })
    v = check("ey-uma", EY_UMA, lookup)
    assert v.status == "unreachable"
    assert v.failures == ["Sesame at CDS: TimeoutError: timed out"]


# ---------------------------------------------------------------- changed paths

from datetime import date  # noqa: E402


@pytest.mark.parametrize("name, expected", [
    ("2026-11-15", date(2026, 11, 15)),
    ("2023-05-20-day-calib", date(2023, 5, 20)),
    ("2026-10-19 copy", date(2026, 10, 19)),
    ("2026-13-01", None),
    ("2026-1-15", None),
    ("plans", None),
    ("", None),
])
def test_folder_date(name, expected):
    assert co.folder_date(name) == expected


def test_strict_cutoff():
    assert co.is_strict(co.STRICT_FROM)
    assert not co.is_strict(date(2026, 11, 15))


def test_classify_changed_paths():
    paths = [
        "2026-11-20/1-a.txt", "2026-11-20/b.txt", "2026-11-20/.DS_Store",
        "2026-11-21/notes.md",
        "ey_uma.txt",                    # loose plan at the root
        "LICENSE", "check_objects.py", ".gitignore",
        "tests/data/x.txt", ".github/workflows/check_plans.yml",
        "plans/1-a.txt", "plans/b.txt",  # undated folder, reported once
        "2026-11-20/sub/c.txt",          # nested plan file
        "2026-11-20/D.TXT",              # upper-case extension
        "2026-11-20/sub/E.Txt",
        "LOOSE.TXT",
    ]
    changes = co.classify_changed_paths(paths)
    assert changes.folders == {"2026-11-20", "2026-11-21"}
    assert changes.plan_files == {"2026-11-20/1-a.txt", "2026-11-20/b.txt", "2026-11-20/D.TXT"}
    # nested files are reported by main, which knows whether the folder is strict
    assert changes.nested_files == {"2026-11-20/sub/c.txt", "2026-11-20/sub/E.Txt"}
    msgs = [(f.level, f.path) for f in changes.findings]
    assert ("error", "ey_uma.txt") in msgs
    assert ("error", "LOOSE.TXT") in msgs
    assert ("error", "plans") in msgs
    assert len(msgs) == 3
    root = next(f for f in changes.findings if f.path == "ey_uma.txt")
    assert "move these into a dated folder" in root.message


# ---------------------------------------------------------------- per-file and per-line checks

def test_file_format_findings_levels_and_flat_exemption():
    text = "#autofocus\nM31\t0:43:444\t41:46:10\nNGC 1 1:00:00 +1:00:00\nMP 00018\n"
    findings, targets = co.check_file_format("2026-11-20/a.txt", text, "error")
    assert [(f.level, f.line) for f in findings] == [("error", 2), ("error", 3)]
    assert [t.kind for t in targets] == ["mp"]
    findings, _ = co.check_file_format("2025-10-10/a.txt", text, "warning")
    assert {f.level for f in findings} == {"warning"}
    assert co.check_file_format("2026-11-20/DFlat.txt", "10,ip,1\n", "error") == ([], [])


def test_file_format_returns_targets_and_mp_lines():
    text = "MP 00018\nM31\t0:43:44\t41:46:10\n"
    findings, targets = co.check_file_format("2026-11-20/a.txt", text, "error")
    assert findings == []
    assert [t.kind for t in targets] == ["mp", "target"]


def entry_files(*items):
    """(path, text) pairs for the verified index."""
    return list(items)


def test_verified_index_requires_strict_earlier_same_name_and_close():
    files = entry_files(
        ("2026-11-17/a.txt", "ey-uma\t09:02:20.8\t+49:49:09.3\n"),
        ("2026-11-10/b.txt", "M33\t1:33:51\t30:39:37\n"),            # not strict
        ("2026-11-17/c.txt", "; coords-ok: mosaic\nM31\t0:43:44\t41:46:10\n"),
        ("2026-11-17/DFlat.txt", "10,ip,1\n"),
        ("tests/x.txt", "M57\t18:53:35\t33:01:45\n"),
    )
    index = co.VerifiedIndex.from_files(files)
    later = date(2026, 11, 18)
    hit = index.find("EY UMa", offset(EY_UMA, 10), later)
    assert hit is not None and hit.path == "2026-11-17/a.txt" and hit.line == 1
    assert index.find("ey uma", offset(EY_UMA, 30), later) is None      # too far
    assert index.find("other", EY_UMA, later) is None                    # name differs
    assert index.find("ey-uma", EY_UMA, date(2026, 11, 17)) is None      # not earlier
    assert index.find("M33", plan_coord("1:33:51", "30:39:37"), later) is None
    m31 = index.find("M31", plan_coord("0:43:44", "41:46:10"), later)
    assert m31.override == "mosaic"


def line_of(text):
    return [ln for ln in co.parse_plan_text(text) if ln.kind == "target"][0]


EMPTY_INDEX = co.VerifiedIndex([])
NIGHT = date(2026, 11, 20)


def run_line(text, lookup, index=EMPTY_INDEX, offline_ok=False):
    return co.check_line("2026-11-20/x.txt", line_of(text), NIGHT, index,
                         co.Catalogs(lookup), offline_ok)


def test_line_with_override_passes_without_lookup_and_is_listed():
    lookup = FakeLookup()
    (f,) = run_line("; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n", lookup)
    assert f.level == "override"
    assert "mosaic pane" in f.message and f.line == 2
    assert lookup.calls == []


def test_line_with_empty_override_is_error():
    (f,) = run_line("; coords-ok:\nM31\t0:43:44\t41:46:10\n", FakeLookup())
    assert f.level == "error"
    assert "reason" in f.message


def test_line_verified_by_earlier_plan():
    index = co.VerifiedIndex.from_files([("2026-11-17/a.txt", "ey-uma\t09:02:20.8\t+49:49:09.3\n")])
    lookup = FakeLookup()
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:49:09.3\n", lookup, index)
    assert f.level == "notice"
    assert "2026-11-17/a.txt" in f.message
    assert lookup.calls == []


def test_line_verified_by_earlier_plan_with_override_is_flagged():
    index = co.VerifiedIndex.from_files(
        [("2026-11-17/a.txt", "; coords-ok: mosaic\nM31\t0:43:44\t41:46:10\n")])
    (f,) = run_line("M31\t0:43:44\t41:46:10\n", FakeLookup(), index)
    assert f.level == "override"
    assert "verified by earlier plan with override" in f.message
    assert "mosaic" in f.message


def test_line_mismatch_message_for_students():
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:52:21\n", wrong_ey_lookup())   # 3.2 arcmin north
    assert f.level == "error"
    assert f.message.startswith("`ey-uma` is 3.2 arcmin from the catalog position "
                                "09:02:20.76 +49:49:09.2 (SIMBAD via Sesame at CDS).")
    assert f.message.endswith("Fix the coordinates, or if the offset is deliberate add "
                              "`; coords-ok: <reason>` on its own line anywhere above the target "
                              "line, after the previous target.")


def test_line_confirmed_is_notice():
    lookup = FakeLookup({(CDS, "ey-uma"): NOT_FOUND, (CFA, "ey-uma"): NOT_FOUND,
                         (CDS, "ey uma"): EY_UMA_ANSWER})
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:49:09.3\n", lookup)
    assert f.level == "notice"
    assert "SIMBAD via Sesame at CDS" in f.message


def test_line_not_found_message():
    lookup = FakeLookup({(CDS, "Groth"): NOT_FOUND, (CFA, "Groth"): NOT_FOUND,
                         (VSX, "Groth"): NOT_FOUND})
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", lookup)
    assert f.level == "error"
    assert f.message.startswith("`Groth` was not found in any catalog.")
    assert f.message.endswith("if it is right, add `; coords-ok: <where the coordinates came "
                              "from>` on its own line anywhere above the target line, after the "
                              "previous target.")


def test_line_unreachable_error_or_warning_offline():
    responses = {(CDS, "Groth"): requests.Timeout("timed out"),
                 (CFA, "Groth"): co.ServiceError("answer was not a Sesame XML document"),
                 (VSX, "Groth"): requests.ConnectionError("no route")}
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", FakeLookup(responses))
    assert f.level == "error"
    assert f.message.startswith("Could not reach the catalog service")
    assert "Re-run jobs" in f.message
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", FakeLookup(responses), offline_ok=True)
    assert f.level == "warning"


def test_mp_line_is_notice():
    line = co.parse_plan_text("MP 00018\n")[0]
    (f,) = co.check_line("2026-11-20/x.txt", line, NIGHT, EMPTY_INDEX, co.Catalogs(FakeLookup()), False)
    assert f.level == "notice" and "minor planet" in f.message.lower()


# ---------------------------------------------------------------- reporting

FINDINGS = [
    co.Finding("notice", "2026-11-20/a.txt", 3, "`x` matches AAVSO VSX."),
    co.Finding("error", "2026-11-20/b.txt", 7, "RA `0:43:444` is not a valid coordinate."),
    co.Finding("override", "2026-11-20/a.txt", 9, "`M31`: accepted, 50%, a:b"),
    co.Finding("warning", "2023-01-01/c.txt", 2, "old problem"),
    co.Finding("error", "plans", 0, "Folder `plans` does not start with a date."),
]


def test_text_report_lists_everything_errors_first():
    text = co.format_text_report(FINDINGS)
    lines = text.splitlines()
    assert lines[0].startswith("ERROR")
    assert "plans: Folder" in text
    assert "2026-11-20/b.txt:7: RA `0:43:444`" in text
    assert "OVERRIDE" in text and "NOTICE" in text and "WARNING" in text
    assert "2 errors, 1 warning, 1 override" in text


def test_markdown_summary_is_complete():
    md = co.format_markdown(FINDINGS)
    for f in FINDINGS:
        assert f.message in md
    assert "`2026-11-20/b.txt` line 7" in md
    assert md.index("Errors") < md.index("Warnings") < md.index("Overrides") < md.index("Notices")


EVIL_PATH = "2026-11-20/x\n::stop-commands::tok\r::warning ::fake.txt"
EVIL = [
    co.Finding("error", EVIL_PATH, 3, "`ey\n::error ::forged` is 3.2 arcmin off."),
    co.Finding("warning", "2026-11-20/a\x1b[2Jb\x0b\x85 .txt", 0, "ok"),
]


def test_text_report_escapes_control_characters():
    text = co.format_text_report(EVIL)
    lines = text.split("\n")
    assert len(lines) == 3                 # two findings and the count line
    assert lines[0] == ("ERROR   2026-11-20/x\\n::stop-commands::tok\\r::warning ::fake.txt:3: "
                        "`ey\\n::error ::forged` is 3.2 arcmin off.")
    assert lines[1] == "WARNING 2026-11-20/a\\x1b[2Jb\\x0b\\x85\\u2028.txt: ok"
    for ch in "\r\x0b\x0c\x1b\x85  ":
        assert ch not in text


def test_visible_escapes():
    assert co.visible("a\tb\nc\rd\x00e\x7f") == "a\\tb\\nc\\rd\\x00e\\x7f"
    assert co.visible("NGC 7331 – ü") == "NGC 7331 – ü"


def md_lines_for(findings):
    return co.format_markdown(findings).split("\n")


def test_markdown_cannot_forge_headings_links_or_break_code_spans():
    findings = [
        co.Finding("error", "2026-11-20/a`b.txt", 3,
                   "`x` ![img](https://e.example/i.png) [link](https://e.example)` is off."),
        co.Finding("error", "2026-11-20/c.txt\n# Forged heading\n- forged item", 0,
                   "`a``b`\n## forged"),
        co.Finding("warning", "2026-11-20/<img src=x>.txt", 0, "<b>bold</b> & *star* _u_ | |"),
    ]
    md = co.format_markdown(findings)
    lines = md.split("\n")
    assert not any(line.startswith("#") and "orged" in line for line in lines)
    assert not any(line.startswith("- forged") for line in lines)
    assert "![" not in md.replace("\\!\\[", "")
    assert "](" not in md.replace("\\]\\(", "")
    # HTML is escaped in text and literal inside a code span
    assert "<b>" not in md and "\\<b\\>bold\\</b\\>" in md
    assert md.count("<img") == 1 and "`2026-11-20/<img src=x>.txt`" in md
    # every finding is one list item
    items = [line for line in lines if line.startswith("- ")]
    assert len(items) == 3
    # a backtick in a path is shown inside a longer code span, not breaking out of it
    assert "``2026-11-20/a`b.txt``" in md


def test_markdown_code_spans_still_render_for_normal_messages():
    md = co.format_markdown([co.Finding("error", "2026-11-20/b.txt", 7,
                                        "RA `0:43:444` is not a valid coordinate.")])
    assert "- `2026-11-20/b.txt` line 7: RA `0:43:444` is not a valid coordinate." in md


def test_e2e_file_name_cannot_inject_workflow_commands(repo, capsys, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    plans = night(EY_LINE, GAIA_LINE)
    plans["x\n::stop-commands::tok\n# Forged.txt"] = "M31 0:43:44 41:46:10\n"
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1
    # the runner treats a line as a command only if it starts with "::" (after whitespace)
    for line in out.split("\n"):
        line = line.lstrip()
        assert not line.startswith("::") or line.startswith(("::error file=", "::warning file=",
                                                              "::notice file="))
    assert "x\\n::stop-commands::tok\\n# Forged.txt" in out
    # annotations keep the real path in file= (escaped) and show the message visibly
    assert ("::error file=2026-11-20::Chaining: Plan 2026-11-20/x\\n::stop-commands::tok"
            "\\n# Forged.txt is not used.") in out.split("\n")
    assert not any(line.startswith("# Forged") for line in summary.read_text().split("\n"))


NON_UTF8 = b"caf\xe9.txt".decode("utf-8", "surrogateescape")   # as git paths arrive


def test_reports_show_non_utf8_bytes_visibly():
    findings = [co.Finding("error", f"2026-11-20/{NON_UTF8}", 2, f"`{NON_UTF8}` is odd.")]
    text = co.format_text_report(findings)
    assert "2026-11-20/caf\\xe9.txt:2: `caf\\xe9.txt` is odd." in text
    text.encode("utf-8")                                  # no lone surrogates left
    co.format_markdown(findings).encode("utf-8")
    for line in co.format_annotations(findings):
        line.encode("utf-8")


def test_e2e_non_utf8_file_name_from_git_is_reported(repo, capsys, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(co, "changed_paths", lambda *a: ([NON_UTF8], [NON_UTF8]))
    code, out = run_main(capsys)
    assert code == 1
    assert "ERROR   caf\\xe9.txt: `caf\\xe9.txt` is a plan file at the top" in out
    assert "- `caf\\xe9.txt`: `caf\\xe9.txt` is a plan file" in summary.read_text(encoding="utf-8")


def test_e2e_non_utf8_file_name_in_folder(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    try:
        (repo / "2026-11-20").joinpath(NON_UTF8).write_text("M31 0:43:44 41:46:10\n")
    except OSError:
        pytest.skip("this filesystem only allows UTF-8 file names (macOS)")
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1
    assert "ERROR   2026-11-20/caf\\xe9.txt:1:" in out


def test_e2e_directory_named_like_a_plan_is_reported(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    write(repo, "2026-11-20/x.txt/notes.md", "hi\n")
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1
    assert ("ERROR   2026-11-20/x.txt: `2026-11-20/x.txt` is not a regular file. "
            "Plan files must be regular files.") in out
    assert "Traceback" not in out


SYMLINK_MESSAGE = "is a symbolic link. Plan files must be regular files, not links."


@pytest.mark.parametrize("target", ["/etc/hosts", "nowhere.txt", "b.txt"])
def test_e2e_symlinked_plan_is_error_and_not_followed(repo, capsys, target):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    (repo / "2026-11-20/c.txt").symlink_to(target)
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1, out
    assert f"ERROR   2026-11-20/c.txt: `2026-11-20/c.txt` {SYMLINK_MESSAGE}" in out
    assert not re.search(r"2026-11-20/c\.txt:\d", out)      # its content was not read
    assert "Traceback" not in out


def test_e2e_symlink_in_old_folder_warns(repo, capsys):
    (repo / "2025-10-10/c.txt").symlink_to("b.txt")
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0, out
    assert f"WARNING 2025-10-10/c.txt: `2025-10-10/c.txt` {SYMLINK_MESSAGE}" in out


def test_e2e_type_change_from_symlink_to_file_is_checked(repo, capsys):
    (repo / "2026-11-17/c.txt").symlink_to("b.txt")
    commit(repo, "a link on main")
    git(repo, "branch", "-f", "main")
    (repo / "2026-11-17/c.txt").unlink()
    write(repo, "2026-11-17/c.txt", "M31\t0:43:444\t41:46:10\n")
    git(repo, "add", "-A")
    commit(repo, "replace the link by a file")
    code, out = run_main(capsys)
    assert code == 1, out
    assert "ERROR   2026-11-17/c.txt:1: RA `0:43:444`" in out


def test_e2e_symlink_in_verified_ref_is_not_used(repo, capsys):
    (repo / "2026-11-17/c.txt").symlink_to(EY_LINE.strip())    # link target text is a plan line
    commit(repo, "a link on main")
    git(repo, "branch", "-f", "main")
    for name, text in night(EY_LINE, "; coords-ok: x\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = ey_and_gaia_lookup()
    code, out = run_main(capsys, lookup=lookup)
    assert "verified by earlier plan (2026-11-17/c.txt" not in out
    assert "`ey-uma` matches SIMBAD" in out


def test_e2e_override_above_directives_applies(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["b.txt"] = ("; coords-ok: mosaic pane 2\n; ACP plan\n\n#autofocus\n#repeat 9\n"
                      "M31\t0:50:00\t41:46:10\n#shutdown\n")
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = ey_and_gaia_lookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert "OVERRIDE2026-11-20/b.txt:6: `M31`: coordinates accepted without a catalog check" in out


def test_e2e_stray_override_warns_and_does_not_cover_a_later_target(repo, capsys):
    plans = night(WRONG_EY, GAIA_LINE)
    plans["1-a.txt"] = "; coords-ok: offset\nMP 00018\n" + plans["1-a.txt"]
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=wrong_ey_lookup())
    assert code == 1
    assert "WARNING 2026-11-20/1-a.txt:1: This `; coords-ok:` line does not apply" in out
    assert "ERROR   2026-11-20/1-a.txt:5: `ey-uma` is 3.2 arcmin" in out


def test_e2e_unexpected_exception_is_one_readable_error(repo, capsys, monkeypatch):
    def boom(*args):
        raise KeyError("surprise")
    monkeypatch.setattr(co, "classify_changed_paths", boom)
    code, out = run_main(capsys)
    assert code == 1
    assert out.strip() == ("ERROR   The plan check stopped because of an unexpected problem "
                           "(KeyError: 'surprise'). Use Re-run jobs; if it keeps failing ask an admin.")


def test_annotations_escape_and_skip_notices():
    out = co.format_annotations(FINDINGS)
    assert "::error file=2026-11-20/b.txt,line=7::RA `0:43:444` is not a valid coordinate." in out
    assert "::error file=plans::Folder `plans` does not start with a date." in out
    assert "::warning file=2023-01-01/c.txt,line=2::old problem" in out
    assert "::notice file=2026-11-20/a.txt,line=9::`M31`: accepted, 50%25, a:b" in out
    assert not any("matches AAVSO VSX" in line for line in out)


# ---------------------------------------------------------------- end to end (throwaway git repo)

import subprocess  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(repo, *args):
    import os
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           *args], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout


def write(repo, path, text):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode())


def commit(repo, message="c"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


EY_LINE = "ey-uma\t09:02:20.8\t+49:49:09.3\n"
GAIA_LINE = "Gaia-DR3-1016132190708213504\t09:06:48\t+50:00:16\n"
NGC_LINE = "NGC 7331\t22:37:04.0506048\t34:24:56.80008\n"


def night(first_target, second_target):
    """A valid two-plan night: 1-a.txt chains to b.txt, which shuts down."""
    return {
        "1-a.txt": "; first\r\n#autofocus\r\n" + first_target.replace("\n", "\r\n") + "#chain b.txt\r\n",
        "b.txt": "#autofocus\n" + second_target + "#shutdown\n",
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for var in ("GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    r = tmp_path / "plans repo"
    r.mkdir()
    git(r, "-c", "init.defaultBranch=main", "init", "-q")
    for name, text in night(NGC_LINE, "M31\t0:43:444\t41:46:10\n").items():
        write(r, f"2025-10-10/{name}", text)                 # old folder with the real typo
    for name, text in night(NGC_LINE, "; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n").items():
        write(r, f"2026-11-17/{name}", text)                 # strict folder already on main
    write(r, "LICENSE", "license\n")
    commit(r, "base")
    git(r, "checkout", "-q", "-b", "pr")
    monkeypatch.chdir(r)
    return r


def ey_and_gaia_lookup():
    return FakeLookup({
        (CDS, "ey-uma"): NOT_FOUND, (CFA, "ey-uma"): NOT_FOUND, (CDS, "ey uma"): EY_UMA_ANSWER,
        (GAIA, "1016132190708213504"): GAIA_ANSWER,
    })


def run_main(capsys, *args, lookup=None):
    code = co.main(["--base", "main", *args], lookup=lookup or FakeLookup())
    return code, capsys.readouterr().out


def test_e2e_nothing_to_check(repo, capsys):
    write(repo, "README.md", "hi\n")
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0
    assert "Nothing to check" in out


@pytest.fixture
def summary(tmp_path, monkeypatch):
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    return path


def test_e2e_nothing_to_check_writes_summary(repo, capsys, summary):
    write(repo, "README.md", "hi\n")
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0
    assert summary.read_text() == ("## Plan check: passed\n\n"
                                   "Nothing to check: no plan files or plan folders changed.\n")


def test_e2e_git_failure_writes_summary(repo, capsys, summary):
    code, out = run_main(capsys, "--base", "no-such-branch")
    assert code == 1
    assert out.startswith("ERROR   could not run git: git merge-base")
    text = summary.read_text()
    assert text.startswith("## Plan check: FAILED\n\nERROR could not run git: git merge-base")


def test_e2e_unexpected_exception_writes_summary(repo, capsys, summary, monkeypatch):
    def boom(*args):
        raise KeyError("surprise")
    monkeypatch.setattr(co, "classify_changed_paths", boom)
    assert run_main(capsys)[0] == 1
    assert "unexpected problem \\(KeyError: 'surprise'\\)" in summary.read_text()


def test_e2e_new_strict_night_passes(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = ey_and_gaia_lookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert "2026-11-20/1-a.txt:3" in out and "matches SIMBAD via Sesame at CDS" in out
    assert "2026-11-20/b.txt:2" in out and "Gaia DR3 (epoch 2000)" in out
    assert len(lookup.calls) == 4


def test_e2e_already_verified_and_override_reported(repo, capsys):
    for name, text in night(NGC_LINE, "M31\t0:43:44.5\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = FakeLookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert lookup.calls == []
    assert "verified by earlier plan (2026-11-17/1-a.txt line 3)" in out
    assert "verified by earlier plan with override" in out


def test_e2e_wrong_coordinate_fails_with_summary_and_annotations(repo, capsys, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    wrong = "ey-uma\t09:02:20.8\t+49:52:21\n"
    for name, text in night(wrong, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = ey_and_gaia_lookup()
    lookup.add(CFA, "ey uma", EY_UMA_CFA_ANSWER)
    lookup.add(VSX, "ey uma", NOT_FOUND)
    code, out = run_main(capsys, lookup=lookup)
    assert code == 1
    assert "::error file=2026-11-20/1-a.txt,line=3::`ey-uma` is 3.2 arcmin" in out
    md = summary.read_text()
    assert "`ey-uma` is 3.2 arcmin from the catalog position" in md


def test_e2e_sign_flipped_dec_is_mismatch(repo, capsys):
    # catalog says -0:32:00; the plan dropped the minus sign
    star = [co.Position(sky(150.0, -32 / 60), "SIMBAD via Sesame at CDS")]
    lookup = FakeLookup({(CDS, "Star X"): star, (CFA, "Star X"): star, (VSX, "Star X"): NOT_FOUND})
    for name, text in night("Star X\t10:00:00\t+0:32:00\n", "; coords-ok: test\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=lookup)
    assert code == 1
    assert ("ERROR   2026-11-20/1-a.txt:3: `Star X` is 1.1 deg from the catalog position "
            "10:00:00.00 -00:32:00.0") in out


WRONG_EY = "ey-uma\t09:02:20.8\t+49:52:21\n"      # 3.2 arcmin north of EY UMa


def wrong_ey_lookup():
    lookup = ey_and_gaia_lookup()
    lookup.add(CFA, "ey uma", EY_UMA_CFA_ANSWER)
    lookup.add(VSX, "ey uma", NOT_FOUND)
    return lookup


def test_e2e_new_folder_dated_before_cutoff_is_strict(repo, capsys):
    # a year typo: 2025 for 2026; the folder is new in this change
    for name, text in night(WRONG_EY, GAIA_LINE).items():
        write(repo, f"2025-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=wrong_ey_lookup())
    assert code == 1, out
    assert "ERROR   2025-11-20/1-a.txt:3: `ey-uma` is 3.2 arcmin" in out
    assert ("WARNING 2025-11-20: Folder `2025-11-20` is new, but its date is before "
            f"{co.STRICT_FROM.isoformat()}, when this check started. Check the year in the "
            "folder name.") in out


def test_e2e_new_folder_dated_before_cutoff_is_strict_when_staged(repo, capsys):
    for name, text in night(WRONG_EY, GAIA_LINE).items():
        write(repo, f"2025-11-20/{name}", text)
    git(repo, "add", "-A")
    code = co.main(["--staged"], lookup=wrong_ey_lookup())
    out = capsys.readouterr().out
    assert code == 1, out
    assert "ERROR   2025-11-20/1-a.txt:3" in out


def test_e2e_new_old_folder_chaining_problem_is_error(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["extra.txt"] = "#shutdown\n"
    for name, text in plans.items():
        write(repo, f"2025-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1
    assert "ERROR   2025-11-20: Chaining: Plan 2025-11-20/extra.txt is not used" in out


NESTED_MESSAGE = ("is in a subfolder, so it is not checked. Plan files must be directly inside "
                  "the dated folder: move the files up into `2026-11-20/`")


def test_e2e_plan_files_in_subfolder_of_new_folder_are_errors(repo, capsys):
    # dragging the parent folder in the web page nests the night one level down
    for name, text in night(WRONG_EY, GAIA_LINE).items():
        write(repo, f"2026-11-20/2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 1, out
    assert "ERROR   2026-11-20/2026-11-20/1-a.txt: `2026-11-20/2026-11-20/1-a.txt` " + NESTED_MESSAGE in out


def test_e2e_plan_file_in_subfolder_of_old_folder_warns(repo, capsys):
    write(repo, "2025-10-10/sub/c.txt", EY_LINE)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0, out
    assert "WARNING 2025-10-10/sub/c.txt: `2025-10-10/sub/c.txt` is in a subfolder" in out


def test_e2e_upper_case_txt_extension_is_checked(repo, capsys):
    plans = night(EY_LINE, "M31\t0:43:444\t41:46:10\n")
    plans["1-a.TXT"] = plans.pop("1-a.txt")
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1, out
    assert "2026-11-20/1-a.TXT:3: `ey-uma` matches" in out
    assert "ERROR   2026-11-20/b.txt:2: RA `0:43:444`" in out


def test_e2e_upper_case_root_plan_file_is_error(repo, capsys):
    write(repo, "EY_UMA.TXT", EY_LINE)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 1
    assert "`EY_UMA.TXT` is a plan file at the top of the repository" in out


def test_verified_index_reads_upper_case_extension():
    index = co.VerifiedIndex.from_files([("2026-11-17/A.TXT", EY_LINE)])
    assert index.find("ey-uma", EY_UMA, date(2026, 11, 18)) is not None


def test_e2e_verified_ref_lists_upper_case_extension(repo, capsys):
    write(repo, "2026-11-17/c.TXT", "#autofocus\n" + EY_LINE + "#shutdown\n")
    write(repo, "2026-11-17/b.txt", "#autofocus\n; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n#chain c.TXT\n")
    commit(repo, "on main")
    git(repo, "branch", "-f", "main")
    for name, text in night(EY_LINE, "; coords-ok: x\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = FakeLookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert "verified by earlier plan (2026-11-17/c.TXT line 2)" in out


BOM = "﻿"


def test_e2e_utf8_bom_is_accepted(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", BOM + text)
    write(repo, "2026-11-20/b.txt", BOM + GAIA_LINE + "#shutdown\n")   # target on line 1
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 0, out
    assert "2026-11-20/b.txt:1: `Gaia-DR3-1016132190708213504` matches" in out


def test_e2e_utf8_bom_in_verified_ref(repo, capsys):
    write(repo, "2026-11-17/c.txt", BOM + EY_LINE + "#shutdown\n")
    write(repo, "2026-11-17/b.txt", "; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n#chain c.txt\n")
    commit(repo, "on main")
    git(repo, "branch", "-f", "main")
    for name, text in night(EY_LINE, "; coords-ok: x\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0, out
    assert "verified by earlier plan (2026-11-17/c.txt line 1)" in out


def test_e2e_root_plan_file_is_error(repo, capsys):
    write(repo, "ey_uma.txt", EY_LINE)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 1
    assert "move these into a dated folder" in out


def test_e2e_bad_folder_name_is_error(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"Nov 20/{name}", text)
    commit(repo)
    code, out = run_main(capsys)
    assert code == 1
    assert "Folder `Nov 20` does not start with a date" in out


def test_e2e_old_folder_only_warns_and_never_looks_up(repo, capsys):
    write(repo, "2025-10-10/b.txt", "#autofocus\nM31 0:43:44 41:46:10\nNGC 1\t1:0:0\t1:0:0\n#shutdown\n")
    commit(repo)
    lookup = FakeLookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert "WARNING 2025-10-10/b.txt:2" in out and "tab" in out.lower()
    assert lookup.calls == []


def test_e2e_unchanged_file_in_strict_folder_only_warns(repo, capsys):
    for name, text in night(EY_LINE, "M31\t0:43:444\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo, "night")
    git(repo, "branch", "-f", "main")           # the night is now on main
    write(repo, "2026-11-20/1-a.txt", night(NGC_LINE, "")["1-a.txt"])
    commit(repo, "edit first plan")
    code, out = run_main(capsys)
    assert code == 0, out
    assert "WARNING 2026-11-20/b.txt:2: RA `0:43:444`" in out


def test_e2e_bad_format_in_changed_strict_file_is_error(repo, capsys):
    for name, text in night("ey-uma 09:02:20.8 +49:49:09.3\n", GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    lookup = ey_and_gaia_lookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 1
    assert "ERROR   2026-11-20/1-a.txt:3" in out and "TAB" in out


def test_e2e_chaining_problem_only_warns_in_old_folder(repo, capsys):
    # Older folders only warn (42 of them have no plan starting with 1).
    write(repo, "2025-10-10/extra.txt", "#shutdown\n")
    commit(repo)
    code, out = run_main(capsys)
    assert code == 0, out
    assert "WARNING" in out and "2025-10-10/extra.txt is not used" in out


def test_e2e_chaining_problem_is_error_in_strict_folder(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["extra.txt"] = "#shutdown\n"
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=ey_and_gaia_lookup())
    assert code == 1
    assert "ERROR" in out and "2026-11-20/extra.txt is not used" in out


def test_folder_name_with_date_prefix_but_invalid_rest_is_error():
    # check_folder_names.py is the single definition of a valid folder name.
    changes = co.classify_changed_paths(["2026-11-20 copy/1-a.txt", "2026-11-20 copy/b.txt",
                                         "2026-11-21-redo/1-a.txt"])
    assert changes.folders == {"2026-11-21-redo"}
    assert changes.plan_files == {"2026-11-21-redo/1-a.txt"}
    assert [f.level for f in changes.findings] == ["error"]
    assert "`2026-11-20 copy` is not a valid plan folder name" in changes.findings[0].message


def test_e2e_unreachable_fails_unless_offline_ok(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, lookup=FakeLookup(default=DOWN))   # every service down
    assert code == 1
    assert "Could not reach the catalog service" in out
    code, out = run_main(capsys, "--offline-ok", lookup=FakeLookup(default=DOWN))
    assert code == 0
    assert "WARNING" in out and "Could not reach" in out


def test_e2e_staged_mode_and_odd_filenames(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["b.txt"] = plans["b.txt"].replace("#shutdown", "#chain my'plan;$(x).txt")
    plans["my'plan;$(x).txt"] = "#autofocus\n" + NGC_LINE + "#shutdown\n"
    for name, text in plans.items():
        write(repo, f"2026-11-20-extra/{name}", text)
    git(repo, "add", "-A")                        # staged, not committed
    lookup = ey_and_gaia_lookup()
    code = co.main(["--staged"], lookup=lookup)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2026-11-20-extra/my'plan;$(x).txt:2" in out
    assert "verified by earlier plan" in out      # NGC 7331 from 2026-11-17 on HEAD


def test_e2e_verified_ref_is_read_from_git_not_working_tree(repo, capsys):
    # A strict folder that exists only in the working tree must not verify anything.
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-18/{name}", text)
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    git(repo, "add", "2026-11-20")
    commit(repo)
    lookup = ey_and_gaia_lookup()
    code, out = run_main(capsys, lookup=lookup)
    assert code == 0, out
    assert len(lookup.calls) == 4


def test_unreachable_with_a_mismatch_leads_with_the_offset():
    lookup = FakeLookup({
        (CDS, "ey-uma"): TimeoutError("timed out"),
        (CFA, "ey-uma"): NOT_FOUND, (CFA, "ey uma"): EY_UMA_CFA_ANSWER, (VSX, "ey uma"): NOT_FOUND,
    })
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:52:21\n", lookup)
    assert f.level == "error"
    assert f.message.startswith("`ey-uma` is 3.2 arcmin from the catalog position")
    assert "Sesame at CDS: TimeoutError: timed out" in f.message
    assert "Re-run jobs" in f.message


def test_requirements_txt_at_the_root_is_not_a_plan_file():
    changes = co.classify_changed_paths(["requirements.txt", "ey_uma.txt"])
    assert [f.path for f in changes.findings] == ["ey_uma.txt"]
