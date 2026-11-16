import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import check_objects as co  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------- sexagesimal

@pytest.mark.parametrize("text, hours", [
    ("22:37:04.0506048", 22 + 37 / 60 + 4.0506048 / 3600),   # long decimals
    ("0:43:44", 43 / 60 + 44 / 3600),                          # unpadded hours
    ("09:02:20.8", 9 + 2 / 60 + 20.8 / 3600),
    ("23:58:24", 23 + 58 / 60 + 24 / 3600),
])
def test_parse_ra_valid(text, hours):
    assert co.parse_ra(text) == pytest.approx(hours * 15)


@pytest.mark.parametrize("text", [
    "0:43:444",        # the real typo
    "10:16:41.72s",    # trailing unit letter
    "24:00:00",        # hour out of range
    "12:60:00",
    "12:00:60",
    "+12:00:00",       # RA is never signed
    "12:00",           # two fields
    "12.5",
    "",
    "12:00:00.",
])
def test_parse_ra_invalid(text):
    assert co.parse_ra(text) is None


@pytest.mark.parametrize("text, degrees", [
    ("34:24:56.80008", 34 + 24 / 60 + 56.80008 / 3600),   # unsigned Dec
    ("+49:49:09.3", 49 + 49 / 60 + 9.3 / 3600),
    ("-0:32:00.9", -(32 / 60 + 0.9 / 3600)),              # negative zero degrees
    ("-05:23:28", -(5 + 23 / 60 + 28 / 3600)),
    ("90:00:00", 90.0),
    ("8:01:02", 8 + 1 / 60 + 2 / 3600),
])
def test_parse_dec_valid(text, degrees):
    assert co.parse_dec(text) == pytest.approx(degrees)


@pytest.mark.parametrize("text", [
    "+84.32.05.6",     # dots instead of colons (real)
    "23:19:24.1:",     # trailing colon (real)
    "91:00:00",
    "90:00:01",
    "45:60:00",
    "--45:00:00",
    "45:00",
    "",
])
def test_parse_dec_invalid(text):
    assert co.parse_dec(text) is None


# ---------------------------------------------------------------- geometry

def test_separation_zero():
    assert co.separation_arcsec(10.0, 20.0, 10.0, 20.0) == pytest.approx(0)


def test_separation_in_dec():
    assert co.separation_arcsec(10.0, 20.0, 10.0, 20.0 + 1 / 60) == pytest.approx(60, rel=1e-6)


def test_separation_ra_shrinks_with_cos_dec():
    # 1 degree of RA at Dec 60 is half a degree on the sky
    assert co.separation_arcsec(0.0, 60.0, 1.0, 60.0) == pytest.approx(1800, rel=1e-3)


def test_separation_wraps_at_ra_zero():
    assert co.separation_arcsec(359.99, 0.0, 0.01, 0.0) == pytest.approx(72, rel=1e-6)


@pytest.mark.parametrize("arcsec, text", [
    (8.46, "8.5 arcsec"),
    (59.9, "59.9 arcsec"),
    (192.0, "3.2 arcmin"),
    (7200.0, "2.0 deg"),
])
def test_format_separation(arcsec, text):
    assert co.format_separation(arcsec) == text


def test_format_position():
    # ey uma from Sesame: 135.5865 49.8192
    assert co.format_position(135.58650, 49.81925) == "09:02:20.76 +49:49:09.3"
    assert co.format_position(0.0, -0.5336) == "00:00:00.00 -00:32:01.0"


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
    assert line.ra == pytest.approx(339.2669, abs=1e-4)
    assert line.dec == pytest.approx(34.41578, abs=1e-4)
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


# ---------------------------------------------------------------- response parsers
# Fixtures in tests/data are real response bodies cached on 2026-11-16, except
# where a test builds a body inline.

def fixture(name):
    return (DATA / name).read_text()


HTML_PAGE = "<!DOCTYPE html><html><body><h1>Service unavailable</h1></body></html>"


def test_parse_sesame_found_ey_uma():
    (pos,) = co.parse_sesame(fixture("sesame_cds_ey_uma.xml"), "CDS")
    assert pos.ra == pytest.approx(135.5865, abs=1e-3)
    assert pos.dec == pytest.approx(49.8192, abs=1e-3)
    assert pos.source == "SIMBAD via Sesame at CDS"


def test_parse_sesame_mirrors_give_different_m39_centres():
    (cds,) = co.parse_sesame(fixture("sesame_cds_M39.xml"), "CDS")
    (cfa,) = co.parse_sesame(fixture("sesame_cfa_M39.xml"), "CfA")
    sep = co.separation_arcsec(cds.ra, cds.dec, cfa.ra, cfa.dec)
    assert sep / 60 == pytest.approx(5.7, abs=0.1)
    assert cfa.source == "SIMBAD via Sesame at CfA"


def test_parse_sesame_not_found():
    assert co.parse_sesame(fixture("sesame_cds_Groth_notfound.xml"), "CDS") == []
    assert co.parse_sesame(fixture("sesame_cds_ey-uma_notfound.xml"), "CDS") == []


@pytest.mark.parametrize("body", [HTML_PAGE, "", "<Sesame><broken"])
def test_parse_sesame_html_is_service_error(body):
    with pytest.raises(co.ServiceError):
        co.parse_sesame(body, "CDS")


def test_parse_sesame_ned_resolver_label():
    body = ("<?xml version='1.0'?><Sesame><Target><name>x</name>"
            "<Resolver name='N=NED'><jradeg>10.5</jradeg><jdedeg>-3.25</jdedeg></Resolver>"
            "</Target></Sesame>")
    (pos,) = co.parse_sesame(body, "CfA")
    assert (pos.ra, pos.dec, pos.source) == (10.5, -3.25, "NED via Sesame at CfA")


def test_parse_mast_found():
    (pos,) = co.parse_mast(fixture("mast_tic_282794970.json"), "282794970")
    assert pos.ra == pytest.approx(199.484644701099)
    assert pos.dec == pytest.approx(16.4180442854519)
    assert pos.source == "TESS Input Catalog at MAST"


def test_parse_mast_compares_ids_as_integers():
    (pos,) = co.parse_mast(fixture("mast_tic_282794970.json"), "0282794970")
    assert pos.source == "TESS Input Catalog at MAST"


def test_tic_with_leading_zeros_is_confirmed():
    fetch = FakeFetch({co.mast_request("282794970"): fixture("mast_tic_282794970.json")})
    v = check("TIC_0282794970.01", *TIC_POS, fetch)
    assert v.status == "confirmed"
    assert co.mast_request("0282794970") == co.mast_request("282794970")


def test_parse_mast_wrong_id_or_empty_is_not_found():
    assert co.parse_mast(fixture("mast_tic_282794970.json"), "1") == []
    assert co.parse_mast('{"status" : "COMPLETE","msg" : "","data":[]}', "1") == []


@pytest.mark.parametrize("body", [HTML_PAGE, '{"status": "ERROR", "msg": "boom", "data": []}'])
def test_parse_mast_errors(body):
    with pytest.raises(co.ServiceError):
        co.parse_mast(body, "1")


def test_parse_gaia_uses_epoch_2000_position():
    (pos,) = co.parse_gaia(fixture("gaia_dr3_1016132190708213504.json"))
    assert pos.ra == pytest.approx(136.70305703091927)
    assert pos.dec == pytest.approx(50.004432351457204)
    assert pos.source == "Gaia DR3 (epoch 2000)"


def test_parse_gaia_no_row_and_html():
    assert co.parse_gaia('{"metadata": [], "data": []}') == []
    with pytest.raises(co.ServiceError):
        co.parse_gaia(HTML_PAGE)


def test_parse_vsx_found_and_not_found():
    (pos,) = co.parse_vsx(fixture("vsx_ASASSN-26be.json"))
    assert (pos.ra, pos.dec) == (pytest.approx(270.91367), pytest.approx(19.97956))
    assert pos.source == "AAVSO VSX"
    assert co.parse_vsx(fixture("vsx_Groth_notfound.json")) == []
    with pytest.raises(co.ServiceError):
        co.parse_vsx(HTML_PAGE)


# ---------------------------------------------------------------- routing

@pytest.mark.parametrize("name, route", [
    ("TIC_435934762.01", ("tic", "435934762")),
    ("TIC 282794970", ("tic", "282794970")),
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


# ---------------------------------------------------------------- catalog client and verdicts

NOT_FOUND_SESAME = fixture("sesame_cds_Groth_notfound.xml")
NOT_FOUND_VSX = fixture("vsx_Groth_notfound.json")


class FakeFetch:
    """Stands in for the network: maps (url, data) of a request to a body or an exception.

    A request with no response (and no explicit default) is recorded in
    `unexpected`; the autouse fixture below fails the test if any were made.
    Tests of an outage pass default=DOWN explicitly.
    """

    instances = []

    def __init__(self, responses=None, default=None):
        self.responses = {}
        self.calls = []
        self.unexpected = []
        self.default = default
        for request, body in (responses or {}).items():
            self.add(request, body)
        FakeFetch.instances.append(self)

    def add(self, request, body):
        self.responses[(request.url, request.data)] = body

    def __call__(self, url, data=None):
        self.calls.append((url, data))
        body = self.responses.get((url, data), self.default)
        if body is None:
            self.unexpected.append((url, data))
            raise AssertionError(f"unexpected request {url} {data}")
        if isinstance(body, Exception):
            raise body
        return body


DOWN = OSError("connection refused")   # an explicit transport failure


@pytest.fixture(autouse=True)
def no_unexpected_requests():
    FakeFetch.instances.clear()
    yield
    unexpected = [call for f in FakeFetch.instances for call in f.unexpected]
    FakeFetch.instances.clear()
    assert unexpected == [], "the code made requests the test did not expect"


def sesame(mirror, query):
    return co.sesame_request(mirror, query)


def test_fake_fetch_records_unexpected_requests():
    # Catalogs turns every fetch exception into a service failure, so an
    # exception alone would not make a test fail; the record does.
    fetch = FakeFetch()
    check("Groth", 214.8, 52.8, fetch)
    assert fetch.unexpected
    fetch.unexpected.clear()


def test_requests_have_expected_urls_and_tolerances():
    r = co.sesame_request("CDS", "ey uma")
    assert r.url == "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?ey%20uma"
    assert r.data is None and r.tolerance == 120
    r = co.sesame_request("CfA", "M39")
    assert r.url == "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?M39"
    r = co.vsx_request("ASASSN-26be")
    assert r.url.endswith("ident=ASASSN-26be") and r.tolerance == 20
    r = co.mast_request("282794970")
    assert r.url == "https://mast.stsci.edu/api/v0/invoke"
    assert "282794970" in r.data and r.tolerance == 20
    r = co.gaia_request("1016132190708213504")
    assert "EPOCH_PROP_POS" in r.data and "1016132190708213504" in r.data and r.tolerance == 20


def check(name, ra_deg, dec_deg, fetch):
    return co.check_target(name, ra_deg, dec_deg, co.Catalogs(fetch))


def offset(ra, dec, arcsec):
    """Shift a position north by arcsec."""
    return ra, dec + arcsec / 3600


TIC_POS = (199.484644701099, 16.4180442854519)


def test_tic_confirmed_with_one_request():
    fetch = FakeFetch({co.mast_request("282794970"): fixture("mast_tic_282794970.json")})
    v = check("TIC_282794970.01", *offset(*TIC_POS, 8.5), fetch)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "TESS Input Catalog at MAST"
    assert len(fetch.calls) == 1


def test_tic_off_by_30_arcsec_is_mismatch():
    fetch = FakeFetch({co.mast_request("282794970"): fixture("mast_tic_282794970.json")})
    v = check("TIC_282794970.01", *offset(*TIC_POS, 30), fetch)
    assert v.status == "mismatch"
    assert v.nearest_sep == pytest.approx(30, abs=0.1)


def test_tic_not_in_catalog():
    fetch = FakeFetch({co.mast_request("1"): '{"status":"COMPLETE","msg":"","data":[]}'})
    assert check("TIC_1", 10, 10, fetch).status == "notfound"


def test_gaia_confirmed():
    fetch = FakeFetch({co.gaia_request("1016132190708213504"):
                       fixture("gaia_dr3_1016132190708213504.json")})
    # the real plan line: 09:06:48 +50:00:16 (whole seconds; ~8 arcsec off)
    v = check("Gaia-DR3-1016132190708213504", co.parse_ra("09:06:48"), co.parse_dec("+50:00:16"), fetch)
    assert v.status == "confirmed"


EY_UMA = (co.parse_ra("09:02:20.8"), co.parse_dec("+49:49:09.3"))


def test_sesame_tries_normalisations_and_stops_when_confirmed():
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): fixture("sesame_cds_ey-uma_notfound.xml"),
        sesame("CfA", "ey-uma"): fixture("sesame_cds_ey-uma_notfound.xml"),
        sesame("CDS", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
    })
    v = check("ey-uma", *EY_UMA, fetch)
    assert v.status == "confirmed"
    assert len(fetch.calls) == 3          # CfA "ey uma" and VSX never asked


M39_PLAN = (co.parse_ra("21:31:33.4"), co.parse_dec("+48:14:49"))   # CfA centre


def test_sesame_cds_disagreeing_is_not_final_cfa_confirms():
    fetch = FakeFetch({
        sesame("CDS", "M39"): fixture("sesame_cds_M39.xml"),
        sesame("CfA", "M39"): fixture("sesame_cfa_M39.xml"),
    })
    v = check("M39", *M39_PLAN, fetch)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "SIMBAD via Sesame at CfA"


def test_sesame_mismatch_asks_vsx_and_reports_nearest():
    fetch = FakeFetch({
        sesame("CDS", "M39"): fixture("sesame_cds_M39.xml"),
        sesame("CfA", "M39"): fixture("sesame_cfa_M39.xml"),
        co.vsx_request("M 39"): NOT_FOUND_VSX,
    })
    ra, dec = offset(*M39_PLAN, 300)      # 5 arcmin north of the CfA centre
    v = check("M39", ra, dec, fetch)
    assert v.status == "mismatch"
    assert v.nearest.source == "SIMBAD via Sesame at CfA"
    assert v.nearest_sep == pytest.approx(300, abs=1)
    assert (co.vsx_request("M 39").url, None) in fetch.calls
    # once a spelling is found, other spellings are not tried in Sesame
    assert (sesame("CDS", "M 39").url, None) not in fetch.calls


def test_vsx_rescues_name_sesame_does_not_know():
    fetch = FakeFetch({
        sesame("CDS", "ASASSN_26be"): NOT_FOUND_SESAME, sesame("CfA", "ASASSN_26be"): NOT_FOUND_SESAME,
        sesame("CDS", "ASASSN-26be"): NOT_FOUND_SESAME, sesame("CfA", "ASASSN-26be"): NOT_FOUND_SESAME,
        sesame("CDS", "ASASSN 26be"): NOT_FOUND_SESAME, sesame("CfA", "ASASSN 26be"): NOT_FOUND_SESAME,
        co.vsx_request("ASASSN-26be"): fixture("vsx_ASASSN-26be.json"),
    })
    v = check("ASASSN_26be", co.parse_ra("18:03:39.29"), co.parse_dec("+19:58:46.3"), fetch)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "AAVSO VSX"


def test_not_found_anywhere():
    fetch = FakeFetch({
        sesame("CDS", "Groth"): NOT_FOUND_SESAME, sesame("CfA", "Groth"): NOT_FOUND_SESAME,
        co.vsx_request("Groth"): NOT_FOUND_VSX,
    })
    v = check("Groth", 214.8, 52.8, fetch)
    assert v.status == "notfound"
    assert v.failures == []


def test_html_page_counts_as_service_failure_not_not_found():
    fetch = FakeFetch({
        sesame("CDS", "Groth"): HTML_PAGE, sesame("CfA", "Groth"): NOT_FOUND_SESAME,
        co.vsx_request("Groth"): NOT_FOUND_VSX,
    })
    v = check("Groth", 214.8, 52.8, fetch)
    assert v.status == "unreachable"
    assert "Sesame at CDS" in v.failures[0]


def test_one_mirror_down_other_confirms():
    fetch = FakeFetch({
        sesame("CDS", "M39"): co.ServiceError("timed out"),
        sesame("CfA", "M39"): fixture("sesame_cfa_M39.xml"),
    })
    assert check("M39", *M39_PLAN, fetch).status == "confirmed"


def test_mismatch_with_a_failed_source_is_unreachable_but_keeps_nearest():
    fetch = FakeFetch({
        sesame("CDS", "M39"): fixture("sesame_cds_M39.xml"),
        sesame("CfA", "M39"): co.ServiceError("timed out"),
        co.vsx_request("M 39"): NOT_FOUND_VSX,
    })
    v = check("M39", *M39_PLAN, fetch)
    assert v.status == "unreachable"
    assert v.nearest.source == "SIMBAD via Sesame at CDS"


def test_catalogs_memoises_requests_within_a_run():
    fetch = FakeFetch({co.mast_request("282794970"): fixture("mast_tic_282794970.json")})
    cats = co.Catalogs(fetch)
    co.check_target("TIC_282794970.01", *TIC_POS, cats)
    co.check_target("TIC_282794970.02", *offset(*TIC_POS, 60), cats)
    assert len(fetch.calls) == 1


def test_catalogs_memoises_failures_too():
    fetch = FakeFetch({co.mast_request("1"): co.ServiceError("down")})
    cats = co.Catalogs(fetch)
    co.check_target("TIC_1", 1, 1, cats)
    co.check_target("TIC_1", 1, 1, cats)
    assert len(fetch.calls) == 1


def test_disk_cache_reused_and_only_good_answers_stored(tmp_path):
    good = co.mast_request("282794970")
    bad = co.sesame_request("CDS", "Groth")
    fetch = FakeFetch({good: fixture("mast_tic_282794970.json"), bad: HTML_PAGE})
    cats = co.Catalogs(fetch, cache_dir=tmp_path)
    cats.ask(good)
    with pytest.raises(co.ServiceError):
        cats.ask(bad)
    second = FakeFetch({bad: NOT_FOUND_SESAME})
    cats2 = co.Catalogs(second, cache_dir=tmp_path)
    assert cats2.ask(good)[0].source == "TESS Input Catalog at MAST"
    assert cats2.ask(bad) == []
    assert second.calls == [(bad.url, bad.data)]


# ---------------------------------------------------------------- urllib fetcher (no network)

class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUrlopen:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


def test_fetcher_sends_generic_user_agent_and_timeout():
    opener = FakeUrlopen(["ok"])
    fetch = co.UrllibFetcher(urlopen=opener, sleep=lambda s: None)
    assert fetch("https://example.org/x", None) == "ok"
    request, timeout = opener.requests[0]
    assert request.get_header("User-agent") == "acp-plans-coordinate-check (python-urllib)"
    assert timeout == co.TIMEOUT
    assert request.data is None


def test_fetcher_posts_encoded_body():
    opener = FakeUrlopen(["ok"])
    co.UrllibFetcher(urlopen=opener, sleep=lambda s: None)("https://example.org", "a=1&b=2")
    assert opener.requests[0][0].data == b"a=1&b=2"


def test_fetcher_retries_once():
    opener = FakeUrlopen([TimeoutError("slow"), "ok"])
    assert co.UrllibFetcher(urlopen=opener, sleep=lambda s: None)("https://e.org", None) == "ok"
    assert len(opener.requests) == 2


def test_fetcher_gives_up_after_retry():
    opener = FakeUrlopen([TimeoutError("slow"), OSError("reset"), "never"])
    with pytest.raises(OSError):
        co.UrllibFetcher(urlopen=opener, sleep=lambda s: None)("https://e.org", None)
    assert len(opener.requests) == 2


def test_fetcher_spaces_requests():
    sleeps = []
    clock = iter([100.0, 100.2, 100.4, 105.0]).__next__
    fetch = co.UrllibFetcher(urlopen=FakeUrlopen(["a", "b"]), sleep=sleeps.append, clock=clock)
    fetch("https://e.org/1", None)
    fetch("https://e.org/2", None)
    assert sleeps == [pytest.approx(co.MIN_INTERVAL - 0.2)]


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
    ra, dec = EY_UMA
    hit = index.find("EY UMa", ra, dec + 10 / 3600, later)
    assert hit is not None and hit.path == "2026-11-17/a.txt" and hit.line == 1
    assert index.find("ey uma", ra, dec + 30 / 3600, later) is None      # too far
    assert index.find("other", ra, dec, later) is None                    # name differs
    assert index.find("ey-uma", ra, dec, date(2026, 11, 17)) is None      # not earlier
    assert index.find("M33", co.parse_ra("1:33:51"), co.parse_dec("30:39:37"), later) is None
    m31 = index.find("M31", co.parse_ra("0:43:44"), co.parse_dec("41:46:10"), later)
    assert m31.override == "mosaic"


def line_of(text):
    return [ln for ln in co.parse_plan_text(text) if ln.kind == "target"][0]


EMPTY_INDEX = co.VerifiedIndex([])
NIGHT = date(2026, 11, 20)


def run_line(text, fetch, index=EMPTY_INDEX, offline_ok=False):
    return co.check_line("2026-11-20/x.txt", line_of(text), NIGHT, index,
                         co.Catalogs(fetch), offline_ok)


def test_line_with_override_passes_without_lookup_and_is_listed():
    fetch = FakeFetch()
    (f,) = run_line("; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n", fetch)
    assert f.level == "override"
    assert "mosaic pane" in f.message and f.line == 2
    assert fetch.calls == []


def test_line_with_empty_override_is_error():
    (f,) = run_line("; coords-ok:\nM31\t0:43:44\t41:46:10\n", FakeFetch())
    assert f.level == "error"
    assert "reason" in f.message


def test_line_verified_by_earlier_plan():
    index = co.VerifiedIndex.from_files([("2026-11-17/a.txt", "ey-uma\t09:02:20.8\t+49:49:09.3\n")])
    fetch = FakeFetch()
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:49:09.3\n", fetch, index)
    assert f.level == "notice"
    assert "2026-11-17/a.txt" in f.message
    assert fetch.calls == []


def test_line_verified_by_earlier_plan_with_override_is_flagged():
    index = co.VerifiedIndex.from_files(
        [("2026-11-17/a.txt", "; coords-ok: mosaic\nM31\t0:43:44\t41:46:10\n")])
    (f,) = run_line("M31\t0:43:44\t41:46:10\n", FakeFetch(), index)
    assert f.level == "override"
    assert "verified by earlier plan with override" in f.message
    assert "mosaic" in f.message


def test_line_mismatch_message_for_students():
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): NOT_FOUND_SESAME, sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CDS", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
        sesame("CfA", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
        co.vsx_request("ey uma"): NOT_FOUND_VSX,
    })
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:52:21\n", fetch)   # 3.2 arcmin north
    assert f.level == "error"
    assert f.message.startswith("`ey-uma` is 3.2 arcmin from the catalog position "
                                "09:02:20.76 +49:49:09.2 (SIMBAD via Sesame at CDS).")
    assert f.message.endswith("Fix the coordinates, or if the offset is deliberate add "
                              "`; coords-ok: <reason>` on its own line anywhere above the target "
                              "line, after the previous target.")


def test_line_confirmed_is_notice():
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): NOT_FOUND_SESAME, sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CDS", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
    })
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:49:09.3\n", fetch)
    assert f.level == "notice"
    assert "SIMBAD via Sesame at CDS" in f.message


def test_line_not_found_message():
    fetch = FakeFetch({sesame("CDS", "Groth"): NOT_FOUND_SESAME,
                       sesame("CfA", "Groth"): NOT_FOUND_SESAME,
                       co.vsx_request("Groth"): NOT_FOUND_VSX})
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", fetch)
    assert f.level == "error"
    assert f.message.startswith("`Groth` was not found in any catalog.")
    assert f.message.endswith("if it is right, add `; coords-ok: <where the coordinates came "
                              "from>` on its own line anywhere above the target line, after the "
                              "previous target.")


def test_line_unreachable_error_or_warning_offline():
    responses = {sesame("CDS", "Groth"): co.ServiceError("timed out"),
                 sesame("CfA", "Groth"): HTML_PAGE,
                 co.vsx_request("Groth"): OSError("no route")}
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", FakeFetch(responses))
    assert f.level == "error"
    assert f.message.startswith("Could not reach the catalog service")
    assert "Re-run jobs" in f.message
    (f,) = run_line("Groth\t14:19:00\t+52:48:00\n", FakeFetch(responses), offline_ok=True)
    assert f.level == "warning"


def test_mp_line_is_notice():
    line = co.parse_plan_text("MP 00018\n")[0]
    (f,) = co.check_line("2026-11-20/x.txt", line, NIGHT, EMPTY_INDEX, co.Catalogs(FakeFetch()), False)
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
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
    assert code == 1
    assert "ERROR   2026-11-20/caf\\xe9.txt:1:" in out


def test_e2e_directory_named_like_a_plan_is_reported(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", text)
    write(repo, "2026-11-20/x.txt/notes.md", "hi\n")
    commit(repo)
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    fetch = ey_and_gaia_fetch()
    code, out = run_main(capsys, fetch=fetch)
    assert "verified by earlier plan (2026-11-17/c.txt" not in out
    assert "`ey-uma` matches SIMBAD" in out


def test_e2e_override_above_directives_applies(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["b.txt"] = ("; coords-ok: mosaic pane 2\n; ACP plan\n\n#autofocus\n#repeat 9\n"
                      "M31\t0:50:00\t41:46:10\n#shutdown\n")
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    fetch = ey_and_gaia_fetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert "OVERRIDE2026-11-20/b.txt:6: `M31`: coordinates accepted without a catalog check" in out


def test_e2e_stray_override_warns_and_does_not_cover_a_later_target(repo, capsys):
    plans = night(WRONG_EY, GAIA_LINE)
    plans["1-a.txt"] = "; coords-ok: offset\nMP 00018\n" + plans["1-a.txt"]
    for name, text in plans.items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, fetch=wrong_ey_fetch())
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


def ey_and_gaia_fetch():
    return FakeFetch({
        sesame("CDS", "ey-uma"): NOT_FOUND_SESAME, sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CDS", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
        co.gaia_request("1016132190708213504"): fixture("gaia_dr3_1016132190708213504.json"),
    })


def run_main(capsys, *args, fetch=None):
    code = co.main(["--base", "main", *args], fetch=fetch or FakeFetch())
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
    fetch = ey_and_gaia_fetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert "2026-11-20/1-a.txt:3" in out and "matches SIMBAD via Sesame at CDS" in out
    assert "2026-11-20/b.txt:2" in out and "Gaia DR3 (epoch 2000)" in out
    assert len(fetch.calls) == 4


def test_e2e_already_verified_and_override_reported(repo, capsys):
    for name, text in night(NGC_LINE, "M31\t0:43:44.5\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    fetch = FakeFetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert fetch.calls == []
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
    fetch = ey_and_gaia_fetch()
    fetch.add(sesame("CfA", "ey uma"), fixture("sesame_cds_ey_uma.xml"))
    fetch.add(co.vsx_request("ey uma"), NOT_FOUND_VSX)
    code, out = run_main(capsys, fetch=fetch)
    assert code == 1
    assert "::error file=2026-11-20/1-a.txt,line=3::`ey-uma` is 3.2 arcmin" in out
    md = summary.read_text()
    assert "`ey-uma` is 3.2 arcmin from the catalog position" in md


def sesame_xml(ra_deg, dec_deg):
    return ("<?xml version='1.0'?><Sesame><Target><name>x</name>"
            f"<Resolver name='S=Simbad'><jradeg>{ra_deg}</jradeg><jdedeg>{dec_deg}</jdedeg>"
            "</Resolver></Target></Sesame>")


def test_e2e_sign_flipped_dec_is_mismatch(repo, capsys):
    # catalog says -0:32:00; the plan dropped the minus sign
    star = sesame_xml(150.0, -32 / 60)
    fetch = FakeFetch({sesame("CDS", "Star X"): star, sesame("CfA", "Star X"): star,
                       co.vsx_request("Star X"): NOT_FOUND_VSX})
    for name, text in night("Star X\t10:00:00\t+0:32:00\n", "; coords-ok: test\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, fetch=fetch)
    assert code == 1
    assert ("ERROR   2026-11-20/1-a.txt:3: `Star X` is 1.1 deg from the catalog position "
            "10:00:00.00 -00:32:00.0") in out


WRONG_EY = "ey-uma\t09:02:20.8\t+49:52:21\n"      # 3.2 arcmin north of EY UMa


def wrong_ey_fetch():
    fetch = ey_and_gaia_fetch()
    fetch.add(sesame("CfA", "ey uma"), fixture("sesame_cds_ey_uma.xml"))
    fetch.add(co.vsx_request("ey uma"), NOT_FOUND_VSX)
    return fetch


def test_e2e_new_folder_dated_before_cutoff_is_strict(repo, capsys):
    # a year typo: 2025 for 2026; the folder is new in this change
    for name, text in night(WRONG_EY, GAIA_LINE).items():
        write(repo, f"2025-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, fetch=wrong_ey_fetch())
    assert code == 1, out
    assert "ERROR   2025-11-20/1-a.txt:3: `ey-uma` is 3.2 arcmin" in out
    assert ("WARNING 2025-11-20: Folder `2025-11-20` is new, but its date is before "
            f"{co.STRICT_FROM.isoformat()}, when this check started. Check the year in the "
            "folder name.") in out


def test_e2e_new_folder_dated_before_cutoff_is_strict_when_staged(repo, capsys):
    for name, text in night(WRONG_EY, GAIA_LINE).items():
        write(repo, f"2025-11-20/{name}", text)
    git(repo, "add", "-A")
    code = co.main(["--staged"], fetch=wrong_ey_fetch())
    out = capsys.readouterr().out
    assert code == 1, out
    assert "ERROR   2025-11-20/1-a.txt:3" in out


def test_e2e_new_old_folder_chaining_problem_is_error(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["extra.txt"] = "#shutdown\n"
    for name, text in plans.items():
        write(repo, f"2025-11-20/{name}", text)
    commit(repo)
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    assert index.find("ey-uma", *EY_UMA, date(2026, 11, 18)) is not None


def test_e2e_verified_ref_lists_upper_case_extension(repo, capsys):
    write(repo, "2026-11-17/c.TXT", "#autofocus\n" + EY_LINE + "#shutdown\n")
    write(repo, "2026-11-17/b.txt", "#autofocus\n; coords-ok: mosaic pane\nM31\t0:43:44\t41:46:10\n#chain c.TXT\n")
    commit(repo, "on main")
    git(repo, "branch", "-f", "main")
    for name, text in night(EY_LINE, "; coords-ok: x\nM31\t0:43:44\t41:46:10\n").items():
        write(repo, f"2026-11-20/{name}", text)
    commit(repo)
    fetch = FakeFetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert "verified by earlier plan (2026-11-17/c.TXT line 2)" in out


BOM = "﻿"


def test_e2e_utf8_bom_is_accepted(repo, capsys):
    for name, text in night(EY_LINE, GAIA_LINE).items():
        write(repo, f"2026-11-20/{name}", BOM + text)
    write(repo, "2026-11-20/b.txt", BOM + GAIA_LINE + "#shutdown\n")   # target on line 1
    commit(repo)
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    fetch = FakeFetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert "WARNING 2025-10-10/b.txt:2" in out and "tab" in out.lower()
    assert fetch.calls == []


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
    fetch = ey_and_gaia_fetch()
    code, out = run_main(capsys, fetch=fetch)
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
    code, out = run_main(capsys, fetch=ey_and_gaia_fetch())
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
    code, out = run_main(capsys, fetch=FakeFetch(default=DOWN))   # every service down
    assert code == 1
    assert "Could not reach the catalog service" in out
    code, out = run_main(capsys, "--offline-ok", fetch=FakeFetch(default=DOWN))
    assert code == 0
    assert "WARNING" in out and "Could not reach" in out


def test_e2e_staged_mode_and_odd_filenames(repo, capsys):
    plans = night(EY_LINE, GAIA_LINE)
    plans["b.txt"] = plans["b.txt"].replace("#shutdown", "#chain my'plan;$(x).txt")
    plans["my'plan;$(x).txt"] = "#autofocus\n" + NGC_LINE + "#shutdown\n"
    for name, text in plans.items():
        write(repo, f"2026-11-20-extra/{name}", text)
    git(repo, "add", "-A")                        # staged, not committed
    fetch = ey_and_gaia_fetch()
    code = co.main(["--staged"], fetch=fetch)
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
    fetch = ey_and_gaia_fetch()
    code, out = run_main(capsys, fetch=fetch)
    assert code == 0, out
    assert len(fetch.calls) == 4


def test_failed_service_is_not_asked_again_in_the_run():
    # CDS timing out costs a timeout plus a retry per request; after one failure
    # the run skips it and relies on the other sources.
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): co.ServiceError("timed out"),
        sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CfA", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
    })
    v = check("ey-uma", *EY_UMA, fetch)
    assert v.status == "confirmed"
    assert (sesame("CDS", "ey uma").url, None) not in fetch.calls
    assert len(fetch.calls) == 3


def test_malformed_answer_fails_only_that_request():
    # an HTML page for one name says nothing about the service's other answers
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): HTML_PAGE,
        sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CDS", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
    })
    v = check("ey-uma", *EY_UMA, fetch)
    assert v.status == "confirmed"
    assert v.confirmed_by.source == "SIMBAD via Sesame at CDS"


def test_malformed_answer_is_still_remembered_for_its_request():
    fetch = FakeFetch({co.mast_request("1"): HTML_PAGE})
    cats = co.Catalogs(fetch)
    for _ in range(2):
        with pytest.raises(co.ServiceError):
            cats.ask(co.mast_request("1"))
    assert len(fetch.calls) == 1


def test_unreachable_failures_are_unique_and_keep_the_original_error():
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): OSError("timed out"),
        sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CfA", "ey uma"): NOT_FOUND_SESAME,
        co.vsx_request("ey uma"): NOT_FOUND_VSX,
    })
    v = check("ey-uma", *EY_UMA, fetch)
    assert v.status == "unreachable"
    assert v.failures == ["Sesame at CDS: OSError: timed out"]


def test_unreachable_with_a_mismatch_leads_with_the_offset():
    fetch = FakeFetch({
        sesame("CDS", "ey-uma"): OSError("timed out"),
        sesame("CfA", "ey-uma"): NOT_FOUND_SESAME,
        sesame("CfA", "ey uma"): fixture("sesame_cds_ey_uma.xml"),
        co.vsx_request("ey uma"): NOT_FOUND_VSX,
    })
    (f,) = run_line("ey-uma\t09:02:20.8\t+49:52:21\n", fetch)
    assert f.level == "error"
    assert f.message.startswith("`ey-uma` is 3.2 arcmin from the catalog position")
    assert "Sesame at CDS: OSError: timed out" in f.message
    assert "Re-run jobs" in f.message
