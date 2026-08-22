#!/usr/bin/env python3
"""Finalize a reproducible CycloneDX JSON document for attestation.

``cyclonedx-py --output-reproducible`` intentionally omits the per-run timestamp
and random serial number.  GitHub's SBOM attestation action requires a
``serialNumber`` to recognize CycloneDX input, so derive a stable UUID from the
canonical content rather than reintroducing randomness.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not permitted: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member: {key!r}")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def finalize_cyclonedx(path: Path) -> str:
    """Add a content-derived UUID serial number and atomically canonicalize *path*."""

    path = path.resolve()
    if not path.is_file():
        raise ValueError("CycloneDX input must be an existing JSON file")
    document = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(document, dict):
        raise ValueError("CycloneDX document must be a JSON object")
    if document.get("bomFormat") != "CycloneDX":
        raise ValueError("CycloneDX document must declare bomFormat='CycloneDX'")
    spec_version = document.get("specVersion")
    if not isinstance(spec_version, str) or not spec_version:
        raise ValueError("CycloneDX document must declare a non-empty specVersion")

    content = dict(document)
    content.pop("serialNumber", None)
    content_digest = sha256(_canonical_bytes(content)).hexdigest()
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"urn:sha256:{content_digest}")
    content["serialNumber"] = f"urn:uuid:{serial}"
    encoded = _canonical_bytes(content) + b"\n"

    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_raw)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return content["serialNumber"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="add a deterministic serial number to reproducible CycloneDX JSON"
    )
    parser.add_argument("document", type=Path)
    args = parser.parse_args()
    finalize_cyclonedx(args.document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
