from __future__ import annotations

import math
from typing import Any

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
        super().__init__(*args, **kwargs)

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
        text = str(value).strip()[:maximum]
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

        try:
            amount = float(structured.get("amount", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("resource verifier amount must be numeric") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("resource verifier amount must be a finite positive number")

        config = self._active_verifier_config or {}
        minimum = config.get("min_amount")
        maximum = config.get("max_amount")
        expected = config.get("expected_amount")
        tolerance = max(0.0, float(config.get("amount_tolerance", 0.0) or 0.0))
        if minimum is not None and amount < float(minimum):
            raise PermissionError("settled amount is below configured verifier minimum")
        if maximum is not None and amount > float(maximum):
            raise PermissionError("settled amount exceeds configured verifier maximum")
        if expected is not None and abs(amount - float(expected)) > tolerance:
            raise PermissionError("settled amount differs from configured expected amount")
        if self._active_target_amount is not None:
            target_tolerance = max(
                tolerance,
                float(config.get("target_amount_tolerance", 0.0) or 0.0),
            )
            if abs(amount - self._active_target_amount) > target_tolerance:
                raise PermissionError(
                    "settled amount does not match the accepted work target amount"
                )

        provider_evidence = self._clean_attestation(
            structured.get("evidence"), "provider evidence", 8000
        )
        # Bind the human/audit-readable evidence to the provider/account/final status
        # before the base registry constructs and signs the exact economic claim.
        structured["evidence"] = (
            f"provider={provider}; account={account_binding}; status={settlement_status}; "
            f"event={provider_event_id}; evidence={provider_evidence}"
        )[:8000]
        return structured
