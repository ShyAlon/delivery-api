#!/usr/bin/env python3
"""Owner-side exact-image workflow state and fail-fast preflight.

Preparation only until owned-service acceptance and GHCR publication are wired.
No server is contacted by this module. Never promotes partial state to a receipt.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tempfile
import tarfile

# Keep one module identity when invoked as the owner script; helpers import it.
if __name__ == "__main__":
    sys.modules.setdefault("image_workflow", sys.modules[__name__])

from image_receipt import InvalidReceipt, atomic_write, digest, object_json
from runtime_spec import contract_bytes

REPOS = ("delivery-api", "interview-engine", "spec-generator", "verification-loop")
STAGES = ("preflight", "build", "local-acceptance", "publish", "verify-manifest", "receipt")


class WorkflowError(RuntimeError):
    pass


STARTED = time.monotonic()


def log(level: str, message: str) -> None:
    colors = {"INFO": "36", "PASS": "32", "WARN": "33", "FAIL": "31"}
    prefix = "[" + level + "]"
    if sys.stderr.isatty() and "NO_COLOR" not in os.environ:
        prefix = "\033[" + colors[level] + "m" + prefix + "\033[0m"
    print(prefix + " elapsed=" + str(round(time.monotonic()-STARTED,1)) + "s " + message, file=sys.stderr, flush=True)


def command(args: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
    # Output is consumed as typed state. It may contain account config, so never
    # echo arbitrary subprocess output or include it in failure messages.
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkflowError("required command unavailable or timed out: " + args[0]) from error
    if result.returncode:
        reason="required command failed: " + args[0] + "; exit code " + str(result.returncode) + "; no state advanced"
        if not result.stdout.strip() and not result.stderr.strip():
            reason+="; produced no output, possible signal kill or out of memory"
        raise WorkflowError(reason)
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
    return {"sources": sources, "platform": "linux/amd64", "runtime_contract": digest(contract_bytes()),
            "acceptance_contract": acceptance_contract(root/"delivery-api")}


def acceptance_contract(root: Path) -> str:
    # Covers orchestration, schema/roles, external fixtures and the same owner
    # acceptance entry. Any changed behavior invalidates earlier local evidence.
    fixed=("scripts/docker/image_acceptance.py","scripts/docker/local_image_run.py",
           "scripts/docker/local_service_fixture.py","scripts/docker/fixture_services.py",
           "scripts/docker/retained_image_proof.py","scripts/docker/Dockerfile.local-fixture",
           "scripts/docker/image-local-acceptance.py","scripts/docker/private_backoffice.py",
           "scripts/docker/local_image_preview.py","scripts/ops/browser_smoke.py","scripts/owner/deepseek-acceptance.py",
           "scripts/ops/acceptance.sh","tests/pg_harness.py","src/delivery_api/runtime_roles.py")
    files=sorted([root/path for path in fixed]+list((root/"supabase/migrations").glob("*.sql")))
    h=hashlib.sha256()
    for path in files:
        if not path.is_file():raise WorkflowError("missing acceptance contract input")
        name=str(path.relative_to(root)).encode();raw=path.read_bytes()
        h.update(len(name).to_bytes(8,"big"));h.update(name)
        h.update(len(raw).to_bytes(8,"big"));h.update(raw)
    return "sha256:"+h.hexdigest()


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


def inspect_image(image: str) -> dict:
    from image_receipt import object_json
    raw = command(["docker", "image", "inspect", "--format", "{{json .}}", image])
    value = object_json(raw.encode())
    import re
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(value.get("Id", ""))):
        raise WorkflowError("Docker did not return an immutable image ID")
    if value.get("Os") != "linux" or value.get("Architecture") != "amd64":
        raise WorkflowError("candidate is not linux/amd64")
    return value


def build(root: Path, state: StageState) -> dict:
    """Build only locally. Source archives pin commits, not mutable directories."""
    def valid(evidence):
        image = inspect_image(evidence["image_id"])
        return image["Id"] == evidence["image_id"] and evidence["sources"] == state.inputs["sources"]
    if state.reusable("build", valid):
        log("PASS", "stage 2/6 reused: immutable local image revalidated")
        return state.state["stages"]["build"]
    # Any absent/invalidated image requires a fresh build, never remote fallback.
    sources = source_snapshot(root)
    if sources != state.inputs["sources"]:
        raise WorkflowError("source changed after preflight")
    with tempfile.TemporaryDirectory(prefix="requi-image-build-") as temporary:
        context = Path(temporary)
        for name, sha in sources.items():
            archive = context / (name + ".tar")
            command(["git", "archive", "--format=tar", "--output=" + str(archive), sha], cwd=root/name)
            target = context/name
            target.mkdir()
            with tarfile.open(archive) as stream:
                # Git archive comes from the reviewed source snapshot; reject links
                # rather than let them escape the minimal context.
                members = stream.getmembers()
                if any(item.issym() or item.islnk() for item in members):
                    raise WorkflowError("source archive contains links: " + name)
                stream.extractall(target, members=members, filter="data")
            archive.unlink()
        # Context readability must not depend on a private owner umask. Only
        # committed source bytes are present, never settings or credentials.
        for source in context.rglob('*'):
            if source.is_dir():source.chmod(0o755)
            elif source.is_file():source.chmod(0o755 if source.stat().st_mode&0o111 else 0o644)
        tag = "requi-full-candidate:" + state.key.split(":")[1][:24]
        revisions = ",".join(name + "=" + sha for name, sha in sources.items())
        log("INFO", "stage 2/6 build: linux/amd64 candidate locally, no provider keys or server")
        command(["docker", "build", "--platform", "linux/amd64", "--build-arg", "REQUI_BUILD_ID=" + state.key,
                 "--build-arg", "REQUI_BUILD_REPOS=" + revisions, "--build-arg",
                 "REQUI_BUILD_AT=" + datetime.datetime.now(datetime.timezone.utc).isoformat(), "--file", str(context/"delivery-api"/"Dockerfile.full"),
                 "--tag", tag, str(context)], timeout=7200)
        image = inspect_image(tag)
    evidence = {"image_id": image["Id"], "sources": sources, "platform": "linux/amd64"}
    state.complete("build", evidence)
    log("PASS", "local immutable candidate built; acceptance still required")
    return evidence


def local_acceptance(root: Path, state: StageState, evidence: dict) -> dict:
    """Run the same immutable service with external owned local dependencies."""
    from local_image_run import OwnedRun
    image_id=evidence["image_id"]
    if inspect_image(image_id)["Id"]!=image_id:
        raise WorkflowError("acceptance image identity differs")
    # Never reuse a successful boolean from a torn-down fixture run. Each call
    # proves the current exact image anew; build stage is independently reusable.
    # An old receipt must never outlive a new failed acceptance attempt.
    (state.path.parent/"accepted-image.json").unlink(missing_ok=True)
    log("INFO", "stage 3/6 local acceptance: owned services, signed HTTP, real child evidence")
    run=OwnedRun(root/"delivery-api",image_id)
    try:
        run.start()
        result=run.http_acceptance(state.path.parent/"local-http-evidence.json")
    finally:
        run.close()
    required={"startup":"passed","signed-http":"passed","child-lifecycle":"passed",
              "stop":"passed","retained-outputs":"passed"}
    if result.get("signed-http")!="passed" or result.get("interview-start-answer-stop")!="passed":
        raise WorkflowError("local HTTP lifecycle evidence incomplete")
    proof=result.get("retained",{})
    if proof.get("child-lifecycle")!="passed" or proof.get("retained-outputs")!="passed":
        raise WorkflowError("retained child/output acceptance incomplete")
    if result.get("active-stop",{}).get("stop")!="passed" or result["active-stop"].get("active-call-observed") is not True:
        raise WorkflowError("active-call Stop acceptance incomplete")
    accepted={"image_id":image_id,"checks":required,"runtime_contract":state.inputs["runtime_contract"]}
    state.complete("local-acceptance",accepted)
    log("PASS", "exact local image accepted; no registry publication or deployment yet")
    return accepted


def receipt(state: StageState, publication: dict) -> dict:
    from image_receipt import Receipt
    accepted=state.state["stages"].get("local-acceptance",{})
    if accepted.get("image_id")!=publication.get("config_digest"):
        raise WorkflowError("registry config differs from local acceptance")
    value={"schema":1,**publication,"sources":state.inputs["sources"],
           "runtime_contract":state.inputs["runtime_contract"],
           "acceptance_contract":state.inputs["acceptance_contract"],"checks":accepted.get("checks")}
    raw=json.dumps(value,sort_keys=True,indent=2).encode()
    Receipt.read(raw,sources=state.inputs["sources"],runtime_contract=state.inputs["runtime_contract"],
                 acceptance_contract=state.inputs["acceptance_contract"])
    atomic_write(state.path.parent/"accepted-image.json",raw)
    state.complete("receipt",{"manifest_digest":publication["manifest_digest"],"config_digest":publication["config_digest"]})
    log("PASS","stage 6/6 exact-image receipt written; deployment is a separate owner action")
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repos-root", type=Path, default=Path(__file__).resolve().parents[3])
    p.add_argument("--run-dir", type=Path, help="resume directory, defaults to a source/contract keyed private local directory")
    p.add_argument("--backoffice-port", type=int, default=8020)
    p.add_argument("--preview", action="store_true", help="review accepted local image with existing private settings; --apply starts it")
    p.add_argument("--deploy", metavar="PRIVATE_GHCR_REPOSITORY", help="review an accepted manifest; no SSH unless --apply")
    p.add_argument("--apply", action="store_true", help="owner-confirmed deploy apply only")
    p.add_argument("--previous-image-id", help="reviewed previous image config digest paired with previous container ID")
    p.add_argument("--previous-container-id", help="exact reviewed old container ID for the first ownership transition only")
    p.add_argument("--host", help="explicit reviewed SSH user@host for deployment")
    p.add_argument("--name", help="container name; defaults by preview/deploy target")
    p.add_argument("--env-file", help="existing private settings path; default by target")
    p.add_argument("--port", type=int, help="loopback host port; default by target")
    p.add_argument("--network", default="bridge")
    p.add_argument("--publish", metavar="PRIVATE_GHCR_REPOSITORY", help="explicitly publish after local acceptance; existing private package and login required")
    p.add_argument("--accept-local", action="store_true", help="build/reuse and exercise exact image with owned local services only")
    p.add_argument("--build", action="store_true", help="build locally only; does not grant acceptance, publication or deployment")
    p.add_argument("--review", action="store_true", help="check inputs only; never build, publish or contact a server")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.apply and not (args.deploy or args.preview):raise WorkflowError('--apply requires --deploy or --preview; no action inferred')
        if sum(bool(v) for v in (args.review,args.build,args.accept_local,args.publish,args.deploy,args.preview))!=1:
            raise WorkflowError('choose exactly one review/build/accept-local/publish/deploy stage goal')
        log("INFO", "stage 1/6 preflight: local tools and four clean source revisions")
        inputs = preflight(args.repos_root.resolve())
        run_dir=args.run_dir or Path.home()/".local/share/requi/image-workflow"/digest(json.dumps(inputs,sort_keys=True).encode()).split(":")[1][:24]
        state = StageState(run_dir / "state.json", inputs)
        if not state.reusable("preflight", lambda evidence: evidence == inputs):
            state.complete("preflight", inputs)
        log("PASS", "preflight passed; platform linux/amd64, one shared runtime contract")
        args.name=args.name or ('requi-preview-exec' if args.preview else 'requi-delivery')
        args.port=args.port or (8001 if args.preview else 8000)
        args.env_file=args.env_file or (str(Path.home()/'.config/requi/preview.env') if args.preview else '/etc/requi/full.env')
        if args.preview:
            from local_image_preview import preview
            preview(state,env_file=args.env_file,name=args.name,port=args.port,network=args.network,backoffice_port=args.backoffice_port,apply=args.apply)
            return 0
        if args.deploy:
            if not args.host:raise WorkflowError('deploy needs an explicit reviewed host')
            from owner_image_deploy import deploy
            deploy(state=state,repository=args.deploy,host=args.host,name=args.name,env_file=args.env_file,
                   port=args.port,network=args.network,previous_container_id=args.previous_container_id,previous_image_id=args.previous_image_id,apply=args.apply)
            return 0
        if args.review:
            log("INFO", "review only: nothing built, published, started or deployed")
            return 0
        if args.build or args.accept_local or args.publish:
            log('INFO','selected goal may build an amd64 image and owned local test services; no live database or paid provider')
            if input('Type yes to run this local image stage: ').strip()!='yes':
                raise WorkflowError('local image stage declined; nothing built or started')
            evidence=build(args.repos_root.resolve(), state)
            if args.accept_local or args.publish:
                accepted=local_acceptance(args.repos_root.resolve(),state,evidence)
                if args.publish:
                    from private_publication import publish
                    publication=publish(args.publish,state,accepted)
                    receipt(state,publication)
            return 0
        raise WorkflowError("exact-image local-service acceptance is not wired yet; no build/publication/deploy attempted")
    except (WorkflowError, InvalidReceipt) as error:
        log("FAIL", str(error))
        log("INFO", "No automatic retry or alternate build path. Fix the named input, rerun the same command and run directory; completed image stages are revalidated.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
