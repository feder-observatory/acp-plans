import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import NamedTuple


@dataclass
class PlanStatus:
    shuts_down: bool = False
    chains: bool = False
    chain_exists: bool = False
    is_flat: bool = False
    is_used: bool = False
    calls_flat: bool = False
    flat_config_exists: bool = False
    chain_name_missing: bool = False


FLAT_DIRECTIVES = ("#duskflats", "#dawnflats", "#screenflats")


def has_directive(line, directives):
    """Whether a plan line starts with one of the directives; ACP accepts any letter case."""
    return line.lower().startswith(directives)


@dataclass
class PlanCheck:
    plan_path: Path
    plan_contents: list[str] = None

    def __post_init__(self):
        with open(self.plan_path, "r", encoding="utf-8-sig") as f:
            self.plan_contents = f.readlines()
        self.plan_contents = [line for line in self.plan_contents if line.strip() and not line.startswith(";")]

    def is_flat_config(self):
        """Check whether the plan is a flat configuration file."""
        flat_line = re.compile(r"\d+,\w+,\d+")
        return all(flat_line.match(line) for line in self.plan_contents)

    def is_empty(self):
        """Check whether the plan has no lines other than comments and blank lines."""
        return not self.plan_contents

    def chains(self):
        """Check whether the plan chains to another plan."""
        return not self.is_empty() and has_directive(self.plan_contents[-1], "#chain")

    def chain_target(self):
        """The file name after the final #chain, or None if there is none."""
        words = self.plan_contents[-1].split()
        return words[1] if len(words) > 1 else None

    def has_shutdown(self):
        """Check whether the plan has a #shutdown command."""
        return not self.is_empty() and has_directive(self.plan_contents[-1], "#shutdown")

    def calls_flat(self):
        """Check whether the plan calls a flat configuration file."""
        return any(has_directive(line, FLAT_DIRECTIVES) for line in self.plan_contents)

    def flats_config_file(self):
        """Check whether the plan uses a flats configuration file and returns that file name."""
        for line in self.plan_contents:
            if has_directive(line, FLAT_DIRECTIVES):
                words = line.split()
                return words[1] if len(words) > 1 else False
        return None


@dataclass
class PlanSetCheck:
    folder: Path
    first_plan: Path | None = None
    all_plans: dict[Path, PlanStatus] = None
    plan_status: dict[Path, PlanStatus] = None

    def __post_init__(self):
        # Only regular files: symlinks are never followed and folders never opened.
        self.all_plans = {p: PlanCheck(p) for p in sorted(self.folder.iterdir())
                          if p.name.lower().endswith(".txt")
                          and p.is_file() and not p.is_symlink()}
        try:
            self.first_plan = [p for p in self.all_plans if p.stem.startswith("1")][0]
        except IndexError:
            raise RuntimeError("No plan in the plan set starts with 1. The first plan "
                               "should start with the number '1'.")
        self.plan_status = {p: PlanStatus() for p in self.all_plans}

    def check_plan_chains(self):
        """"
        Check that each plan has a #chain or a #shutdown. If there is a #chain,
        check that the next plan exists.
        """
        self.unused_plans = []
        for name, plan in self.all_plans.items():
            self.plan_status[name].chains = plan.chains()
            self.plan_status[name].shuts_down = plan.has_shutdown()
            self.plan_status[name].is_flat = plan.is_flat_config()

            if plan.calls_flat():
                self.plan_status[name].calls_flat = True
                if plan.flats_config_file():
                    flat_config = Path(self.folder / plan.flats_config_file())
                    self.plan_status[name].flat_config_exists = flat_config.exists()
                    if self.plan_status[name].flat_config_exists and flat_config in self.plan_status:
                        self.plan_status[flat_config].is_used = True

            if self.plan_status[name].chains and plan.chain_target() is None:
                self.plan_status[name].chain_name_missing = True
            elif self.plan_status[name].chains:
                next_plan = Path(self.folder / plan.chain_target())
                self.plan_status[name].chain_exists = next_plan.exists()
                if self.plan_status[name].chain_exists and next_plan in self.plan_status:
                    self.plan_status[next_plan].is_used = True

            if name == self.first_plan:
                self.plan_status[name].is_used = True


def find_problems(folder):
    """Return a list of the chaining problems in the plan set in folder ([] if valid)."""
    try:
        plan_set = PlanSetCheck(Path(folder))
    except RuntimeError as exc:
        return [str(exc)]
    plan_set.check_plan_chains()

    errors = []
    for name, status in plan_set.plan_status.items():
        if plan_set.all_plans[name].is_empty():
            errors.append(f"Plan {name} is empty: it has no lines other than comments and "
                          "blank lines.")
        if not status.is_used:
            errors.append(f"Plan {name} is not used.")
        if status.chain_name_missing:
            errors.append(f"Plan {name} has a #chain line with no plan file name.")
        elif status.chains and not status.chain_exists:
            errors.append(f"Plan {name} chains to a non-existent plan.")
        if status.calls_flat and not status.flat_config_exists:
            errors.append(f"Plan {name} calls a non-existent flat configuration file.")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the validity of a plan set.")
    parser.add_argument("folder", help="Folder containing the plan set")
    args = parser.parse_args()

    errors = find_problems(args.folder)
    if errors:
        raise RuntimeError("Plan set is not valid. The problems are:\n\t" + "\n\t".join(errors))
    else:
        print("Plan set is valid.")
