"""Pure identity/receipt checks for one privately published, locally tested image.

No Docker, network, credentials or deploy side effects. Callers own those steps.
A manifest digest hashes the original registry bytes, never reserialized JSON.
"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_./-]*\Z")
REPOS = {"delivery-api", "interview-engine", "spec-generator", "verification-loop"}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
CHECKS = {"startup", "signed-http", "child-lifecycle", "stop", "retained-outputs"}

class InvalidReceipt(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise InvalidReceipt(reason)


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def object_json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidReceipt("invalid JSON") from error
    require(type(value) is dict, "JSON object required")
    return value


def validate_manifest(raw: bytes, expected_digest: str, config_digest: str) -> dict:
    require(bool(DIGEST.fullmatch(expected_digest)), "invalid manifest digest")
    require(bool(DIGEST.fullmatch(config_digest)), "invalid config digest")
    require(digest(raw) == expected_digest, "registry manifest bytes changed")
    manifest = object_json(raw)
    require(manifest.get("schemaVersion") == 2, "unsupported manifest schema")
    require(manifest.get("mediaType") in MANIFEST_TYPES, "single-platform manifest required, not an index")
    config = manifest.get("config")
    require(type(config) is dict and config.get("digest") == config_digest, "manifest config differs from tested image")
    require(type(config.get("size")) is int and config["size"] > 0, "invalid config size")
    layers = manifest.get("layers")
    require(type(layers) is list and bool(layers), "image layers missing")
    for layer in layers:
        require(type(layer) is dict and bool(DIGEST.fullmatch(str(layer.get("digest", "")))), "invalid layer digest")
        require(type(layer.get("size")) is int and layer["size"] > 0, "invalid layer size")
    return manifest


@dataclass(frozen=True)
class Receipt:
    repository: str
    manifest_digest: str
    config_digest: str
    sources: dict[str, str]
    runtime_contract: str
    acceptance_contract: str

    @property
    def reference(self) -> str:
        return self.repository + "@" + self.manifest_digest

    @classmethod
    def read(cls, raw: bytes, *, sources: dict[str, str], runtime_contract: str,
             acceptance_contract: str) -> Receipt:
        value = object_json(raw)
        expected = {"schema", "repository", "manifest_digest", "config_digest", "platform",
                    "sources", "runtime_contract", "acceptance_contract", "checks"}
        require(set(value) == expected and value.get("schema") == 1, "receipt schema differs")
        require(type(value.get("repository")) is str and bool(REPOSITORY.fullmatch(value["repository"])), "private GHCR repository required")
        require(value.get("platform") == "linux/amd64", "linux/amd64 acceptance required")
        for key in ("manifest_digest", "config_digest", "runtime_contract", "acceptance_contract"):
            require(type(value.get(key)) is str and bool(DIGEST.fullmatch(value[key])), "invalid " + key)
        recorded = value.get("sources")
        require(type(recorded) is dict and set(recorded) == REPOS, "exact source tuple required")
        require(all(type(v) is str and bool(SHA.fullmatch(v)) for v in recorded.values()), "full source SHAs required")
        require(recorded == sources, "accepted source tuple changed")
        require(value["runtime_contract"] == runtime_contract, "runtime contract changed")
        require(value["acceptance_contract"] == acceptance_contract, "acceptance contract changed")
        require(value.get("checks") == {key: "passed" for key in CHECKS}, "required acceptance did not pass")
        return cls(value["repository"], value["manifest_digest"], value["config_digest"],
                   dict(recorded), runtime_contract, acceptance_contract)

    def verify_loaded(self, image: dict) -> None:
        require(image.get("Id") == self.config_digest, "loaded image differs from tested image")
        require(image.get("Os") == "linux" and image.get("Architecture") == "amd64", "loaded image platform differs")
        require(self.reference in image.get("RepoDigests", []), "loaded image manifest differs")


def atomic_write(path: Path, raw: bytes) -> None:
    """No successful receipt survives a partial write; no secrets go in receipts."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
