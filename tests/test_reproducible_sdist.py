from __future__ import annotations

import gzip
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "release_tools" / "normalize_sdist.py"


def _write_sdist(path: Path, *, timestamp: int) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(
            filename=path.name,
            mode="wb",
            fileobj=raw,
            mtime=timestamp,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                directory = tarfile.TarInfo("demo-1.0")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o755
                directory.mtime = timestamp
                archive.addfile(directory)
                content = b"immutable source\n"
                member = tarfile.TarInfo("demo-1.0/module.py")
                member.mode = 0o644
                member.mtime = timestamp
                member.size = len(content)
                archive.addfile(member, BytesIO(content))


def test_sdist_normalization_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    _write_sdist(first, timestamp=1_700_000_001)
    _write_sdist(second, timestamp=1_800_000_002)

    for archive in (first, second):
        subprocess.run(
            [sys.executable, str(NORMALIZER), str(archive), "--epoch", "1700000000"],
            check=True,
        )

    assert sha256(first.read_bytes()).digest() == sha256(second.read_bytes()).digest()
    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == [
        "demo-1.0",
        "demo-1.0/module.py",
    ]
    assert all(member.mtime == 1_700_000_000 for member in members)
    assert all(member.uid == 0 and member.gid == 0 for member in members)


def test_sdist_normalization_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        content = b"escape"
        member = tarfile.TarInfo("../escape")
        member.size = len(content)
        archive.addfile(member, BytesIO(content))

    result = subprocess.run(
        [sys.executable, str(NORMALIZER), str(archive_path), "--epoch", "1700000000"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unsafe sdist member path" in result.stderr
