#!/usr/bin/env python3
"""Rewrite a setuptools sdist with deterministic archive metadata.

Setuptools currently emits source archives whose gzip and tar timestamps reflect
the wall clock even when ``SOURCE_DATE_EPOCH`` is set.  The file contents are
already deterministic; this small release-only tool normalizes the container
metadata without changing the source tree represented by the archive.
"""

from __future__ import annotations

import argparse
from copy import copy
import gzip
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


def _safe_name(raw: str) -> str:
    name = PurePosixPath(raw)
    if name.is_absolute() or not name.parts or ".." in name.parts:
        raise ValueError(f"unsafe sdist member path: {raw!r}")
    normalized = name.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"invalid sdist member path: {raw!r}")
    return normalized


def normalize_sdist(archive: Path, *, epoch: int) -> None:
    if epoch < 0 or epoch > 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH must fit the gzip timestamp field")
    archive = archive.resolve()
    if not archive.is_file() or not archive.name.endswith(".tar.gz"):
        raise ValueError("sdist must be an existing .tar.gz file")

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    names: set[str] = set()
    top_levels: set[str] = set()
    with tarfile.open(archive, mode="r:gz") as source:
        for original in source.getmembers():
            name = _safe_name(original.name)
            if name in names:
                raise ValueError(f"duplicate sdist member: {name!r}")
            names.add(name)
            top_levels.add(PurePosixPath(name).parts[0])
            if not (original.isdir() or original.isfile()):
                raise ValueError(f"unsupported sdist member type: {name!r}")
            payload: bytes | None = None
            if original.isfile():
                extracted = source.extractfile(original)
                if extracted is None:
                    raise ValueError(f"unable to read sdist member: {name!r}")
                payload = extracted.read()
                if len(payload) != original.size:
                    raise ValueError(f"truncated sdist member: {name!r}")
            member = copy(original)
            member.name = name
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = epoch
            member.pax_headers = {}
            entries.append((member, payload))

    if len(top_levels) != 1:
        raise ValueError("sdist must contain exactly one top-level directory")

    archive.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent
    )
    temporary = Path(temporary_raw)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            os.fchmod(raw.fileno(), 0o644)
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.GNU_FORMAT,
                ) as target:
                    for member, payload in sorted(entries, key=lambda item: item[0].name):
                        target.addfile(
                            member,
                            BytesIO(payload) if payload is not None else None,
                        )
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, archive)
        directory_fd = os.open(archive.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="normalize gzip/tar metadata in a Python source distribution"
    )
    parser.add_argument("archive", type=Path)
    epoch_raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    parser.add_argument(
        "--epoch",
        type=int,
        default=int(epoch_raw) if epoch_raw else None,
        help="canonical Unix timestamp (defaults to SOURCE_DATE_EPOCH)",
    )
    args = parser.parse_args()
    if args.epoch is None:
        parser.error("--epoch or SOURCE_DATE_EPOCH is required")
    normalize_sdist(args.archive, epoch=args.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
