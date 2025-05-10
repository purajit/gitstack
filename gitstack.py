#!/usr/bin/env python3

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, List, MutableMapping, Set

GITSTACK_FILE = os.environ.get("GITSTACK_FILE", ".gitstack")
TRUNK_CANDIDATES = ["main", "master"]

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)


NC = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
GREY = "\033[90m"
BOLD = "\033[1m"


def read_gitstack_file() -> MutableMapping[str, str]:
    """Read the gitstack file at the current git root"""
    gitstack_path = git_get_root() / GITSTACK_FILE
    if not Path(gitstack_path).is_file():
        return {}
    with open(gitstack_path) as f:
        return json.loads(f.read())


def write_gitstack_file(gitstack_contents: MutableMapping[str, str]):
    """Write the gitstack contents back in"""
    gitstack_path = git_get_root() / GITSTACK_FILE
    with open(gitstack_path, "w") as f:
        f.write(json.dumps(gitstack_contents))


def git_get_root() -> Path:
    """List all locally checked out branches"""
    p = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
    )
    return Path(p.stdout.decode().strip())


def git_list_all_branches() -> Set[str]:
    """List all locally checked out branches"""
    p = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        check=True,
        capture_output=True,
    )
    return set(p.stdout.decode().strip().split())


def git_get_current_branch() -> str:
    """Get name of active branch"""
    p = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
    )
    return p.stdout.decode().strip()


def print_branch_level(
    branch: str, parent_branch: str | None, current_branch: str, depth: int
) -> None:
    """Prints a branch line as part of a tree"""
    branch_line = f"\u001b[32m{branch}\u001b[0m" if branch == current_branch else branch

    level_gap = " " * (2 * (depth - 1))
    print(f"{level_gap}↳ {branch_line}" if depth > 0 else branch_line)
    if parent_branch is not None:
        commits_in_branch = (
            subprocess.run(
                [
                    "git",
                    "log",
                    f"{parent_branch}..{branch}",
                    "--oneline",
                    "--no-merges",
                ],
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )
        if not commits_in_branch:
            print(f"{RED}{level_gap}  empty branch{NC}" if depth > 0 else branch_line)
            return
        for commit in reversed(commits_in_branch.split("\n")):
            commit_title = commit.split(" ", 1)[1]
            print(
                f"{GREY}{level_gap}  {commit_title}{NC}" if depth > 0 else branch_line
            )


class NoValidTrunkError(Exception):
    """Error for when none of the trunk candidates match"""


class UnhandledPRStateError(Exception):
    """Error for when none of the trunk candidates match"""


class GitStack:
    """Main GitStack implementation"""

    def __init__(self) -> None:
        self.stack_changed = False
        self.gitstack = read_gitstack_file()
        self.gitstack_children: MutableMapping[str, Set[str]] = {}
        self.trunk = self._get_trunk()
        self.original_branch = git_get_current_branch()
        for branch, parent in self.gitstack.items():
            self.gitstack_children.setdefault(parent, set()).add(branch)

    def operate(self, operation: str, args: List[str]) -> None:
        """Main entrypoint"""
        if operation in {"b", "branch"}:
            assert 1 <= len(args) <= 2
            branch = args[0]
            parent: str
            if len(args) > 1:
                parent = self.original_branch if args[1] == "." else args[1]
            else:
                parent = self.trunk
            self.create_branch(branch, parent)
        elif operation in {"p", "print"}:
            assert len(args) == 0
            self.print_stack()
        elif operation in {"d", "down"}:
            assert len(args) == 0
            self.switch_to_parent()
        elif operation in {"u", "up"}:
            assert len(args) == 0
            self.switch_to_child()
        elif operation in {"t", "track"}:
            assert len(args) == 1
            parent = args[0]
            self.track_current_branch(parent)
        elif operation in {"pr"}:
            assert len(args) == 0
            self.create_prs()
        elif operation in {"s", "sync"}:
            assert len(args) == 0
            self.sync()

    def wrapup(self) -> None:
        """Wrap up tasks - like rewriting .gitstack if anything changed"""
        if self.stack_changed:
            write_gitstack_file(self.gitstack)

    def print_stack(self):
        """Pretty print the entire stack"""
        local_branches = git_list_all_branches()
        self._traverse_stack(
            lambda branch, depth: print_branch_level(
                branch, self.gitstack.get(branch), self.original_branch, depth
            )
            if branch in local_branches
            else None
        )
        untracked_branches = local_branches - self.gitstack.keys() - {self.trunk}
        if untracked_branches:
            print()
            print(f"{RED}Branches not tracked by gitstack:{NC}")
            for untracked_branch in untracked_branches:
                print(f"* {untracked_branch}")

    def create_prs(self):
        """Submit the stack starting at the current branch going down"""
        branch = self.original_branch
        while branch != self.trunk:
            p = subprocess.run(
                [
                    "gh",
                    "pr",
                    "status",
                    "--json",
                    "state",
                    "--jq",
                    ".currentBranch",
                ],
                check=True,
                capture_output=True,
            )
            has_pr = bool(p.stdout.decode().strip())
            if has_pr:
                subprocess.run(
                    ["git", "push"],
                    check=True,
                    stdout=sys.stdout.buffer,
                    stderr=sys.stderr.buffer,
                )
            else:
                parent = self.gitstack[branch]
                subprocess.run(
                    ["git", "push"],
                    check=True,
                    stdout=sys.stdout.buffer,
                    stderr=sys.stderr.buffer,
                )
                subprocess.run(
                    ["gh", "pr", "create", "--base", parent, "--draft", "--fill"],
                    check=True,
                    stdout=sys.stdout.buffer,
                    stderr=sys.stderr.buffer,
                )
                print(f"{GREEN}Created PR for {BLUE}{branch}{NC}")

            branch = self.switch_to_parent()

        subprocess.run(
            ["git", "switch", "-q", self.original_branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def create_branch(self, branch: str, parent: str) -> None:
        """Create new branch and add to gitstack"""
        cmd = (
            ["git", "checkout", "-b", branch, parent]
            if parent
            else ["git", "checkout", "-b", branch]
        )
        subprocess.run(
            cmd,
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        self._track_branch(branch, parent)

    def track_current_branch(self, parent):
        """Add current branch to gitstack tracking"""
        if parent not in git_list_all_branches():
            print(f"{RED}Branch {BLUE}{parent}{RED} does not exist{NC}")
            return

        branch = self.original_branch
        if branch == parent:
            print("{RED}Branch cannot be its own parent{NC}")
            return
        if branch == self.trunk:
            print("{RED}Trunk cannot have a parent{NC}")
            return

        if branch in self.gitstack and parent == self.gitstack[branch]:
            print(
                f"{GREY}Parent of {BLUE}{branch}{GREY} is already {BLUE}{parent}{GREY}, no changes needed.{NC}"
            )
            return
        if branch in self.gitstack:
            response = input(
                f"{RED}This will switch the parent of {BLUE}{branch}{RED} from {self.gitstack[branch]} to {BLUE}{parent}{RED} (y/N) {NC}"
            )
            if response not in {"y", "Y"}:
                return
            # TODO: replay commits on different base branch

        self._track_branch(branch, parent)

    def switch_to_parent(self) -> str:
        """Go one step above the stack, closer to trunk"""
        current_branch = git_get_current_branch()
        if current_branch == self.trunk:
            print(f"{GREY}Already on trunk{NC}")
            return current_branch
        if current_branch not in self.gitstack:
            print(
                f"{RED}Current branch {current_branch} not tracked by gst, use `gst t <parent>` and try again{NC}"
            )
            return current_branch
        parent = self.gitstack[current_branch]
        subprocess.run(
            ["git", "switch", "-q", parent],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        return parent

    def switch_to_child(self) -> str:
        """Go one step deeper into the stack, further from trunk"""
        current_branch = git_get_current_branch()
        if current_branch != self.trunk and current_branch not in self.gitstack:
            print(f"{RED}Current branch {current_branch} isn't tracked by gst.{NC}")
            return current_branch

        child_branches = self.gitstack_children.get(current_branch, set())
        if len(child_branches) < 1:
            print(f"{RED}Current branch {current_branch} has no children.{NC}")
            return current_branch

        child_branch: str
        if len(child_branches) == 1:
            child_branch = next(iter(child_branches))
        else:
            print("Multiple child branches to choose from: ")
            child_branches_list = list(child_branches)
            for i, child_branch in enumerate(child_branches_list):
                print(f"{i}. {child_branch}")
            child_branch_idx = int(input("Select by number: "))
            child_branch = child_branches_list[child_branch_idx]

        subprocess.run(
            ["git", "switch", "-q", child_branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        return child_branch

    def sync(self):
        """Rebase/merge all branches on top of current trunk"""
        self._traverse_stack(lambda branch, depth: self._check_and_rebase(branch))
        # switch back to original branch once done, if it exists - it may have
        # been deleted in the process of sync
        if self.original_branch in git_list_all_branches():
            subprocess.run(
                ["git", "switch", "-q", self.original_branch],
                check=True,
                stdout=sys.stdout.buffer,
                stderr=sys.stderr.buffer,
            )

    def _traverse_stack(self, fn: Callable[[str, int], None]):
        """DFS through the gitstack from trunk, calling a function on each branch"""
        visited = set()
        tracking_stack = [(self.trunk, 0)]

        while tracking_stack:
            branch, depth = tracking_stack.pop()
            if branch in visited:
                continue
            fn(branch, depth)
            visited.add(branch)
            for child_branch in self.gitstack_children.get(branch, []):
                if child_branch in visited:
                    continue
                tracking_stack.append((child_branch, depth + 1))

    def _check_and_rebase(self, branch: str) -> None:
        """Evaluate a branch and decide what to do - merge/rebase, untrack, or remove"""
        if branch == self.trunk:
            return

        # if absent, remove from gitstack
        all_branches = git_list_all_branches()
        if branch not in all_branches:
            self._untrack_branch(branch)
            return

        # if merged, remove from gitstack
        subprocess.run(
            ["git", "switch", "-q", branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        p = subprocess.run(
            [
                "gh",
                "pr",
                "status",
                "--json",
                "state,isDraft",
                "--jq",
                ".currentBranch",
            ],
            check=True,
            capture_output=True,
        )
        pr_state_str = p.stdout.decode().strip()
        pr_state = json.loads(pr_state_str) if pr_state_str else {}
        if (state := pr_state.get("state")) in ("MERGED", "CLOSED"):
            if state == "MERGED":
                should_remove = input(
                    f"{RED}Branch {BLUE}{branch}{RED} has already been merged into master, delete local branch? (Y/n) {NC}"
                )
            elif state == "CLOSED":
                should_remove = input(
                    f"{RED}Branch {BLUE}{branch}{RED} has been closed, delete local branch? (Y/n) {NC}"
                )
            else:
                raise UnhandledPRStateError(f"{RED}Unknown state {state}{NC}")

            if should_remove in ("", "Y", "y"):
                self._delete_branch(branch)
                self._untrack_branch(branch)
            print()
            return

        parent = self.gitstack[branch]
        p = subprocess.run(
            ["git", "show-ref", "--heads", "-s", parent],
            check=True,
            capture_output=True,
        )
        current_parent_sha = p.stdout.decode().strip()
        p = subprocess.run(
            ["git", "merge-base", parent, branch], check=True, capture_output=True
        )
        current_base_sha = p.stdout.decode().strip()
        if current_parent_sha == current_base_sha:
            print(
                f"{GREEN}Branch up-to-date {BLUE}{branch}{GREEN} -> {BLUE}{parent}{NC}"
            )
            print()
            return
        if not pr_state or pr_state.get("isDraft"):
            rebase_commits = (
                subprocess.check_output(
                    ["git", "log", "--pretty=format:'%h %s'", f"{parent}.."]
                )
                .decode()
                .splitlines()
            )
            print(
                f"{YELLOW}Rebasing these commits in {BLUE}{branch}{YELLOW} onto {BLUE}{parent}:{NC}"
            )
            for rebase_commit in rebase_commits[::-1]:
                print(f"* {rebase_commit}")
            response = input(
                f"{YELLOW}Continue (no will drop into interactive rebase)? (Y/n) {NC}"
            )
            if response in {"n", "N"}:
                subprocess.run(["git", "rebase", "-i", parent], check=True)
            else:
                subprocess.run(["git", "rebase", parent], check=True)
        else:
            print(f"{YELLOW}Merging {BLUE}{parent}{YELLOW} into {BLUE}{branch}{NC}")
            subprocess.run(
                ["git", "merge", "-q", "--no-ff", "--no-edit", parent], check=True
            )
        print()

    def _get_trunk(self):
        """Get name of trunk based on possible candidates"""
        all_branches = git_list_all_branches()
        for trunk_candidate in TRUNK_CANDIDATES:
            if trunk_candidate in all_branches:
                return trunk_candidate
        raise NoValidTrunkError()

    def _delete_branch(self, branch: str) -> None:
        subprocess.run(
            ["git", "switch", "-q", self.trunk],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        subprocess.run(
            ["git", "branch", "-D", branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def _get_branch_stack(self, branch: str) -> List[str]:
        """Get the full path of the current stack [current_branch, parent, ..., trunk]"""
        stack = [branch]
        while branch in self.gitstack:
            branch = self.gitstack[branch]
            stack.append(branch)
        return stack

    def _track_branch(self, branch: str, parent: str) -> None:
        """Add one branches pointing to a specific parent"""
        self._track_branches([branch], parent)

    def _track_branches(self, branches: Iterable[str], parent: str) -> None:
        """Add multiple branches pointing to a specific parent all at once"""
        for branch in branches:
            self.gitstack[branch] = parent
        self.gitstack_children.setdefault(parent, set()).update(branches)
        self.stack_changed = True

    def _untrack_branch(self, branch: str) -> None:
        """Remove branch from gitstack, and handle its children"""
        parent = self.gitstack.pop(branch)
        children = self.gitstack_children.pop(branch, set())
        for current_children in self.gitstack_children.values():
            if branch in current_children:
                current_children.remove(branch)
        self._track_branches(children, parent)


def parse_args() -> argparse.Namespace:
    """Parse args"""
    parser = argparse.ArgumentParser(
        prog="gst",
        description="git stacks",
    )

    parser.add_argument("operation")
    parser.add_argument("args", type=str, nargs="*")
    return parser.parse_args()


if __name__ == "__main__":
    program_args = parse_args()
    gitstack = GitStack()
    gitstack.operate(program_args.operation, program_args.args)
    gitstack.wrapup()
