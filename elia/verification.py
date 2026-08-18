from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import hmac
import json
import secrets
from typing import Any, Mapping


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class VerificationReceipt:
    authority: str
    claim_sha256: str
    evidence_sha256: str
    nonce: str
    signature: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class VerificationRegistry:
    """Cryptographic trust root for verified external facts.

    Keys are supplied by trusted runtime/infrastructure configuration, never by the
    language model or the claim being verified. HMAC is intentionally used here for
    small local adapters: the store can authenticate a receipt without trusting a
    caller-provided authority string. External/public verifiers can later replace this
    backend with an asymmetric signature implementation behind the same receipt shape.
    """

    def __init__(self, authority_keys: Mapping[str, bytes | str]):
        keys: dict[str, bytes] = {}
        for raw_name, raw_key in authority_keys.items():
            name = str(raw_name).strip()[:256]
            key = raw_key.encode("utf-8") if isinstance(raw_key, str) else bytes(raw_key)
            if not name:
                raise ValueError("verification authority name is required")
            if len(key) < 16:
                raise ValueError(f"verification key for {name!r} must be at least 16 bytes")
            keys[name] = key
        if not keys:
            raise ValueError("at least one verification authority is required")
        self._keys = keys

    @staticmethod
    def _material(authority: str, claim_sha256: str, evidence_sha256: str, nonce: str) -> bytes:
        return _canonical(
            {
                "authority": authority,
                "claim_sha256": claim_sha256,
                "evidence_sha256": evidence_sha256,
                "nonce": nonce,
            }
        )

    def issue(
        self,
        authority: str,
        *,
        claim: Any,
        evidence: str,
        nonce: str | None = None,
    ) -> VerificationReceipt:
        authority = str(authority).strip()
        key = self._keys.get(authority)
        if key is None:
            raise PermissionError(f"unknown verification authority: {authority!r}")
        evidence_text = str(evidence).strip()
        if not evidence_text:
            raise ValueError("verification evidence is required")
        claim_sha256 = _digest(claim)
        evidence_sha256 = sha256(evidence_text.encode("utf-8")).hexdigest()
        nonce = str(nonce or secrets.token_hex(16))[:128]
        signature = hmac.new(
            key,
            self._material(authority, claim_sha256, evidence_sha256, nonce),
            sha256,
        ).hexdigest()
        return VerificationReceipt(
            authority=authority,
            claim_sha256=claim_sha256,
            evidence_sha256=evidence_sha256,
            nonce=nonce,
            signature=signature,
        )

    def verify(self, receipt: VerificationReceipt, *, claim: Any, evidence: str) -> str:
        if not isinstance(receipt, VerificationReceipt):
            raise TypeError("verified state requires a VerificationReceipt")
        key = self._keys.get(receipt.authority)
        if key is None:
            raise PermissionError(f"untrusted verification authority: {receipt.authority!r}")
        evidence_text = str(evidence).strip()
        if not evidence_text:
            raise ValueError("verification evidence is required")
        claim_sha256 = _digest(claim)
        evidence_sha256 = sha256(evidence_text.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(receipt.claim_sha256, claim_sha256):
            raise PermissionError("verification receipt claim digest mismatch")
        if not hmac.compare_digest(receipt.evidence_sha256, evidence_sha256):
            raise PermissionError("verification receipt evidence digest mismatch")
        expected = hmac.new(
            key,
            self._material(
                receipt.authority,
                receipt.claim_sha256,
                receipt.evidence_sha256,
                receipt.nonce,
            ),
            sha256,
        ).hexdigest()
        if not hmac.compare_digest(receipt.signature, expected):
            raise PermissionError("verification receipt signature mismatch")
        return receipt.authority
