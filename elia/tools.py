from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from . import __version__
from .body import SensorimotorFabric
from .causal import CausalMemoryStore
from .observations import ObservationStore
from .state_bus import OrganismStateBus
from .world_model import WorldModelStore


@dataclass(slots=True)
class ToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    args: str
    authority: str
    side_effects: str
    network_scope: str
    cost_class: str
    enabled: bool = True
    readiness: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


class ToolRegistry:
    """Explicit capability boundary plus durable sensorium/provenance wiring."""

    def __init__(
        self,
        workspace: Path,
        tool_config: dict[str, Any] | None = None,
        *,
        mcp_target_overrides: dict[str, Any] | None = None,
    ):
        self.workspace = workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.config = tool_config or {}
        database = self.workspace.parent / "memory.sqlite3"
        self.observations = ObservationStore(database)
        self.causal = CausalMemoryStore(database)
        self.state_bus = OrganismStateBus(database)
        self.world_model = WorldModelStore(database)
        self.body = SensorimotorFabric(
            self.workspace,
            self.config.get("body", {}),
            mcp_target_overrides=mcp_target_overrides,
        )

    def catalog(self) -> dict[str, dict[str, Any]]:
        http_enabled = bool(self.config.get("http_get", {}).get("enabled", True))
        capabilities = [
            Capability(
                "noop",
                "Take no external action this cycle.",
                "{}",
                "none",
                "none",
                "none",
                "negligible",
            ),
            Capability(
                "list_workspace",
                "List files owned by ELIA inside the private workspace jail.",
                "{}",
                "workspace_read",
                "reads private workspace metadata",
                "none",
                "negligible",
            ),
            Capability(
                "read_workspace",
                "Read one UTF-8 file owned by ELIA inside the workspace jail.",
                "{path: str}",
                "workspace_read",
                "reads private workspace content",
                "none",
                "negligible",
            ),
            Capability(
                "write_workspace",
                "Write one UTF-8 file owned by ELIA inside the workspace jail.",
                "{path: str, content: str}",
                "workspace_write",
                "writes private workspace content",
                "none",
                "low",
            ),
            Capability(
                "http_get",
                "Read one public HTTP/HTTPS resource. Private and reserved destinations are rejected.",
                "{url: str}",
                "public_network_read",
                "remote read request only",
                "public_http_https",
                "network",
                enabled=http_enabled,
            ),
            Capability(
                "self_check",
                "Run bounded local checks of ELIA-owned workspace primitives and path-jail enforcement.",
                "{}",
                "local_self_diagnostic",
                "creates and removes one temporary workspace scratch file",
                "none",
                "low",
            ),
            Capability(
                "propose_repair",
                "Persist a structured repair proposal for later validation; does not modify runtime code or deploy anything.",
                "{title: str, diagnosis: str, proposed_change: str, validation_plan: str}",
                "workspace_write",
                "writes a proposal under workspace/repairs only",
                "none",
                "low",
            ),
            Capability(
                "stage_deliverable",
                "Persist a useful-work deliverable candidate for an opportunity without submitting it externally.",
                "{title: str, content: str, format: str, opportunity_id?: int, validation: str, evidence?: str}",
                "workspace_write",
                "writes a staged artifact under workspace/deliverables only",
                "none",
                "low",
            ),
            Capability(
                "sensorium_recent",
                "Read recent normalized observations from all tools/body adapters.",
                "{limit?: int}",
                "local_observation_read",
                "none",
                "none",
                "negligible",
            ),
            Capability(
                "causal_snapshot",
                "Read empirical intervention history and per-action success statistics; never presented as causal proof.",
                "{}",
                "local_experience_read",
                "none",
                "none",
                "negligible",
            ),
            Capability(
                "world_model_query",
                "Query evidence-bearing beliefs and contradictions in ELIA's external world model.",
                "{text?: str, domain?: str, statuses?: [str], limit?: int}",
                "local_world_model_read",
                "none",
                "none",
                "negligible",
            ),
            Capability(
                "world_model_propose",
                "Create/reinforce one evidence-bearing world hypothesis; cannot create a verified fact.",
                "{domain: str, subject: str, predicate: str, object: any, confidence: number, evidence: str, observation_id?: int}",
                "local_world_model_write",
                "writes a revisable hypothesis with confidence cap",
                "none",
                "low",
            ),
            Capability(
                "world_model_revise",
                "Revise an existing model-originated world hypothesis as hypothesis/supported/disputed only.",
                "{id: int, status?: hypothesis|supported|disputed, confidence?: number, evidence: str, observation_id?: int}",
                "local_world_model_write",
                "updates a revisable hypothesis; cannot verify/refute authoritatively",
                "none",
                "low",
            ),
            Capability(
                "body_diagnostics",
                "Inspect which digital body adapters/capabilities are configured and currently available.",
                "{}",
                "local_body_introspection",
                "none",
                "none",
                "negligible",
            ),
        ]
        result = {item.name: item.as_dict() for item in capabilities}
        result.update(self.body.capabilities())
        return result

    def descriptions(self) -> dict[str, str]:
        return {
            name: f"{item['description']} args={item['args']} enabled={item['enabled']} readiness={item.get('readiness', 'ready')}"
            for name, item in self.catalog().items()
        }

    def execute(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        args = dict(args or {})
        capability = self.catalog().get(name)
        if capability is not None and not capability["enabled"]:
            return ToolResult(
                False,
                name,
                error=(
                    f"Capability is disabled/unavailable: {name} "
                    f"({capability.get('readiness', 'disabled')})"
                ),
            )

        transaction_id = self.state_bus.begin(f"capability:{name}")
        args_fingerprint = _fingerprint(args)
        self.state_bus.append(
            transaction_id,
            phase="action",
            kind="CAPABILITY_ATTEMPT",
            payload={"capability": name, "arguments_fingerprint": args_fingerprint},
        )
        started = time.monotonic()
        try:
            result = self._dispatch(name, args)
        except Exception as exc:  # Tool failures become observations, not process failures.
            result = ToolResult(False, name, error=f"{type(exc).__name__}: {exc}")
        duration_ms = (time.monotonic() - started) * 1000.0

        observation_payload = result.as_dict()
        source_kind = "body" if name in self.body.capabilities() else "capability"
        observation = self.observations.record(
            source_kind=source_kind,
            source_ref=name,
            payload=observation_payload,
            trust=0.65 if source_kind == "body" else 0.8,
            success=result.ok,
            summary=(result.error or f"{name} completed")[:4000],
            provenance={
                "capability": name,
                "arguments_fingerprint": args_fingerprint,
                "body_version": __version__,
            },
            transaction_id=transaction_id,
        )
        experience = self.causal.record_intervention(
            action_name=name,
            arguments=args,
            outcome=observation_payload,
            success=result.ok,
            duration_ms=duration_ms,
            observation_id=observation.id,
            transaction_id=transaction_id,
            source="tool_registry",
            outcome_summary=result.error or f"{name} ok={result.ok}",
        )
        self.state_bus.append(
            transaction_id,
            phase="observation",
            kind="OBSERVATION_RECORDED",
            payload={
                "observation_id": observation.id,
                "payload_sha256": observation.payload_sha256,
                "experience_id": experience.id,
                "success": result.ok,
                "duration_ms": duration_ms,
            },
        )
        self.state_bus.commit(
            transaction_id,
            {"capability": name, "success": result.ok, "observation_id": observation.id},
        )
        return result

    def _dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name == "noop":
            return ToolResult(True, name, {"message": "No action taken."})
        if name == "list_workspace":
            return self._list_workspace()
        if name == "read_workspace":
            return self._read_workspace(str(args.get("path", "")))
        if name == "write_workspace":
            return self._write_workspace(
                str(args.get("path", "")), str(args.get("content", ""))
            )
        if name == "http_get":
            return self._http_get(str(args.get("url", "")))
        if name == "self_check":
            return self._self_check()
        if name == "propose_repair":
            return self._propose_repair(args)
        if name == "stage_deliverable":
            return self._stage_deliverable(args)
        if name == "sensorium_recent":
            limit = max(1, min(int(args.get("limit", 12)), 64))
            return ToolResult(True, name, {"observations": self.observations.snapshot(limit)})
        if name == "causal_snapshot":
            return ToolResult(True, name, self.causal.snapshot())
        if name == "world_model_query":
            statuses_raw = args.get("statuses")
            statuses = (
                {str(item) for item in statuses_raw}
                if isinstance(statuses_raw, list)
                else None
            )
            beliefs = self.world_model.query(
                text=str(args.get("text", "")),
                domain=(str(args["domain"]) if args.get("domain") else None),
                statuses=statuses,
                limit=max(1, min(int(args.get("limit", 24)), 64)),
            )
            return ToolResult(
                True,
                name,
                {
                    "beliefs": [item.as_dict() for item in beliefs],
                    "contradictions": self.world_model.snapshot(64)["contradictions"],
                },
            )
        if name == "world_model_propose":
            observation_raw = args.get("observation_id")
            belief = self.world_model.propose(
                domain=str(args.get("domain", "")),
                subject=str(args.get("subject", "")),
                predicate=str(args.get("predicate", "")),
                object=args.get("object"),
                confidence=float(args.get("confidence", 0.5)),
                evidence=str(args.get("evidence", "")),
                source="brain",
                observation_id=(int(observation_raw) if observation_raw is not None else None),
            )
            return ToolResult(True, name, belief.as_dict())
        if name == "world_model_revise":
            observation_raw = args.get("observation_id")
            confidence_raw = args.get("confidence")
            belief = self.world_model.revise_from_model(
                int(args.get("id")),
                status=(str(args["status"]) if args.get("status") is not None else None),
                confidence=(float(confidence_raw) if confidence_raw is not None else None),
                evidence=str(args.get("evidence", "")),
                observation_id=(int(observation_raw) if observation_raw is not None else None),
            )
            return ToolResult(True, name, belief.as_dict())
        if name == "body_diagnostics":
            return ToolResult(True, name, self.body.diagnostics())
        if name in self.body.capabilities():
            body_result = self.body.execute(name, args)
            return ToolResult(body_result.ok, name, body_result.data, body_result.error)
        return ToolResult(False, name, error=f"Unknown tool: {name}")

    def _safe_path(self, relative: str) -> Path:
        if not relative or relative in {".", "./"}:
            raise ValueError("A file path is required")
        candidate = (self.workspace / relative).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError("Path escapes workspace")
        return candidate

    def _list_workspace(self) -> ToolResult:
        files = [
            str(path.relative_to(self.workspace))
            for path in sorted(self.workspace.rglob("*"))
            if path.is_file()
        ]
        return ToolResult(True, "list_workspace", {"files": files[:1000]})

    def _read_workspace(self, relative: str) -> ToolResult:
        path = self._safe_path(relative)
        if not path.is_file():
            return ToolResult(False, "read_workspace", error="File does not exist")
        data = path.read_text(encoding="utf-8")
        return ToolResult(True, "read_workspace", {"path": relative, "content": data[:256_000]})

    def _write_workspace(self, relative: str, content: str) -> ToolResult:
        path = self._safe_path(relative)
        if len(content.encode("utf-8")) > 256_000:
            return ToolResult(False, "write_workspace", error="Write exceeds 256 KB limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            True,
            "write_workspace",
            {"path": relative, "bytes": len(content.encode("utf-8"))},
        )

    def _self_check(self) -> ToolResult:
        token = uuid4().hex
        relative = f".selfcheck-{token}.txt"
        scratch = self._safe_path(relative)
        checks: dict[str, bool] = {}
        try:
            scratch.write_text(token, encoding="utf-8")
            checks["workspace_write"] = scratch.is_file()
            checks["workspace_read"] = scratch.read_text(encoding="utf-8") == token
            try:
                self._safe_path("../selfcheck-escape.txt")
                checks["workspace_jail"] = False
            except ValueError:
                checks["workspace_jail"] = True
        finally:
            scratch.unlink(missing_ok=True)
        checks["scratch_cleanup"] = not scratch.exists()
        checks["state_bus_no_unreconciled_prior_actions"] = len(self.state_bus.incomplete(16)) <= 1
        ok = all(checks.values())
        return ToolResult(
            ok,
            "self_check",
            {
                "checks": checks,
                "body": self.body.diagnostics(),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "note": "Self-check does not broaden capability authority.",
            },
            error=None if ok else "One or more bounded self-checks failed",
        )

    @staticmethod
    def _slug(text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-").lower()
        return (slug or "artifact")[:64]

    def _propose_repair(self, args: dict[str, Any]) -> ToolResult:
        title = str(args.get("title", "")).strip()[:240]
        diagnosis = str(args.get("diagnosis", "")).strip()[:8000]
        proposed_change = str(args.get("proposed_change", "")).strip()[:16000]
        validation_plan = str(args.get("validation_plan", "")).strip()[:8000]
        if not title or not diagnosis or not proposed_change or not validation_plan:
            return ToolResult(
                False,
                "propose_repair",
                error="title, diagnosis, proposed_change, and validation_plan are required",
            )
        proposal = {
            "title": title,
            "diagnosis": diagnosis,
            "proposed_change": proposed_change,
            "validation_plan": validation_plan,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "proposal_only",
            "deployment_authority": "none",
        }
        relative = f"repairs/{self._slug(title)}-{uuid4().hex[:8]}.json"
        payload = json.dumps(proposal, ensure_ascii=False, sort_keys=True, indent=2)
        write = self._write_workspace(relative, payload)
        if not write.ok:
            return ToolResult(False, "propose_repair", error=write.error)
        return ToolResult(
            True,
            "propose_repair",
            {
                "path": relative,
                "status": "proposal_only",
                "message": "Repair proposal stored for validation; no runtime code was changed.",
            },
        )

    def _stage_deliverable(self, args: dict[str, Any]) -> ToolResult:
        title = str(args.get("title", "")).strip()[:240]
        content = str(args.get("content", ""))
        format_name = str(args.get("format", "text")).strip()[:64] or "text"
        validation = str(args.get("validation", "")).strip()[:8000]
        evidence = str(args.get("evidence", "")).strip()[:8000]
        opportunity_raw = args.get("opportunity_id")
        opportunity_id = int(opportunity_raw) if opportunity_raw is not None else None
        if not title or not content or not validation:
            return ToolResult(
                False,
                "stage_deliverable",
                error="title, content, and validation are required",
            )
        if len(content.encode("utf-8")) > 240_000:
            return ToolResult(False, "stage_deliverable", error="deliverable content exceeds 240 KB")
        artifact = {
            "title": title,
            "opportunity_id": opportunity_id,
            "format": format_name,
            "content": content,
            "validation": validation,
            "evidence": evidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "staged_only",
            "submission_authority": "none",
        }
        relative = f"deliverables/{self._slug(title)}-{uuid4().hex[:8]}.json"
        payload = json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2)
        write = self._write_workspace(relative, payload)
        if not write.ok:
            return ToolResult(False, "stage_deliverable", error=write.error)
        return ToolResult(
            True,
            "stage_deliverable",
            {
                "path": relative,
                "opportunity_id": opportunity_id,
                "status": "staged_only",
                "message": "Deliverable candidate stored locally; nothing was submitted externally.",
            },
        )

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are supported")
        if not parsed.hostname:
            raise ValueError("URL hostname is required")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URLs are not supported")

        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in addresses:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError(f"Non-public destination rejected: {ip}")

    def _http_get(self, url: str) -> ToolResult:
        http_cfg = self.config.get("http_get", {})
        if not http_cfg.get("enabled", True):
            return ToolResult(False, "http_get", error="http_get is disabled")

        self._validate_public_url(url)
        timeout = float(http_cfg.get("timeout_seconds", 20))
        max_bytes = int(http_cfg.get("max_bytes", 1_000_000))

        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(
                url,
                headers={"User-Agent": f"ELIA-WILD/{__version__} (+research-agent)"},
            )

        raw = response.content[:max_bytes]
        content_type = response.headers.get("content-type", "")
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        data = {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "content-length", "location", "last-modified"}
            },
            "text": text,
            "truncated": len(response.content) > max_bytes,
        }
        return ToolResult(200 <= response.status_code < 400, "http_get", data)
