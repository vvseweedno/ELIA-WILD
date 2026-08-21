from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import load_config
from .external_effects import ExternalEffectLedger
from .owner_control import OwnerControl, OwnerMandate


def _json_object(raw: str) -> dict[str, Any]:
    value = json.loads(str(raw))
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("--args-json must decode to a JSON object")
    return value


def _control(config_path: str) -> tuple[OwnerControl, ExternalEffectLedger]:
    config = load_config(config_path)
    database = config.runtime.state_dir / "memory.sqlite3"
    mandate = OwnerMandate.load(
        config.system_prompt_path.with_name("owner_mandate.yaml"),
        required=False,
    )
    return OwnerControl(database, mandate), ExternalEffectLedger(database)


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="elia-control",
        description=(
            "Local non-model control plane for owner delegation and external-effect reconciliation."
        ),
    )
    parser.add_argument("--config", default="config/genesis.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("effects")

    kill = sub.add_parser("kill")
    kill.add_argument("--reason", required=True)
    clear_kill = sub.add_parser("clear-kill")
    clear_kill.add_argument("--reason", required=True)

    revoke = sub.add_parser("revoke")
    revoke.add_argument("--reason", required=True)
    clear_revoke = sub.add_parser("clear-revoke")
    clear_revoke.add_argument("--reason", required=True)

    lease = sub.add_parser("grant-lease")
    lease.add_argument("--approved-by", required=True)
    lease.add_argument("--evidence", required=True)
    lease.add_argument("--hours", type=float, default=None)

    approve = sub.add_parser("approve")
    approve.add_argument("--action", required=True)
    approve.add_argument("--args-json", required=True, type=_json_object)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--evidence", required=True)
    approve.add_argument("--ttl-seconds", type=float, default=900.0)

    reconcile = sub.add_parser("reconcile-effect")
    reconcile.add_argument("--effect-id", required=True)
    observed = reconcile.add_mutually_exclusive_group(required=True)
    observed.add_argument("--effect-observed", action="store_true")
    observed.add_argument("--no-effect", action="store_true")
    reconcile.add_argument("--evidence", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    owner, effects = _control(args.config)

    if args.command == "status":
        _print(
            {
                "owner_control": owner.snapshot(),
                "external_effects": effects.diagnostics(),
            }
        )
        return 0
    if args.command == "effects":
        _print(effects.diagnostics())
        return 0
    if args.command == "kill":
        owner.kill(reason=args.reason, killed=True)
        _print({"ok": True, "owner_control": owner.snapshot()})
        return 0
    if args.command == "clear-kill":
        owner.kill(reason=args.reason, killed=False)
        _print({"ok": True, "owner_control": owner.snapshot()})
        return 0
    if args.command == "revoke":
        owner.revoke(reason=args.reason, revoked=True)
        _print({"ok": True, "owner_control": owner.snapshot()})
        return 0
    if args.command == "clear-revoke":
        owner.revoke(reason=args.reason, revoked=False)
        _print({"ok": True, "owner_control": owner.snapshot()})
        return 0
    if args.command == "grant-lease":
        expires_at = owner.grant_lease(
            approved_by=args.approved_by,
            hours=args.hours,
            evidence=args.evidence,
        )
        _print(
            {
                "ok": True,
                "lease_expires_at": expires_at,
                "owner_control": owner.snapshot(),
            }
        )
        return 0
    if args.command == "approve":
        approval_id = owner.approve_once(
            args.action,
            args.args_json,
            approved_by=args.approved_by,
            evidence=args.evidence,
            ttl_seconds=args.ttl_seconds,
        )
        _print(
            {
                "ok": True,
                "approval_id": approval_id,
                "action": str(args.action),
                "single_use": True,
            }
        )
        return 0
    if args.command == "reconcile-effect":
        record = effects.reconcile(
            args.effect_id,
            remote_effect_observed=bool(args.effect_observed),
            evidence=args.evidence,
        )
        _print({"ok": True, "effect": record.as_dict()})
        return 0

    raise AssertionError(f"unhandled control command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
