from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import secrets
import sqlite3
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

    @property
    def fingerprint(self) -> str:
        """Stable digest of the complete signed receipt."""
        return _digest(self.as_dict())


class VerificationRegistry:
    """Cryptographic trust root for verified external facts.

    Keys are supplied by trusted runtime/infrastructure configuration, never by the
    language model or the claim being verified. HMAC is intentionally used here for
    small local adapters: the store can authenticate a receipt without trusting a
    caller-provided authority string. External/public verifiers can later replace this
    backend with an asymmetric signature implementation behind the same receipt shape.

    Signature verification is pure. Single-use semantics are enforced by
    ``consume_verified_receipt`` inside the same SQLite transaction as the authorized
    state change so concurrent replay and restart cannot re-mint the same fact.
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
        nonce = str(nonce or secrets.token_hex(16)).strip()[:128]
        if not nonce:
            raise ValueError("verification receipt nonce is required")
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
        authority = str(receipt.authority).strip()
        nonce = str(receipt.nonce).strip()
        if not authority or not nonce:
            raise PermissionError("verification receipt authority and nonce are required")
        key = self._keys.get(authority)
        if key is None:
            raise PermissionError(f"untrusted verification authority: {authority!r}")
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
                authority,
                receipt.claim_sha256,
                receipt.evidence_sha256,
                nonce,
            ),
            sha256,
        ).hexdigest()
        if not hmac.compare_digest(receipt.signature, expected):
            raise PermissionError("verification receipt signature mismatch")
        return authority


def ensure_receipt_ledger(conn: sqlite3.Connection) -> None:
    """Create the durable global single-use receipt ledger on an existing DB."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS verification_receipt_consumptions (
            authority TEXT NOT NULL,
            nonce TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            consumed_at TEXT NOT NULL,
            purpose TEXT NOT NULL,
            subject_ref TEXT NOT NULL DEFAULT '',
            claim_sha256 TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            PRIMARY KEY(authority, nonce)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_verification_receipt_purpose
        ON verification_receipt_consumptions(purpose, consumed_at)
        """
    )


def consume_verified_receipt(
    conn: sqlite3.Connection,
    registry: VerificationRegistry,
    receipt: VerificationReceipt,
    *,
    claim: Any,
    evidence: str,
    purpose: str,
    subject_ref: str = "",
) -> str:
    """Verify and atomically consume one signed receipt exactly once.

    The caller must invoke this using the same SQLite connection/transaction that
    performs the authorized domain mutation. `(authority, nonce)` prevents replay and
    concurrent races while ``receipt_sha256`` also protects against accidental token
    reuse under schema drift.
    """

    authority = registry.verify(receipt, claim=claim, evidence=evidence)
    purpose = str(purpose).strip()[:128]
    if not purpose:
        raise ValueError("verification receipt purpose is required")
    subject_ref = str(subject_ref).strip()[:512]
    ensure_receipt_ledger(conn)
    try:
        conn.execute(
            """
            INSERT INTO verification_receipt_consumptions(
                authority, nonce, receipt_sha256, consumed_at, purpose, subject_ref,
                claim_sha256, evidence_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authority,
                str(receipt.nonce).strip(),
                receipt.fingerprint,
                datetime.now(timezone.utc).isoformat(),
                purpose,
                subject_ref,
                receipt.claim_sha256,
                receipt.evidence_sha256,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise PermissionError(
            f"verification receipt already consumed: authority={authority!r}, nonce={receipt.nonce!r}"
        ) from exc
    return authority
