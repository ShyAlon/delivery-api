"""One container runtime specification. Target differences are typed values only."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

CONTRACT_VERSION = 2
# Fixed options are shared by preview, owned acceptance and server deployment.
OPTIONS = (
    "--platform", "linux/amd64", "--user", "10001:10001", "--read-only",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--pids-limit", "512", "--memory", "3g", "--memory-swap", "3g",
    "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
    "--env", "DELIVERY_HOST=0.0.0.0", "--env", "DELIVERY_PORT=8000",
    "--env", "HOME=/tmp/control-plane", "--env", "XDG_DATA_HOME=/tmp/control-plane",
    "--env", "VMODEL_ARTIFACTS_DIR=/tmp/artifacts",
)


@dataclass(frozen=True)
class Target:
    image_id: str
    name: str
    env_file: str
    port: int
    network: str
    service_mount: str | None = None
    publish: bool = True

    @property
    def origin(self):return "http://127.0.0.1:"+str(self.port)

    @property
    def binding(self):return "127.0.0.1:"+str(self.port)+":8000"

    def arguments(self) -> list[str]:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id):
            raise ValueError("immutable tested image ID required")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", self.name):
            raise ValueError("invalid owned container name")
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("unprivileged host port required")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", self.network):
            raise ValueError("explicit network required")
        if not PurePosixPath(self.env_file).is_absolute() or any(c in self.env_file for c in "\n\r\0"):
            raise ValueError("absolute settings path required")
        mounts = []
        if self.service_mount is not None:
            if not re.fullmatch(r"requi-fixture-[a-f0-9]{24}", self.service_mount):
                raise ValueError("invocation-owned external service volume required")
            mounts = ["--mount", "type=volume,source=" + self.service_mount + ",target=/fixture,readonly"]
        if type(self.publish) is not bool:raise ValueError("explicit publication required")
        publication=["--publish",self.binding] if self.publish else []
        return ["run", "--detach", "--name", self.name, *OPTIONS,
                "--network", self.network, *mounts, "--env-file", self.env_file,
                *publication, self.image_id]


def contract_bytes() -> bytes:
    import json
    return json.dumps({"version": CONTRACT_VERSION, "options": OPTIONS,
                       "publish": "127.0.0.1:PORT:8000", "command": "image-default"},
                      sort_keys=True, separators=(",", ":")).encode()
