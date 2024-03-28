#!/usr/bin/env python3

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Mapping, Set

GITSTACK_FILE = os.environ.get("GITSTACK_FILE", ".gitstack")
TRUNK_CANDIDATES = ["main", "master"]

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def read_gitstack_file() -> Mapping[str, str]:
    if not Path(GITSTACK_FILE).is_file():
        return {}
    with open(GITSTACK_FILE) as f:
        return json.loads(f.read())


def write_gitstack_file(gitstack_contents: Mapping[str, str]):
    with open(GITSTACK_FILE, "w") as f:
        f.write(json.dumps(gitstack_contents))


class NoValidTrunkError(Exception):
    pass


class GitStack:
    def __init__(self) -> None:
        self.gitstack = read_gitstack_file()
        self.gitstack_children: Mapping[str, List[str]] = {}
        self.trunk = self._get_trunk()
        for branch, parent in self.gitstack.items():
            self.gitstack_children.setdefault(parent, []).append(branch)

    def operate(self, operation: str, args: List[str]) -> None:
        if operation in {"b", "branch"}:
            assert 1 <= len(args) <= 2
            branch = args[0]
            parent: str
            if len(args) > 1:
                parent = self._get_current_branch() if args[1] == "." else args[1]
            else:
                parent = self.trunk
            self.create_branch(branch, parent)
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
        write_gitstack_file(self.gitstack)

    def create_branch(self, branch: str, parent) -> None:
        subprocess.run(
            ["git", "checkout", "-b", branch, parent],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )
        self._track_branch(branch, parent)

    def track_current_branch(self, parent):
        branch = self._get_current_branch()
        self._track_branch(branch, parent)

    def switch_to_parent(self) -> None:
        current_branch = self._get_current_branch()
        if current_branch == self.trunk:
            print("Already on trunk")
            return
        if current_branch not in self.gitstack:
            print(
                f"Current branch {current_branch} not tracked by gst, use `gst t <parent>` and try again"
            )
            return
        parent = self.gitstack[self._get_current_branch()]
        subprocess.run(
            ["git", "switch", parent],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def switch_to_child(self) -> None:
        current_branch = self._get_current_branch()
        child_branches = self.gitstack_children.get(self._get_current_branch(), [])
        child_branch: str
        if len(child_branches) < 1:
            print(
                f"Current branch {current_branch} has no children, or isn't tracked by gst."
            )
            return
        if len(child_branches) == 1:
            child_branch = child_branches[0]
        else:
            print("Multiple child branches to choose from: ")
            for i, child_branch in enumerate(child_branches):
                print(f"{i}. {child_branch}")
            child_branch_idx = int(input("Select by number: "))
            child_branch = child_branches[child_branch_idx]

        subprocess.run(
            ["git", "switch", child_branch],
            check=True,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
        )

    def sync(self):
        visited = set([self.trunk])
        queue = [(self.trunk, 0)]

        while queue:
            branch, depth = queue.pop(0)
            if branch != self.trunk:
                self._check_and_rebase(branch)
            for child_branch in self.gitstack_children.get(branch, []):
                if child_branch in visited:
                    continue
                visited.add(child_branch)
                queue.append((child_branch, depth + 1))

    def _check_and_rebase(self, branch: str) -> None:
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

    def _get_parent(self, branch) -> str:
        return self._get_branch_stack(branch)[1]

    def _get_trunk(self):
        all_branches = self._list_all_branches()
        for trunk_candidate in TRUNK_CANDIDATES:
            if trunk_candidate in all_branches:
                return trunk_candidate
        raise NoValidTrunkError()

    def _get_branch_stack(self, branch: str) -> List[str]:
        stack = [branch]
        while branch in self.gitstack:
            branch = self.gitstack[branch]
            stack.append(branch)
        return stack

    def _list_all_branches(self) -> Set[str]:
        p = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            check=True,
            capture_output=True,
        )
        return set(p.stdout.decode().strip().split())

    def _get_current_branch(self) -> str:
        p = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
        )
        return p.stdout.strip().decode()

    def _track_branch(self, branch: str, parent: str) -> None:
        self.gitstack[branch] = parent
        self.gitstack_children.setdefault(parent, []).append(branch)


def parse_args() -> None:
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
