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
    flat_config_exists: bool = False


@dataclass
class PlanCheck:
    plan_path: Path
    plan_contents: list[str] = None

    def __post_init__(self):
        with open(self.plan_path, "r") as f:
            self.plan_contents = f.readlines()
        self.plan_contents = [line for line in self.plan_contents if line.strip() and not line.startswith(";")]

    def is_flat_config(self):
        """Check whether the plan is a flat configuration file."""
        flat_line = re.compile(r"\d+,\w+,\d+")
        return all(flat_line.match(line) for line in self.plan_contents)

    def chains(self):
        """Check whether the plan chains to another plan."""
        return self.plan_contents[-1].startswith("#chain")

    def has_shutdown(self):
        """Check whether the plan has a #shutdown command."""
        return self.plan_contents[-1].startswith("#shutdown")

    def flats_config_file(self):
        """Check whether the plan uses a flats configuration file and returns that file name."""
        for line in self.plan_contents:
            if line.startswith("#duskflats") or line.startswith("#dawnflats"):
                return line.split(" ")[1].strip()
        return None


@dataclass
class PlanSetCheck:
    folder: Path
    first_plan: Path | None = None
    all_plans: dict[Path, PlanStatus] = None
    plan_status: dict[Path, PlanStatus] = None

    def __post_init__(self):
        self.all_plans = {p: PlanCheck(p) for p in self.folder.glob("*.txt")}
        self.first_plan = [p for p in self.all_plans if p.stem.startswith("1")][0]
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

            if plan.flats_config_file():
                self.plan_status[name].flat_config_exists = Path(self.folder / plan.flats_config_file()).exists()
                if self.plan_status[name].flat_config_exists:
                    self.plan_status[Path(self.folder / plan.flats_config_file())].is_used = True

            if self.plan_status[name].chains:
                next_plan = Path(self.folder / plan.plan_contents[-1].split(" ")[1].strip())
                self.plan_status[name].chain_exists = next_plan.exists()
                if self.plan_status[name].chain_exists:
                    self.plan_status[next_plan].is_used = True

            if name == self.first_plan:
                self.plan_status[name].is_used = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check the validity of a plan set.")
    parser.add_argument("folders", help="Folder containing the plan set")
    args = parser.parse_args()

    plan_set = PlanSetCheck(Path(args.folder))
    plan_set.check_plan_chains()

    for name, status in plan_set.plan_status.items():
        if not status.is_used:
            print(f"Plan {name} is not used.")
        if status.chains and not status.chain_exists:
            print(f"Plan {name} chains to a non-existent plan.")
        if status.is_flat and not status.flat_config_exists:
            print(f"Plan {name} uses a flats configuration file that does not exist.")