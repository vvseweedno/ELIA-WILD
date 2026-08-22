from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINALIZER = ROOT / "release_tools" / "finalize_cyclonedx.py"


def _finalize(path: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FINALIZER), str(path)],
        check=check,
        capture_output=True,
        text=True,
    )


def test_cyclonedx_finalization_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first.cdx.json"
    second = tmp_path / "second.cdx.json"
    first.write_text(
        '{"specVersion":"1.6","bomFormat":"CycloneDX",'
        '"components":[{"name":"elia-wild","type":"application"}]}',
        encoding="utf-8",
    )
    second.write_text(
        '{"serialNumber":"urn:uuid:00000000-0000-4000-8000-000000000000",'
        '"components":[{"type":"application","name":"elia-wild"}],'
        '"bomFormat":"CycloneDX","specVersion":"1.6"}',
        encoding="utf-8",
    )

    _finalize(first)
    _finalize(second)
    first_serial = json.loads(first.read_text(encoding="utf-8"))["serialNumber"]
    second_serial = json.loads(second.read_text(encoding="utf-8"))["serialNumber"]

    assert first.read_bytes() == second.read_bytes()
    assert first_serial == second_serial
    assert first_serial.startswith("urn:uuid:")
    assert uuid.UUID(first_serial.removeprefix("urn:uuid:")).version == 5
    _finalize(first)
    assert json.loads(first.read_text(encoding="utf-8"))["serialNumber"] == first_serial


@pytest.mark.parametrize(
    "payload, message",
    [
        ('{"bomFormat":"SPDX","specVersion":"1.6"}', "bomFormat"),
        ('{"bomFormat":"CycloneDX"}', "specVersion"),
        ('{"bomFormat":"CycloneDX","specVersion":"1.6","x":NaN}', "non-finite"),
        (
            '{"bomFormat":"CycloneDX","bomFormat":"CycloneDX",'
            '"specVersion":"1.6"}',
            "duplicate JSON member",
        ),
    ],
)
def test_cyclonedx_finalization_rejects_malformed_input(
    tmp_path: Path, payload: str, message: str
) -> None:
    document = tmp_path / "invalid.cdx.json"
    document.write_text(payload, encoding="utf-8")

    result = _finalize(document, check=False)

    assert result.returncode != 0
    assert message in result.stderr


def test_cyclonedx_finalization_emits_attest_recognition_fields(
    tmp_path: Path,
) -> None:
    document = tmp_path / "elia.cdx.json"
    document.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "metadata": {"component": {"name": "elia-wild"}},
            }
        ),
        encoding="utf-8",
    )

    _finalize(document)
    result = json.loads(document.read_text(encoding="utf-8"))

    assert result["bomFormat"] == "CycloneDX"
    assert result["specVersion"] == "1.6"
    assert result["serialNumber"].startswith("urn:uuid:")
