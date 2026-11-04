import importlib.util
from pathlib import Path
import sys
import pytest

spec = importlib.util.spec_from_file_location("runtime_spec", Path(__file__).resolve().parents[1] / "scripts/docker/runtime_spec.py")
m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)
ID = "sha256:" + "b" * 64


def target(**kwargs):
    return m.Target(**(dict(image_id=ID, name="owned-123", env_file="/private settings/service.env", port=8001, network="service") | kwargs))


def test_same_execution_options_for_both_targets():
    local = target().arguments()
    remote = target(name="service", env_file="/etc/requi/full.env", port=8000).arguments()
    # Only the values passed to Target differ; all execution options are fixed.
    assert local[5:-7] == remote[5:-7]
    assert local[-1] == remote[-1] == ID
    assert "--platform" in local and local[local.index("--platform")+1] == "linux/amd64"
    assert "--volume" not in local and "-v" not in local
    assert local[local.index("--env-file")+1] == "/private settings/service.env"
    assert local[local.index("--publish")+1] == "127.0.0.1:8001:8000"
    assert local[local.index("--user")+1] == "10001:10001"
    assert "--read-only" in local and "--restart" not in local


@pytest.mark.parametrize("change", [dict(image_id="requi:latest"), dict(image_id="sha256:bad"), dict(name="--evil"),
                                    dict(name="a;touch nope"), dict(port=True), dict(port=80), dict(port=65536),
                                    dict(network="x;pwd"), dict(env_file="relative"), dict(env_file="/x\nKEY=value")])
def test_untrusted_launch_values_refused(change):
    with pytest.raises(ValueError): target(**change).arguments()


def test_contract_stable_and_has_no_target_or_secret_values():
    raw = m.contract_bytes()
    assert b"linux/amd64" in raw and b"image-default" in raw
    assert b"service.env" not in raw and ID.encode() not in raw
    assert raw == m.contract_bytes()
