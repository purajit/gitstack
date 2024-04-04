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


def read_gitstack_file() -> MutableMapping[str, str]:
    """Read the gitstack file at the current git root"""
    gitstack_path = git_get_root() / GITSTACK_FILE
    if not Path(GITSTACK_FILE).is_file():
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


class NoValidTrunkError(Exception):
    """Error for when none of the trunk candidates match"""


class GitStack:
    """Main GitStack implementation"""

    def __init__(self) -> None:
        self.stack_changed = False
        self.gitstack = read_gitstack_file()
        self.gitstack_children: MutableMapping[str, Set[str]] = {}
        self.trunk = self._get_trunk()
        for branch, parent in self.gitstack.items():
            self.gitstack_children.setdefault(parent, set()).add(branch)

    def operate(self, operation: str, args: List[str]) -> None:
        """Main entrypoint"""
        if operation in {"b", "branch"}:
            assert 1 <= len(args) <= 2
            branch = args[0]
            parent: str
            if len(args) > 1:
                parent = git_get_current_branch() if args[1] == "." else args[1]
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
        elif operation in {"s", "sync"}:
            assert len(args) == 0
            self.sync()

    def wrapup(self) -> None:
        """Wrap up tasks - like rewriting .gitstack if anything changed"""
        if self.stack_changed:
            write_gitstack_file(self.gitstack)

    def print_stack(self):
        """Pretty print the entire stack"""
        self._traverse_stack(
            lambda branch, depth: print(" " * (2 * (depth - 1)) + "↳ " + branch)
            if depth > 0
            else print(branch)
        )

    def create_branch(self, branch: str, parent) -> None:
        """Create new branch and add to gitstack"""
        subprocess.run(
            ["git", "checkout", "-b", branch, parent],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        self._track_branch(branch, parent)

    def track_current_branch(self, parent):
        """Add current branch to gitstack tracking"""
        if parent not in git_list_all_branches():
            print(f"Branch {parent} does not exist")
            return

        branch = git_get_current_branch()
        if branch == parent:
            print("Branch cannot be its own parent")
            return
        if branch == self.trunk:
            print("Trunk cannot have a parent")
            return

        if branch in self.gitstack and parent == self.gitstack[branch]:
            print(f"Parent of {branch} is already {parent}, no changes needed.")
            return
        if branch in self.gitstack:
            response = input(
                f"This will switch the parent of {branch} from {self.gitstack[branch]} to {parent} (y/N) "
            )
            if response not in {"y", "Y"}:
                return
            # TODO: replay commits on different base branch

        self._track_branch(branch, parent)

    def switch_to_parent(self) -> None:
        """Go one step above the stack, closer to trunk"""
        current_branch = git_get_current_branch()
        if current_branch == self.trunk:
            print("Already on trunk")
            return
        if current_branch not in self.gitstack:
            print(
                f"Current branch {current_branch} not tracked by gst, use `gst t <parent>` and try again"
            )
            return
        parent = self.gitstack[git_get_current_branch()]
        subprocess.run(
            ["git", "switch", parent],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def switch_to_child(self) -> None:
        """Go one step deeper into the stack, further from trunk"""
        current_branch = git_get_current_branch()
        if current_branch != self.trunk and current_branch not in self.gitstack:
            print(f"Current branch {current_branch} isn't tracked by gst.")
            return

        child_branches = self.gitstack_children.get(git_get_current_branch(), set())
        if len(child_branches) < 1:
            print(f"Current branch {current_branch} has no children.")
            return

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
            ["git", "switch", child_branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def sync(self):
        """Rebase all branches on top of current trunk"""
        self._traverse_stack(lambda branch, depth: self._check_and_rebase(branch))

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
        """Evaluate a branch and decide what to do - rebase, untrack, or remove"""
        if branch == self.trunk:
            return

        # if absent, remove from gitstack
        all_branches = git_list_all_branches()
        if branch not in all_branches:
            self._untrack_branch(branch)

        # if merged, remove from gitstack
        subprocess.run(
            ["git", "switch", branch],
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
                "state",
                "--jq",
                ".currentBranch.state",
            ],
            check=True,
            capture_output=True,
        )
        pr_state = p.stdout.decode().strip()
        if pr_state in ("MERGED", "CLOSED"):
            if pr_state == "MERGED":
                should_remove = input(
                    f"Branch {branch} has already been merged into master, delete local branch? (Y/n) "
                )
            elif pr_state == "MERGED":
                should_remove = input(
                    f"Branch {branch} has been closed, delete local branch? (Y/n) "
                )
            else:
                raise Exception(f"Unknown state {pr_state}")

            if should_remove in ("", "Y", "y"):
                self._delete_branch(branch)
                self._untrack_branch(branch)
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
            logger.info(
                "Branch %s doesn't need to be rebased on top of %s", branch, parent
            )
            return
        logger.info("Rebasing %s on top of %s", branch, parent)
        subprocess.run(["git", "rebase", parent, branch], check=True)

    def _get_trunk(self):
        """Get name of trunk based on possible candidates"""
        all_branches = git_list_all_branches()
        for trunk_candidate in TRUNK_CANDIDATES:
            if trunk_candidate in all_branches:
                return trunk_candidate
        raise NoValidTrunkError()

    def _delete_branch(self, branch: str) -> None:
        subprocess.run(
            ["git", "switch", self.trunk],
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
        for children in self.gitstack_children.values():
            if branch in children:
                children.remove(branch)
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
