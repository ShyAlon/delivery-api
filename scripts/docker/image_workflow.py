#!/usr/bin/env python3
"""Owner-side exact-image workflow state and fail-fast preflight.

Preparation only until owned-service acceptance and GHCR publication are wired.
No server is contacted by this module. Never promotes partial state to a receipt.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from image_receipt import InvalidReceipt, atomic_write, digest, object_json
from runtime_spec import contract_bytes

REPOS = ("delivery-api", "interview-engine", "spec-generator", "verification-loop")
STAGES = ("preflight", "build", "local-acceptance", "publish", "verify-manifest", "receipt")


class WorkflowError(RuntimeError):
    pass


def log(level: str, message: str) -> None:
    colors = {"INFO": "36", "PASS": "32", "WARN": "33", "FAIL": "31"}
    prefix = "[" + level + "]"
    if sys.stderr.isatty() and "NO_COLOR" not in os.environ:
        prefix = "\033[" + colors[level] + "m" + prefix + "\033[0m"
    print(prefix + " " + message, file=sys.stderr, flush=True)


def command(args: list[str], *, cwd: Path | None = None) -> str:
    # Output is consumed as typed state. It may contain account config, so never
    # echo arbitrary subprocess output or include it in failure messages.
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkflowError("required command unavailable or timed out: " + args[0]) from error
    if result.returncode:
        raise WorkflowError("required command failed: " + args[0] + "; no state advanced")
    return result.stdout.strip()


def source_snapshot(root: Path) -> dict[str, str]:
    import re
    sources = {}
    for name in REPOS:
        path = root / name
        if not (path / ".git").exists():
            raise WorkflowError("missing source checkout: " + name)
        if command(["git", "status", "--porcelain", "--untracked-files=no"], cwd=path):
            raise WorkflowError("tracked source changes: " + name)
        sha = command(["git", "rev-parse", "HEAD"], cwd=path)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise WorkflowError("full source revision unavailable: " + name)
        sources[name] = sha
    return sources


def preflight(root: Path, *, check_tools: bool = True) -> dict:
    if check_tools:
        for name in ("git", "docker", "openssl"):
            if not shutil.which(name):
                raise WorkflowError("missing local tool: " + name)
    sources = source_snapshot(root)
    if check_tools:
        command(["docker", "info", "--format", "{{.OSType}}"])
        command(["docker", "buildx", "version"])
    required = ("Dockerfile.full", "scripts/docker/runtime_spec.py", "scripts/ops/acceptance.sh")
    for name in required:
        if not (root / "delivery-api" / name).is_file():
            raise WorkflowError("missing workflow input: " + name)
    return {"sources": sources, "platform": "linux/amd64", "runtime_contract": digest(contract_bytes())}


class StageState:
    """Resume evidence is keyed by input contracts, not saved success booleans."""
    def __init__(self, path: Path, inputs: dict):
        self.path = path
        self.inputs = inputs
        self.key = digest(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode())
        self.state = {"schema": 1, "input_key": self.key, "stages": {}}
        if path.exists():
            value = object_json(path.read_bytes())
            if set(value) != {"schema", "input_key", "stages"} or value["schema"] != 1 or type(value["stages"]) is not dict:
                raise WorkflowError("invalid resume state; inspect it before retrying")
            if value["input_key"] != self.key:
                raise WorkflowError("inputs changed; choose a fresh run directory")
            if set(value["stages"]) - set(STAGES):
                raise WorkflowError("unknown stage in resume state")
            self.state = value

    def reusable(self, name: str, verify) -> bool:
        if name not in STAGES:
            raise WorkflowError("unknown workflow stage")
        evidence = self.state["stages"].get(name)
        if evidence is None:
            return False
        try:
            return verify(evidence) is True
        except (ValueError, WorkflowError, KeyError, TypeError):
            return False

    def complete(self, name: str, evidence: dict) -> None:
        if name not in STAGES or type(evidence) is not dict:
            raise WorkflowError("stage needs typed evidence")
        index = STAGES.index(name)
        if any(prior not in self.state["stages"] for prior in STAGES[:index]):
            raise WorkflowError("cannot skip a required workflow stage")
        # Any rerun invalidates downstream outputs, including the final receipt.
        for later in STAGES[index + 1:]:
            self.state["stages"].pop(later, None)
        self.state["stages"][name] = evidence
        atomic_write(self.path, json.dumps(self.state, sort_keys=True, indent=2).encode())


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repos-root", type=Path, default=Path(__file__).resolve().parents[3])
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--review", action="store_true", help="check inputs only; never build, publish or contact a server")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        log("INFO", "stage 1/6 preflight: local tools and four clean source revisions")
        inputs = preflight(args.repos_root.resolve())
        state = StageState(args.run_dir / "state.json", inputs)
        state.complete("preflight", inputs)
        log("PASS", "preflight passed; platform linux/amd64, one shared runtime contract")
        if args.review:
            log("INFO", "review only: nothing built, published, started or deployed")
            return 0
        raise WorkflowError("exact-image local-service acceptance is not wired yet; no build/publication/deploy attempted")
    except (WorkflowError, InvalidReceipt) as error:
        log("FAIL", str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
