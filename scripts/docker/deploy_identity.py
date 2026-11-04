"""Side-effect-free owner deployment review. No SSH, token collection or pull.

All deploy implementations must consume this reviewed immutable reference and
shared runtime specification. A local receipt is evidence, not deploy authority.
"""
from __future__ import annotations
import json
from pathlib import Path
from image_receipt import Receipt, InvalidReceipt, validate_manifest
from runtime_spec import Target


def review(raw: bytes, *, sources: dict, runtime_contract: str, acceptance_contract: str,
           repository: str, manifest: bytes, image: dict, name: str, env_file: str,
           port: int, network: str) -> dict:
    receipt=Receipt.read(raw,sources=sources,runtime_contract=runtime_contract,
                         acceptance_contract=acceptance_contract)
    if receipt.repository!=repository:raise InvalidReceipt('deployment repository differs from accepted audience')
    validate_manifest(manifest,receipt.manifest_digest,receipt.config_digest)
    receipt.verify_loaded(image)
    # No tag, rebuilding, architecture switch or fallback survives this planner.
    args=Target(receipt.config_digest,name,env_file,port,network).arguments()
    return {'reference':receipt.reference,'image_id':receipt.config_digest,'platform':'linux/amd64',
            'docker_arguments':args,'apply_authorized':False}
