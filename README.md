# ACP plans

This repository holds the ACP observing plans for the telescope, one folder
per night. Each folder is named for the date of the night, for example
`2026-11-20`, and holds that night's plan files (`.txt`).

Every pull request is checked automatically. The check must pass before an
admin merges the plans.

## Adding a night's plans (web browser only)

1. Name the folder on your computer `YYYY-MM-DD`, the date of the night. You
   may add `-something` after the date (for example `2023-05-20-day-calib`).
   No spaces.
2. On the repository's main page, click **Add file** -> **Upload files**.
3. Drag the whole folder into the page, not the files inside it. Dragging the
   folder creates the folder; dragging loose files puts them at the top of the
   repository, and the check rejects them.
4. Click the button to commit and open a pull request. GitHub makes a branch
   and a pull request for you.
5. Wait for the check to finish. A green check mark means it passed. A red X
   means something needs fixing (see below).
6. An admin (Matt or JuanCab) reviews and merges the pull request. Only admins
   can merge.

## What the check does

For every dated folder the pull request touches, the check:

- checks the folder name;
- checks the chaining: the first plan's name starts with `1`, every `#chain`
  points to a plan that exists, every plan is used, and every flat
  configuration file that is called exists;
- checks that every line of every plan file is a valid plan line: a target
  line, a `#directive`, a `; comment`, a blank line, or an `MP nnnnn`
  minor-planet line;
- checks the coordinates of every target in the plan files you added or
  changed against an astronomical catalog.

A target line is the name, the RA and the Dec, separated by TAB characters:

```
NGC 7331	22:37:04.0	+34:24:56.8
```

RA is hours:minutes:seconds; Dec is degrees:minutes:seconds, with or without a
`+` sign (`-0:32:00.9` is fine).

### How the coordinates are checked

- `TIC_nnnn` names are looked up in the TESS Input Catalog (MAST).
- `Gaia-DR3-nnnn` names are looked up in Gaia DR3.
- Other names are looked up in SIMBAD, NED and VizieR (through the Sesame name
  resolver), then in the AAVSO VSX.
- The coordinates pass if they are within **20 arcsec** of a TIC, Gaia or VSX
  position, or within **2 arcmin** of a SIMBAD, NED or VizieR position.
- A target is looked up only the first time it appears. If a plan already
  merged into `main` (from a night on or after the go-live date) has a target
  with the same name within 20 arcsec, the target is accepted without a
  lookup. The name comparison ignores capital letters, spaces, `_` and `-`.
- Minor-planet lines (`MP nnnnn`) are not checked against a catalog.
- Folders that were already in the repository and are dated before the
  go-live date (the `STRICT_FROM` date near the top of `check_objects.py`) are
  never looked up, and their chaining and plan-line problems are only
  warnings. A folder that the pull request creates is always checked in full,
  whatever its date, and gets a warning if its date is before the go-live
  date (check the year). A bad folder name is an error at any date.
- Plan files that the pull request did not change get warnings only, even
  when other files in the same folder changed.
- The flat configuration files `DFlat.txt` and `ScreenFlats.txt` are not
  checked line by line.

## When the check fails (red X)

1. In the pull request, scroll to the checks near the bottom and click
   **Details** next to the failed check.
2. Click **Summary** (top left). The summary lists every problem with the file,
   the line number and what to do. Problems also show as notes on the lines in
   the **Files changed** tab, but the summary is the complete list.
3. To fix a file, open the pull request's **Files changed** tab, click **...**
   at the top right of the file, then **Edit file**. Make the change and commit
   it to the same branch.

   Do not edit the file from the repository's main page: that starts a second
   branch and a second pull request.
4. The check runs again on every commit. Repeat until it passes.

### What the messages mean

Messages are listed under **Errors** (must be fixed), **Warnings** (do not
fail the check), **Overrides** (for the reviewer) and **Notices**.

| Message (start) | What to do |
|---|---|
| `RA ... is not a valid coordinate` / `Dec ... is not a valid coordinate` | Fix the number. RA is `hh:mm:ss.s` (hours below 24), Dec is `+dd:mm:ss.s`; minutes and seconds below 60. |
| `This looks like a target line, but its fields are separated by spaces.` | Put a single TAB between the name and the RA, and between the RA and the Dec. Spaces are allowed only inside the name. |
| `Not a valid plan line.` | The line is not a target, a `#directive` or a `; comment`. Fix it, or start it with `;` if it is a comment. |
| `... is ... from the catalog position ... The limit for that catalog is ...` | Your coordinates are too far from the catalog. Check them against your source and fix them. If the offset is deliberate (for example a mosaic pane), add a `coords-ok` line; see below. |
| `... was not found in any catalog.` | Check the spelling of the name. If it is right, add a `coords-ok` line saying where the coordinates came from; see below. |
| `Could not reach the catalog service to confirm ...` | Not your fault: a catalog website did not answer. In the check's page click **Re-run jobs**. If it keeps failing, ask an admin. |
| `... and the check could not reach every catalog service to compare with` | The coordinates did not match the catalogs that answered, and some catalog did not answer. Check the coordinates; if they are right, use **Re-run jobs**, and if it keeps failing ask an admin. |
| `The ; coords-ok: line above ... has no reason.` | Write the reason after `coords-ok:`. |
| `... is a plan file at the top of the repository.` | The file must be inside a dated folder. In **Edit file**, change the file name box to start with the folder, for example `2026-11-20/`. |
| `Folder ... does not start with a date` / `... is not a valid plan folder name` | The folder must be named `YYYY-MM-DD` or `YYYY-MM-DD-something`, with no spaces. |
| `Chaining: No plan in the plan set starts with 1.` | Rename the first plan so its name starts with `1`. |
| `Chaining: Plan ... is not used.` | No plan chains to this one. Fix the `#chain` line of the plan before it, or remove the file. |
| `Chaining: Plan ... chains to a non-existent plan.` | The file named in the last `#chain` line is missing. Fix the name or add the file. |
| `Chaining: Plan ... calls a non-existent flat configuration file.` | The flats file named in `#duskflats`, `#dawnflats` or `#screenflats` is missing. Add it or fix the name. |
| `The file is not UTF-8 text.` | Save the file as plain text (UTF-8) and upload it again. |
| `... is in a subfolder, so it is not checked.` | The folder was dragged into another folder. Move the files up so they are directly inside the dated folder. |
| `Folder ... is new, but its date is before ...` | (Warning) Check the year in the folder name. |
| `Dec ... starts with a dash that is not the plain minus sign.` | Delete the dash and type the `-` key. |
| `This ; coords-ok: line does not apply to any target.` | (Warning) Move the line so it is above the target it is for, after the previous target. |
| `Chaining: Plan ... is empty` | The file has only comments and blank lines. Add the plan or remove the file. |
| `Chaining: Plan ... has a #chain line with no plan file name.` | Write the name of the next plan after `#chain`. |
| `... is a symbolic link` / `... is not a regular file` | Upload the plan as an ordinary text file. |
| `The plan check stopped because of an unexpected problem` | Use **Re-run jobs**. If it keeps failing, ask an admin. |

Notices such as `matches TESS Input Catalog at MAST`, `verified by earlier
plan` and `Minor planet line ... is not checked` need no action.

### Accepting coordinates the catalog disagrees with: `coords-ok`

If your coordinates are right but the check disagrees (a mosaic pane, a field
centre, an object no catalog knows), add a comment line

```
; coords-ok: <reason or source of the coordinates>
```

on its own line **above the target line**, after the previous target. Only
comment lines, `#` lines and blank lines may lie between it and the target, so
it can go with the other comments above the `#` lines. For example:

```
; coords-ok: pane 2 of the M31 mosaic, offset from the centre on purpose
#count 1,1,1,1
#interval 60,60,60,60
M31	00:44:10.0	+41:36:00.0
```

Each `coords-ok` line covers one target: the next one below it.

The separators in the target line must be TAB characters, not spaces. ACP
ignores lines starting with `;`. Every override is listed in the summary so the
reviewer can see it, and a later night that reuses the same target and
coordinates is also shown as relying on that override.

### If a catalog is down

The check needs a catalog website whenever a plan has a target that has not
been checked before. If the check says it could not reach the catalog service,
use **Re-run jobs** later. If the service stays down, ask an admin: admins can
merge past a red check.

## For admins

- The workflow is `.github/workflows/check_plans.yml`. Its job `check-plans`
  runs on every push and pull request to `main` (no path filters). The job
  `unit-tests` runs the tests separately and is not meant to be required.
- After this check is merged, add `check-plans` as a required status check on
  `main` (Settings -> Branches -> branch protection rule for `main`). GitHub
  offers the check only after it has run successfully in the last seven days.
  - Do not turn on "Require branches to be up to date before merging".
  - Leave "Do not allow bypassing the above settings" / "enforce for
    administrators" off, so an admin can merge past a red check during a
    catalog outage, and can merge their own pull requests.
- Students with the maintain role can re-run a failed check themselves. Pull
  requests from forks by first-time contributors need **Approve workflows to
  run** first.
- A pull request that changes the check script can make its own check pass;
  review changes to `check_objects.py`, `plan_check.py` and
  `check_folder_names.py` with care.
- When the go-live date is decided, set `STRICT_FROM` in `check_objects.py`.

## Running the check locally

The scripts use only the Python standard library and need Python 3.10 or
newer.

Check the changes on your branch against `origin/main`:

```
python check_objects.py
```

Use `--base REF` to compare with another ref, and `--offline-ok` to make an
unreachable catalog a warning instead of an error. `python check_objects.py
--help` lists the options.

### Pre-commit hook

`.pre-commit-config.yaml` runs `check_objects.py --staged --offline-ok` before
each commit. It runs the script with Python 3.11 through
[uv](https://docs.astral.sh/uv/), so both `pre-commit` and `uv` must be on
your `PATH`. To set it up once in your clone:

```
pre-commit install
```

The hook prints the report on every commit. An unreachable catalog is only a
warning here; in the GitHub check it is an error.

### Tests

```
uv run --python 3.11 --with pytest pytest tests/
```

The tests use recorded catalog answers and do not need the network.
