from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .crc import build_crc, compare_crc, read_crc, write_crc
from .identity import IdentityBundle
from .longitudinal import LongitudinalContinuityStore
from .organism import OrganismManifest, default_manifest_path
from .research.registry import maturity_summary


@dataclass(frozen=True, slots=True)
class VitalSignsReport:
    checked_at: str
    healthy: bool
    organism: dict[str, Any]
    crc: dict[str, Any]
    continuity_comparison: dict[str, Any] | None
    longitudinal: dict[str, Any]
    research_maturity: dict[str, list[str]]
    last_healthy_crc_path: str
    failure_evidence_path: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class VitalSigns:
    """Model-independent organism/continuity gate with long-horizon evidence.

    The last *healthy* CRC is never replaced by a broken comparison. A failed check
    preserves both the trusted prior capsule and separate failure evidence. Materially
    new states are also written into a checkpointed longitudinal series.
    """

    def __init__(self, config: Config, *, manifest_path: Path | None = None):
        self.config = config
        self.identity = IdentityBundle.load(
            config.subject_core_path,
            config.continuity_constitution_path,
        )
        self.manifest = OrganismManifest.load(manifest_path or default_manifest_path())
        self.root = config.runtime.state_dir / "workspace" / ".organism"
        self.last_healthy_crc_path = self.root / "last-healthy-crc.json"
        self.latest_report_path = self.root / "vitals.json"
        self.longitudinal = LongitudinalContinuityStore(
            config.runtime.state_dir / "memory.sqlite3"
        )

    def _persist_report(self, report: VitalSignsReport) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.latest_report_path.write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def check(self, *, persist: bool = True) -> VitalSignsReport:
        audit = self.manifest.audit(expected_identity_id=self.identity.identity_id)
        audit_dict = audit.as_dict()
        capsule = build_crc(self.config)
        capsule_dict = capsule.as_dict()
        capsule_dict["capsule_fingerprint"] = capsule.fingerprint

        comparison_dict: dict[str, Any] | None = None
        continuity_healthy = capsule.chronicle_valid
        if self.last_healthy_crc_path.is_file():
            previous = read_crc(self.last_healthy_crc_path)
            comparison = compare_crc(previous, capsule_dict)
            comparison_dict = comparison.as_dict()
            continuity_healthy = continuity_healthy and comparison.status != "broken"

        healthy = bool(audit.healthy and continuity_healthy)
        failure_path: Path | None = None
        if persist:
            self.root.mkdir(parents=True, exist_ok=True)
            if healthy:
                write_crc(self.last_healthy_crc_path, capsule)
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                failure_path = self.root / f"failed-crc-{stamp}.json"
                write_crc(failure_path, capsule)
            self.longitudinal.record(
                capsule=capsule_dict,
                organism=audit_dict,
                comparison=comparison_dict,
                healthy=healthy,
            )

        report = VitalSignsReport(
            checked_at=datetime.now(timezone.utc).isoformat(),
            healthy=healthy,
            organism=audit_dict,
            crc=capsule_dict,
            continuity_comparison=comparison_dict,
            longitudinal=self.longitudinal.summary(),
            research_maturity=maturity_summary(),
            last_healthy_crc_path=str(self.last_healthy_crc_path),
            failure_evidence_path=str(failure_path) if failure_path else None,
        )
        if persist:
            self._persist_report(report)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-vitals")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--no-persist", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    report = VitalSigns(
        config,
        manifest_path=Path(args.manifest) if args.manifest else None,
    ).check(persist=not args.no_persist)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.healthy else 2)


if __name__ == "__main__":
    main()
