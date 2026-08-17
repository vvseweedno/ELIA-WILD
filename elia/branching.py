from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from typing import Any

from . import __version__
from .chronicle import Chronicle
from .config import Config, load_config
from .identity import IdentityBundle, IdentityStore
from .memory import MemoryStore


BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class BranchForkReport:
    from_branch: str
    to_branch: str
    identity_fingerprint: str
    lineage_event_id: int
    parent_checkpoint_digest: str | None
    archived_crc_path: str | None
    archived_crc_sha256: str | None
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BranchManager:
    """Create an explicit identity branch while preserving verifiable ancestry.

    Forking changes branch lineage, not the immutable Subject Core. The parent branch's
    last healthy CRC is archived and removed from the active baseline so the child
    branch establishes its own continuity baseline on the next vital-sign check.
    """

    def __init__(self, config: Config):
        self.config = config
        self.state_dir = config.runtime.state_dir
        self.database = self.state_dir / "memory.sqlite3"
        self.memory = MemoryStore(self.database)
        self.identity_store = IdentityStore(self.database)
        self.identity = IdentityBundle.load(
            config.subject_core_path,
            config.continuity_constitution_path,
        )
        self.chronicle = Chronicle(self.state_dir / "chronicle.jsonl")

    def current_branch(self) -> str:
        persisted = self.memory.get_meta("branch_id")
        if persisted:
            return str(persisted)
        head = self.identity_store.last_lineage()
        return head.branch_id if head else self.config.branch_id

    def fork(self, new_branch: str, *, note: str) -> BranchForkReport:
        new_branch = str(new_branch).strip()
        if not BRANCH_RE.fullmatch(new_branch):
            raise ValueError("branch id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        note = str(note).strip()[:4000]
        if not note:
            raise ValueError("fork note is required to explain why the lineage branches")

        valid, error = self.chronicle.verify()
        if not valid:
            raise RuntimeError(f"cannot fork from invalid Chronicle: {error}")
        identity_valid, identity_error = self.identity_store.verify_identity_fingerprint(
            self.identity.fingerprint
        )
        if not identity_valid:
            raise RuntimeError(f"cannot fork invalid identity state: {identity_error}")

        old_branch = self.current_branch()
        if new_branch == old_branch:
            raise ValueError("new branch must differ from current branch")
        lineage_valid, lineage_error = self.identity_store.verify_lineage(
            expected_identity_fingerprint=self.identity.fingerprint,
            expected_branch_id=old_branch,
        )
        if not lineage_valid:
            raise RuntimeError(f"cannot fork invalid lineage: {lineage_error}")

        organism_dir = self.state_dir / "workspace" / ".organism"
        baseline = organism_dir / "last-healthy-crc.json"
        archived_path: Path | None = None
        archived_sha: str | None = None
        if baseline.is_file():
            archive_dir = organism_dir / "branch-ancestors"
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            archived_path = archive_dir / f"{old_branch}--to--{new_branch}--{stamp}.crc.json"
            shutil.copy2(baseline, archived_path)
            archived_sha = sha256(archived_path.read_bytes()).hexdigest()

        checkpoint_digest = self.memory.get_meta("checkpoint_digest")
        event_id = self.identity_store.record_lineage(
            event="fork",
            branch_id=new_branch,
            body_version=self.memory.get_meta("body_version", __version__) or __version__,
            brain_backend=self.config.brain.backend,
            model_id=self.config.brain.model_id,
            identity_fingerprint=self.identity.fingerprint,
            checkpoint_digest=checkpoint_digest,
            parent_checkpoint_digest=checkpoint_digest,
            note=f"forked from branch {old_branch}: {note}",
        )
        self.memory.set_meta("branch_id", new_branch)
        self.memory.set_meta("branch_parent_id", old_branch)
        self.memory.set_meta("branch_fork_lineage_event", str(event_id))
        self.memory.set_meta("branch_fork_note", note)

        payload = {
            "from_branch": old_branch,
            "to_branch": new_branch,
            "identity_fingerprint": self.identity.fingerprint,
            "lineage_event_id": event_id,
            "parent_checkpoint_digest": checkpoint_digest,
            "archived_crc_path": (
                str(archived_path.relative_to(self.state_dir)) if archived_path else None
            ),
            "archived_crc_sha256": archived_sha,
            "note": note,
        }
        self.chronicle.append("BRANCH_FORK", payload)

        # The child branch must establish its own baseline. Parent evidence remains
        # archived and longitudinal observations remain in SQLite as ancestry.
        baseline.unlink(missing_ok=True)
        (organism_dir / "vitals.json").unlink(missing_ok=True)

        return BranchForkReport(
            from_branch=old_branch,
            to_branch=new_branch,
            identity_fingerprint=self.identity.fingerprint,
            lineage_event_id=event_id,
            parent_checkpoint_digest=checkpoint_digest,
            archived_crc_path=str(archived_path) if archived_path else None,
            archived_crc_sha256=archived_sha,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elia-fork")
    parser.add_argument("new_branch")
    parser.add_argument("--config", default="config/genesis.yaml")
    parser.add_argument("--note", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = BranchManager(load_config(args.config)).fork(args.new_branch, note=args.note)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
