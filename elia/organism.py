from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import importlib
import inspect
from pathlib import Path
from typing import Any

import yaml

from .canonical import canonical_json_bytes
from .paths import default_manifest_path
from .research.registry import RESEARCH_REGISTRY, maturity_summary


MATURITY = {"core", "proven", "prototype", "archived", "hypothesis"}


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _fingerprint(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _load_yaml_object(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"organism manifest fragment must contain a YAML object: {path}")
    return raw


def _merge_default_overlays(path: Path, base: dict[str, Any]) -> dict[str, Any]:
    """Merge immutable generational anatomy overlays next to the default manifest.

    `config/organism.yaml` remains the historical base anatomy. New generations add
    small declarative fragments under `config/organism.d/` instead of rewriting the
    whole lineage manifest. Overlays are sorted by filename, may add layers/organs and
    raise schema_version, but may not change identity_id or silently replace an organ.

    Custom manifest paths do not implicitly load overlays; this keeps isolated tests
    and external audits deterministic unless they explicitly use the project default.
    """

    if path != default_manifest_path().resolve():
        return base
    overlay_dir = path.parent / "organism.d"
    if not overlay_dir.is_dir():
        return base

    merged = deepcopy(base)
    merged_layers = dict(merged.get("layers") or {})
    merged_organs = list(merged.get("organs") or [])
    existing_ids = {
        str(item.get("id", "")).strip()
        for item in merged_organs
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    base_identity = str(merged.get("identity_id", "")).strip()
    overlays: list[dict[str, str]] = []

    for overlay_path in sorted(overlay_dir.glob("*.yaml"), key=lambda item: item.name):
        overlay = _load_yaml_object(overlay_path)
        overlay_identity = str(overlay.get("identity_id", base_identity)).strip()
        if overlay_identity != base_identity:
            raise ValueError(
                f"organism overlay {overlay_path.name} identity_id {overlay_identity!r} "
                f"does not match base {base_identity!r}"
            )
        for key, value in dict(overlay.get("layers") or {}).items():
            merged_layers[str(key)] = str(value)
        additions = overlay.get("organs") or []
        if not isinstance(additions, list):
            raise ValueError(f"organism overlay organs must be a list: {overlay_path.name}")
        for item in additions:
            if not isinstance(item, dict):
                raise ValueError(
                    f"organism overlay organ must be an object: {overlay_path.name}"
                )
            organ_id = str(item.get("id", "")).strip()
            if not organ_id or organ_id in existing_ids:
                raise ValueError(
                    f"organism overlay {overlay_path.name} has invalid/duplicate organ id: {organ_id!r}"
                )
            existing_ids.add(organ_id)
            merged_organs.append(deepcopy(item))
        merged["schema_version"] = max(
            int(merged.get("schema_version", 1)),
            int(overlay.get("schema_version", merged.get("schema_version", 1))),
        )
        overlays.append(
            {
                "name": overlay_path.name,
                "sha256": sha256(overlay_path.read_bytes()).hexdigest(),
            }
        )

    merged["layers"] = merged_layers
    merged["organs"] = merged_organs
    merged["anatomy_overlays"] = overlays
    return merged


@dataclass(frozen=True, slots=True)
class OrganSpec:
    id: str
    layer: str
    kind: str
    role: str
    required: bool
    maturity: str
    authority: str
    path: str | None = None
    module: str | None = None
    symbol: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrganFinding:
    organ_id: str
    severity: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OrganStatus:
    organ: OrganSpec
    available: bool
    implementation_fingerprint: str | None
    resolved_location: str | None
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "organ": self.organ.as_dict(),
            "available": self.available,
            "implementation_fingerprint": self.implementation_fingerprint,
            "resolved_location": self.resolved_location,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class OrganismAuditReport:
    healthy: bool
    identity_id: str
    manifest_fingerprint: str
    architecture_fingerprint: str
    statuses: tuple[OrganStatus, ...]
    findings: tuple[OrganFinding, ...]
    research_maturity: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "identity_id": self.identity_id,
            "manifest_fingerprint": self.manifest_fingerprint,
            "architecture_fingerprint": self.architecture_fingerprint,
            "statuses": [item.as_dict() for item in self.statuses],
            "findings": [item.as_dict() for item in self.findings],
            "research_maturity": self.research_maturity,
        }


@dataclass(frozen=True, slots=True)
class OrganismManifest:
    path: Path
    schema_version: int
    identity_id: str
    name: str
    principle: str
    layers: dict[str, str]
    organs: tuple[OrganSpec, ...]
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: Path | None = None) -> "OrganismManifest":
        path = Path(path or default_manifest_path()).resolve()
        raw = _merge_default_overlays(path, _load_yaml_object(path))
        identity_id = str(raw.get("identity_id", "")).strip()
        if not identity_id:
            raise ValueError("organism manifest has no identity_id")
        items = raw.get("organs") or []
        if not isinstance(items, list) or not items:
            raise ValueError("organism manifest must declare organs")
        seen: set[str] = set()
        organs: list[OrganSpec] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each organ declaration must be an object")
            organ_id = str(item.get("id", "")).strip()
            if not organ_id or organ_id in seen:
                raise ValueError(f"invalid or duplicate organ id: {organ_id!r}")
            seen.add(organ_id)
            maturity = str(item.get("maturity", "prototype")).strip().lower()
            if maturity not in MATURITY:
                raise ValueError(f"invalid maturity {maturity!r} for organ {organ_id}")
            kind = str(item.get("kind", "python")).strip().lower()
            if kind not in {"python", "artifact"}:
                raise ValueError(f"invalid organ kind {kind!r}: {organ_id}")
            path_value = str(item.get("path", "")).strip() or None
            module = str(item.get("module", "")).strip() or None
            symbol = str(item.get("symbol", "")).strip() or None
            if kind == "artifact" and not path_value:
                raise ValueError(f"artifact organ requires path: {organ_id}")
            if kind == "python" and not module:
                raise ValueError(f"python organ requires module: {organ_id}")
            organs.append(
                OrganSpec(
                    id=organ_id,
                    layer=str(item.get("layer", "other")).strip() or "other",
                    kind=kind,
                    role=str(item.get("role", "")).strip(),
                    required=bool(item.get("required", False)),
                    maturity=maturity,
                    authority=str(item.get("authority", "none")).strip() or "none",
                    path=path_value,
                    module=module,
                    symbol=symbol,
                )
            )
        return cls(
            path=path,
            schema_version=max(1, int(raw.get("schema_version", 1))),
            identity_id=identity_id,
            name=str(raw.get("name", identity_id)),
            principle=str(raw.get("principle", "")).strip(),
            layers={
                str(k): str(v)
                for k, v in dict(raw.get("layers") or {}).items()
            },
            organs=tuple(organs),
            raw=raw,
        )

    @property
    def project_root(self) -> Path:
        return self.path.parent.parent

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.raw)

    def _audit_artifact(self, organ: OrganSpec) -> OrganStatus:
        if organ.path is None:
            return OrganStatus(
                organ,
                False,
                None,
                None,
                "artifact organ has no configured path",
            )
        resolved = (self.project_root / organ.path).resolve()
        if not resolved.is_file():
            return OrganStatus(
                organ,
                False,
                None,
                str(resolved),
                "artifact is missing",
            )
        digest = sha256(resolved.read_bytes()).hexdigest()
        return OrganStatus(organ, True, digest, str(resolved), None)

    def _audit_python(self, organ: OrganSpec) -> OrganStatus:
        if organ.module is None:
            return OrganStatus(
                organ,
                False,
                None,
                None,
                "python organ has no configured module",
            )
        try:
            module = importlib.import_module(organ.module)
            target: Any = module
            if organ.symbol:
                if not hasattr(module, organ.symbol):
                    raise AttributeError(f"symbol {organ.symbol!r} is missing")
                target = getattr(module, organ.symbol)
            location = inspect.getsourcefile(target) or getattr(module, "__file__", None)
            digest: str | None = None
            if location and Path(location).is_file():
                digest = sha256(Path(location).read_bytes()).hexdigest()
            else:
                try:
                    digest = sha256(
                        inspect.getsource(target).encode("utf-8")
                    ).hexdigest()
                except (OSError, TypeError):
                    digest = _fingerprint(
                        {"module": organ.module, "symbol": organ.symbol}
                    )
            return OrganStatus(
                organ,
                True,
                digest,
                str(location) if location else organ.module,
                None,
            )
        except Exception as exc:
            return OrganStatus(
                organ,
                False,
                None,
                organ.module,
                f"{type(exc).__name__}: {str(exc)[:1000]}",
            )

    def audit(
        self,
        *,
        expected_identity_id: str | None = None,
    ) -> OrganismAuditReport:
        statuses: list[OrganStatus] = []
        findings: list[OrganFinding] = []
        if expected_identity_id and self.identity_id != str(expected_identity_id):
            findings.append(
                OrganFinding(
                    "manifest",
                    "critical",
                    f"manifest identity_id {self.identity_id!r} != expected {expected_identity_id!r}",
                )
            )
        for organ in self.organs:
            status = (
                self._audit_artifact(organ)
                if organ.kind == "artifact"
                else self._audit_python(organ)
            )
            statuses.append(status)
            if not status.available:
                severity = "critical" if organ.required else "warning"
                findings.append(
                    OrganFinding(
                        organ.id,
                        severity,
                        status.error or "organ unavailable",
                    )
                )
            if organ.required and organ.maturity in {"archived", "hypothesis"}:
                findings.append(
                    OrganFinding(
                        organ.id,
                        "critical",
                        "required organ cannot be maturity=archived/hypothesis",
                    )
                )

        architecture = {
            "manifest": self.fingerprint,
            "implementations": {
                item.organ.id: item.implementation_fingerprint
                for item in statuses
                if item.implementation_fingerprint
            },
            "research": {
                name: artifact.as_dict()
                for name, artifact in sorted(RESEARCH_REGISTRY.items())
            },
        }
        healthy = not any(item.severity == "critical" for item in findings)
        return OrganismAuditReport(
            healthy=healthy,
            identity_id=self.identity_id,
            manifest_fingerprint=self.fingerprint,
            architecture_fingerprint=_fingerprint(architecture),
            statuses=tuple(statuses),
            findings=tuple(findings),
            research_maturity=maturity_summary(),
        )

    def prompt_contract(self) -> dict[str, Any]:
        core = [
            {
                "id": organ.id,
                "layer": organ.layer,
                "role": organ.role,
                "authority": organ.authority,
                "maturity": organ.maturity,
            }
            for organ in self.organs
            if organ.required
        ]
        return {
            "schema_version": self.schema_version,
            "identity_id": self.identity_id,
            "manifest_fingerprint": self.fingerprint,
            "principle": self.principle,
            "core_organs": core,
            "anatomy_overlays": list(self.raw.get("anatomy_overlays") or []),
            "research_maturity": maturity_summary(),
            "research_rule": (
                "prototype/hypothesis research is evidence-generating code, not identity authority or a proven production gain"
            ),
        }


def default_organism_contract() -> dict[str, Any]:
    try:
        return OrganismManifest.load().prompt_contract()
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
        }
