"""Identity refusal checks only, not Docker-host acceptance."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location("image_receipt", Path(__file__).resolve().parents[1] / "scripts/docker/image_receipt.py")
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
D = "sha256:" + "a" * 64
SOURCES = {name: "b" * 40 for name in m.REPOS}


def valid():
    return dict(schema=1, repository="ghcr.io/shyalon/require", manifest_digest=D,
                config_digest=D, platform="linux/amd64", sources=SOURCES,
                runtime_contract=D, acceptance_contract=D,
                checks={name: "passed" for name in m.CHECKS})


def read(value):
    return m.Receipt.read(json.dumps(value).encode(), sources=SOURCES,
                          runtime_contract=D, acceptance_contract=D)


def test_valid_digest_reference():
    assert read(valid()).reference == "ghcr.io/shyalon/require@" + D


@pytest.mark.parametrize("key,value", [
    ("schema", 2), ("platform", "linux/arm64"), ("repository", "docker.io/shy/require"),
    ("repository", "ghcr.io/shy/require:latest"), ("config_digest", "latest"),
    ("manifest_digest", "sha256:bad"), ("runtime_contract", "sha256:" + "c" * 64),
    ("acceptance_contract", "sha256:" + "c" * 64), ("checks", {}),
    ("checks", {name: True for name in m.CHECKS}), ("sources", {}),
    ("sources", {name: "c" * 40 for name in m.REPOS}),
])
def test_changed_or_incomplete_evidence_refused(key, value):
    body = valid(); body[key] = value
    with pytest.raises(m.InvalidReceipt): read(body)


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"schema":1,"schema":1}', b"null"])
def test_truncated_duplicate_or_nonobject_refused(raw):
    with pytest.raises(m.InvalidReceipt):
        m.Receipt.read(raw, sources=SOURCES, runtime_contract=D, acceptance_contract=D)


def test_extra_secret_field_refused():
    body = valid(); body["token"] = "fictional"
    with pytest.raises(m.InvalidReceipt): read(body)


@pytest.mark.parametrize("change", [{"Id": "sha256:" + "c" * 64}, {"Architecture": "arm64"}, {"Os": "windows"}, {"RepoDigests": []}])
def test_wrong_pulled_image_refused(change):
    receipt = read(valid())
    image = dict(Id=D, Os="linux", Architecture="amd64", RepoDigests=[receipt.reference])
    receipt.verify_loaded(image)
    image.update(change)
    with pytest.raises(m.InvalidReceipt): receipt.verify_loaded(image)


def manifest():
    return dict(schemaVersion=2, mediaType="application/vnd.oci.image.manifest.v1+json",
                config=dict(digest=D, size=123), layers=[dict(digest=D, size=234)])


def test_registry_exact_bytes_bound_to_tested_config():
    raw = json.dumps(manifest()).encode()
    m.validate_manifest(raw, m.digest(raw), D)
    with pytest.raises(m.InvalidReceipt): m.validate_manifest(raw + b" ", m.digest(raw), D)
    with pytest.raises(m.InvalidReceipt): m.validate_manifest(raw, m.digest(raw), "sha256:" + "c" * 64)


@pytest.mark.parametrize("change", [dict(mediaType="application/vnd.oci.image.index.v1+json"), dict(layers=[]), dict(config={}), dict(schemaVersion=1)])
def test_index_or_invalid_manifest_refused(change):
    body = manifest(); body.update(change); raw = json.dumps(body).encode()
    with pytest.raises(m.InvalidReceipt): m.validate_manifest(raw, m.digest(raw), D)


def test_atomic_receipt_write_with_space_path(tmp_path):
    path = tmp_path / "work space" / "receipt.json"
    raw = json.dumps(valid()).encode()
    m.atomic_write(path, raw)
    assert path.read_bytes() == raw
    assert not list(path.parent.glob(".receipt-*"))
