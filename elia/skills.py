from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    version: int
    description: str
    maturity: str
    required_capabilities: tuple[str, ...]
    authority: str
    preconditions: tuple[str, ...]
    procedure: tuple[str, ...]
    evidence_contract: tuple[str, ...]
    failure_policy: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        for key in (
            "required_capabilities",
            "preconditions",
            "procedure",
            "evidence_contract",
            "failure_policy",
        ):
            item[key] = list(item[key])
        return item


class SkillRegistry:
    MATURITY = {"proven", "prototype", "archived", "hypothesis"}

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._skills = self._load()

    def _load(self) -> dict[str, SkillManifest]:
        if not self.directory.exists():
            return {}
        skills: dict[str, SkillManifest] = {}
        for path in sorted(self.directory.glob("*.yaml")):
            item = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(item, dict):
                raise ValueError(f"skill manifest must be an object: {path}")
            name = str(item.get("name", "")).strip()
            maturity = str(item.get("maturity", "prototype")).strip().lower()
            if not name:
                raise ValueError(f"skill manifest has no name: {path}")
            if name in skills:
                raise ValueError(f"duplicate skill name: {name}")
            if maturity not in self.MATURITY:
                raise ValueError(f"invalid skill maturity {maturity!r} in {path}")
            manifest = SkillManifest(
                name=name,
                version=max(1, int(item.get("version", 1))),
                description=str(item.get("description", "")).strip(),
                maturity=maturity,
                required_capabilities=tuple(
                    str(value) for value in item.get("required_capabilities", [])
                ),
                authority=str(item.get("authority", "none")),
                preconditions=tuple(str(value) for value in item.get("preconditions", [])),
                procedure=tuple(str(value) for value in item.get("procedure", [])),
                evidence_contract=tuple(
                    str(value) for value in item.get("evidence_contract", [])
                ),
                failure_policy=tuple(str(value) for value in item.get("failure_policy", [])),
            )
            if not manifest.description or not manifest.procedure:
                raise ValueError(f"skill must define description and procedure: {path}")
            skills[name] = manifest
        return skills

    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> SkillManifest | None:
        return self._skills.get(name)

    def catalog(self) -> dict[str, dict[str, Any]]:
        return {name: self._skills[name].as_dict() for name in sorted(self._skills)}

    def availability(
        self,
        capability_catalog: dict[str, dict[str, Any]],
        capability_health: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, skill in sorted(self._skills.items()):
            missing: list[str] = []
            degraded: list[str] = []
            for capability in skill.required_capabilities:
                declared = capability_catalog.get(capability)
                if not declared or not bool(declared.get("enabled", True)):
                    missing.append(capability)
                    continue
                health = capability_health.get(capability, {})
                if int(health.get("consecutive_failures", 0) or 0) >= 3:
                    degraded.append(capability)
            result[name] = {
                "available": not missing and not degraded and skill.maturity != "archived",
                "maturity": skill.maturity,
                "authority": skill.authority,
                "missing_capabilities": missing,
                "degraded_capabilities": degraded,
                "manifest": skill.as_dict(),
            }
        return result

    def prompt_catalog(
        self,
        capability_catalog: dict[str, dict[str, Any]],
        capability_health: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        availability = self.availability(capability_catalog, capability_health)
        return {
            name: {
                "available": item["available"],
                "maturity": item["maturity"],
                "authority": item["authority"],
                "description": item["manifest"]["description"],
                "procedure": item["manifest"]["procedure"],
                "evidence_contract": item["manifest"]["evidence_contract"],
                "failure_policy": item["manifest"]["failure_policy"],
                "missing_capabilities": item["missing_capabilities"],
                "degraded_capabilities": item["degraded_capabilities"],
            }
            for name, item in availability.items()
            if item["maturity"] != "archived"
        }
