from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hmac
import json
import math
import os
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .resource_ingress import ResourceIngressRegistry
from .tools import ToolResult


class AttestedResourceIngressRegistry(ResourceIngressRegistry):
    """Canonical ingress registry with provider/settlement attestation constraints.

    The base registry already supplies replay resistance, exact claim receipts and
    separation from WorkPort acceptance. This production wrapper additionally refuses
    generic "observed amount" JSON. A configured verifier must attest an immutable
    provider event, account binding and final settlement state, and the amount must stay
    inside deployment-owned constraints. When linked to Resource Ecology work, the
    opportunity's target amount becomes the default upper-bound/equality reference.
    """

    FINAL_SETTLEMENT_STATUSES = frozenset({"settled", "confirmed", "completed", "paid"})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._active_verifier_name: str | None = None
        self._active_verifier_config: dict[str, Any] | None = None
        self._active_target_amount: float | None = None
        self._active_work_item_id: int | None = None
        super().__init__(*args, **kwargs)

    @property
    def provider_authentication_ready(self) -> bool:
        for item in self.verifiers().values():
            required = (
                "expected_provider",
                "expected_account_binding",
                "provider_verify_key_env",
            )
            if any(
                not isinstance(item.get(field), str)
                or not str(item.get(field)).strip()
                for field in required
            ):
                return False
            if os.getenv(str(item.get("provider_verify_key_env", "")).strip()) is None:
                return False
        return bool(self.verifiers())

    def _verifier(self, name: str) -> dict[str, Any]:
        item = super()._verifier(name)
        self._active_verifier_name = str(name)
        self._active_verifier_config = dict(item)
        return item

    def _validate_work_target(
        self,
        work_item_id: int | None,
        *,
        asset: str,
        unit: str,
    ) -> None:
        self._active_target_amount = None
        self._active_work_item_id = int(work_item_id) if work_item_id is not None else None
        super()._validate_work_target(work_item_id, asset=asset, unit=unit)
        if work_item_id is None:
            return
        work = self.resource_ecology.work_item(int(work_item_id))
        if work is None:
            raise ValueError(f"work item does not exist: {work_item_id}")
        profile = self.resource_ecology.profile(work.opportunity_id)
        if profile is None:
            raise ValueError("linked work has no resource profile")
        target = float(profile.target_amount)
        if math.isfinite(target) and target > 0:
            self._active_target_amount = target

    @staticmethod
    def _clean_attestation(value: Any, field: str, maximum: int = 512) -> str:
        if not isinstance(value, str):
            raise ValueError(
                f"resource verifier attestation requires {field} as a string"
            )
        text = value.strip()[:maximum]
        if not text:
            raise ValueError(f"resource verifier attestation requires {field}")
        return text

    def _machine_object(self, result: ToolResult) -> dict[str, Any]:
        structured = super()._machine_object(result)
        if not bool(structured.get("observed", True)):
            return structured

        provider = self._clean_attestation(structured.get("provider"), "provider", 128)
        provider_event_id = self._clean_attestation(
            structured.get("provider_event_id"), "provider_event_id", 2000
        )
        account_binding = self._clean_attestation(
            structured.get("account_binding"), "account_binding", 512
        )
        settlement_status = self._clean_attestation(
            structured.get("settlement_status"), "settlement_status", 64
        ).lower()
        if settlement_status not in self.FINAL_SETTLEMENT_STATUSES:
            raise PermissionError(
                "resource verifier event is not finally settled: " + settlement_status
            )

        external_event_id = self._clean_attestation(
            structured.get("external_event_id"), "external_event_id", 2000
        )
        if provider_event_id != external_event_id:
            raise PermissionError(
                "provider_event_id must equal the replay identity external_event_id"
            )

        config = self._active_verifier_config or {}
        expected_provider = self._clean_attestation(
            config.get("expected_provider"), "configured expected_provider", 128
        )
        expected_account = self._clean_attestation(
            config.get("expected_account_binding"),
            "configured expected_account_binding",
            512,
        )
        if not hmac.compare_digest(provider, expected_provider):
            raise PermissionError("provider identity does not match configured verifier scope")
        if not hmac.compare_digest(account_binding, expected_account):
            raise PermissionError("account binding does not match configured verifier scope")

        try:
            amount = float(structured.get("amount", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("resource verifier amount must be numeric") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("resource verifier amount must be a finite positive number")

        minimum = config.get("min_amount")
        maximum = config.get("max_amount")
        expected = config.get("expected_amount")
        tolerance = float(config.get("amount_tolerance", 0.0) or 0.0)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("amount_tolerance must be finite and nonnegative")
        configured_numbers: dict[str, float] = {}
        for field, configured in (
            ("min_amount", minimum),
            ("max_amount", maximum),
            ("expected_amount", expected),
        ):
            if configured is None:
                continue
            number = float(configured)
            if not math.isfinite(number):
                raise ValueError(f"{field} must be finite")
            configured_numbers[field] = number
        if minimum is not None and amount < configured_numbers["min_amount"]:
            raise PermissionError("settled amount is below configured verifier minimum")
        if maximum is not None and amount > configured_numbers["max_amount"]:
            raise PermissionError("settled amount exceeds configured verifier maximum")
        if expected is not None and abs(amount - configured_numbers["expected_amount"]) > tolerance:
            raise PermissionError("settled amount differs from configured expected amount")
        if self._active_target_amount is not None:
            configured_target_tolerance = float(
                config.get("target_amount_tolerance", 0.0) or 0.0
            )
            if not math.isfinite(configured_target_tolerance) or configured_target_tolerance < 0:
                raise ValueError("target_amount_tolerance must be finite and nonnegative")
            target_tolerance = max(tolerance, configured_target_tolerance)
            if abs(amount - self._active_target_amount) > target_tolerance:
                raise PermissionError(
                    "settled amount does not match the accepted work target amount"
                )

        provider_evidence = self._clean_attestation(
            structured.get("evidence"), "provider evidence", 8000
        )
        signed_asset = self._clean_attestation(structured.get("asset"), "signed asset", 128)
        signed_unit = self._clean_attestation(structured.get("unit"), "signed unit", 64)
        signed_kind = self._clean_attestation(structured.get("kind"), "signed kind", 64)
        for field, signed in (
            ("asset", signed_asset),
            ("unit", signed_unit),
            ("kind", signed_kind),
        ):
            expected = self._clean_attestation(config.get(field), f"configured {field}", 128)
            if not hmac.compare_digest(signed, expected):
                raise PermissionError(f"signed {field} does not match configured verifier scope")

        settled_at_text = self._clean_attestation(
            structured.get("settled_at"), "settled_at", 128
        )
        try:
            settled_at = datetime.fromisoformat(settled_at_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("settled_at must be an ISO-8601 timestamp") from exc
        if settled_at.tzinfo is None:
            raise ValueError("settled_at must include a timezone")
        age_seconds = (
            datetime.now(timezone.utc) - settled_at.astimezone(timezone.utc)
        ).total_seconds()
        maximum_age = float(config.get("max_attestation_age_seconds", 604800.0))
        if not math.isfinite(maximum_age) or maximum_age <= 0:
            raise ValueError("max_attestation_age_seconds must be finite and positive")
        if age_seconds < -300.0 or age_seconds > maximum_age:
            raise PermissionError("provider attestation is outside the configured freshness window")

        signed_work_item = structured.get("work_item_id")
        if signed_work_item is not None:
            if isinstance(signed_work_item, bool) or not isinstance(signed_work_item, int):
                raise ValueError("signed work_item_id must be an integer or null")
        if signed_work_item != self._active_work_item_id:
            raise PermissionError("signed work_item_id does not match the requested verifier scope")

        claim = {
            "provider": provider,
            "provider_event_id": provider_event_id,
            "external_event_id": external_event_id,
            "account_binding": account_binding,
            "settlement_status": settlement_status,
            "asset": signed_asset,
            "unit": signed_unit,
            "kind": signed_kind,
            "amount": structured.get("amount"),
            "settled_at": settled_at_text,
            "work_item_id": signed_work_item,
            "evidence": provider_evidence,
        }
        signature_text = self._clean_attestation(
            structured.get("attestation_signature"), "attestation_signature", 512
        )
        key_env = self._clean_attestation(
            config.get("provider_verify_key_env"), "provider_verify_key_env", 256
        )
        encoded_key = os.getenv(key_env, "").strip()
        if not encoded_key:
            raise RuntimeError("provider verification public key is missing")
        try:
            if encoded_key.startswith("base64:"):
                encoded_key = encoded_key.split(":", 1)[1].strip()
            try:
                verify_key_raw = base64.b64decode(encoded_key, validate=True)
            except (ValueError, binascii.Error):
                verify_key_raw = bytes.fromhex(encoded_key)
            signature = base64.b64decode(signature_text, validate=True)
            VerifyKey(verify_key_raw).verify(
                json.dumps(
                    claim,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
                signature,
            )
        except (ValueError, binascii.Error, BadSignatureError) as exc:
            raise PermissionError("provider attestation signature verification failed") from exc

        # Bind the human/audit-readable evidence to the provider/account/final status
        # after external authentication and before the local receipt kernel signs its
        # separately scoped exact economic claim.
        structured["evidence"] = (
            f"provider_signature=ed25519; provider={provider}; account={account_binding}; status={settlement_status}; "
            f"event={provider_event_id}; evidence={provider_evidence}"
        )[:8000]
        return structured
