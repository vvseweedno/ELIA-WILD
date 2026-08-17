from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .identity import IdentityBundle
from .organism import OrganismManifest
from .prompting import PromptTemplate
from .research.runtime import RuntimeCompatibilityChecker
from .skills import SkillRegistry
from .tools import ToolRegistry
from .vitals import VitalSigns


@dataclass(frozen=True, slots=True)
class DoctorReport:
    healthy: bool
    identity: dict[str, Any]
    organism: dict[str, Any]
    vitals: dict[str, Any]
    prompt: dict[str, Any]
    capabilities: dict[str, Any]
    skills: dict[str, Any]
    runtime_checks: list[dict[str, Any]]
    external_readiness: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrganismDoctor:
    """CPU-only installation/organism diagnostic; never loads the model backend."""

    def __init__(self, config: Config):
        self.config = config

    def run(self) -> DoctorReport:
        identity = IdentityBundle.load(
            self.config.subject_core_path,
            self.config.continuity_constitution_path,
        )
        organism = OrganismManifest.load().audit(expected_identity_id=identity.identity_id)
        vitals = VitalSigns(self.config).check(persist=False)
        prompt = PromptTemplate.load(self.config.system_prompt_path)
        tools = ToolRegistry(self.config.runtime.state_dir / "workspace", self.config.raw_tools)
        capability_catalog = tools.catalog()
        skills = SkillRegistry(self.config.skills_dir).catalog()
        checks = RuntimeCompatibilityChecker().check(
            required_modules=("json", "sqlite3", "yaml", "httpx"),
            optional_modules=("torch", "transformers", "bitsandbytes"),
        )
        runtime_ok = RuntimeCompatibilityChecker.healthy(checks)
        required_skills_valid = all(
            isinstance(item, dict) and item.get("name")
            for item in skills.values()
        )
        healthy = bool(organism.healthy and vitals.healthy and runtime_ok and required_skills_valid)
        return DoctorReport(
            healthy=healthy,
            identity={
                "identity_id": identity.identity_id,
                "name": identity.name,
                "fingerprint": identity.fingerprint,
                "branch_id": self.config.branch_id,
            },
            organism={
                "healthy": organism.healthy,
                "manifest_fingerprint": organism.manifest_fingerprint,
                "architecture_fingerprint": organism.architecture_fingerprint,
                "critical_findings": [
                    item.as_dict() for item in organism.findings if item.severity == "critical"
                ],
            },
            vitals={
                "healthy": vitals.healthy,
                "continuity_comparison": vitals.continuity_comparison,
            },
            prompt={
                "path": str(prompt.path),
                "fingerprint": prompt.fingerprint,
            },
            capabilities={
                "count": len(capability_catalog),
                "enabled": sorted(
                    name for name, item in capability_catalog.items() if item.get("enabled")
                ),
            },
            skills={
                "count": len(skills),
                "names": sorted(skills),
            },
            runtime_checks=[item.as_dict() for item in checks],
            external_readiness={
                "checkpoint_key_present": bool(os.getenv("ELIA_CHECKPOINT_KEY", "").strip()),
                "kaggle_api_token_present": bool(os.getenv("KAGGLE_API_TOKEN", "").strip()),
                "kaggle_state_dataset_configured": bool(os.getenv("ELIA_KAGGLE_STATE_DATASET", "").strip()),
                "kaggle_kernel_configured": bool(os.getenv("ELIA_KAGGLE_KERNEL", "").strip()),
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-doctor")
    parser.add_argument("--config", default="config/genesis.yaml")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = OrganismDoctor(load_config(args.config)).run()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if report.healthy else 2)


if __name__ == "__main__":
    main()
