from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from release_tools.finalize_cyclonedx import finalize_cyclonedx


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

    first_serial = finalize_cyclonedx(first)
    second_serial = finalize_cyclonedx(second)

    assert first.read_bytes() == second.read_bytes()
    assert first_serial == second_serial
    assert first_serial.startswith("urn:uuid:")
    assert uuid.UUID(first_serial.removeprefix("urn:uuid:")).version == 5
    assert finalize_cyclonedx(first) == first_serial


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

    with pytest.raises(ValueError, match=message):
        finalize_cyclonedx(document)


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

    finalize_cyclonedx(document)
    result = json.loads(document.read_text(encoding="utf-8"))

    assert result["bomFormat"] == "CycloneDX"
    assert result["specVersion"] == "1.6"
    assert result["serialNumber"].startswith("urn:uuid:")
