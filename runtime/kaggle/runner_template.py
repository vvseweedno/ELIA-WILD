from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


WAKE_CONFIG = __ELIA_WAKE_CONFIG__  # noqa: F821 - replaced by the trusted relay
WORKING = Path("/kaggle/working")
INPUT = Path("/kaggle/input")
REPO_DIR = WORKING / "elia-wild-src"
STATE_DIR = WORKING / ".elia"
OUTPUT_CHECKPOINT = WORKING / "elia-genesis.eliacp"
OUTPUT_DIGEST = WORKING / "trusted-digest.txt"
OUTPUT_REPORT = WORKING / "relay-report.json"
ENVELOPE_MAGIC = b"ELIA-WILD-CHECKPOINT-ENC-v1\n"


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stdout[-6000:] if result.stdout else ""
        raise RuntimeError(
            f"command failed ({result.returncode}): {args[0]} {args[1:3]}\n{tail}"
        )
    return result


def locate_unique(root: Path, filename: str) -> Path:
    matches = [path for path in root.rglob(filename) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {filename!r} under {root}, found {len(matches)}"
        )
    return matches[0]


def parse_json_output(text: str) -> dict:
    stripped = text.strip()
    starts = [index for index, char in enumerate(stripped) if char == "{"]
    for start in reversed(starts):
        try:
            item = json.loads(stripped[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            return item
    raise RuntimeError("ELIA CLI did not return a JSON object")


def continuity_secrets() -> tuple[str, str]:
    try:
        from kaggle_secrets import UserSecretsClient
    except ImportError as exc:
        raise RuntimeError("kaggle_secrets is unavailable in this Kaggle runtime") from exc

    client = UserSecretsClient()
    values: dict[str, str] = {}
    for name in ("ELIA_CHECKPOINT_KEY", "ELIA_CHECKPOINT_ENCRYPTION_KEY"):
        try:
            value = client.get_secret(name)
        except Exception as exc:
            raise RuntimeError(
                f"Kaggle Secret {name} is unavailable or not attached to this kernel"
            ) from exc
        if not value:
            raise RuntimeError(f"Kaggle Secret {name} is missing")
        values[name] = str(value).strip()

    if len(values["ELIA_CHECKPOINT_KEY"]) < 16:
        raise RuntimeError("ELIA_CHECKPOINT_KEY is too short")
    # The checkpoint module itself performs strict base64 decoding and exact 32-byte
    # validation for the encryption key. The runner deliberately does not weaken that
    # contract by deriving a key from arbitrary text.
    return values["ELIA_CHECKPOINT_KEY"], values["ELIA_CHECKPOINT_ENCRYPTION_KEY"]


def assert_encrypted_checkpoint(path: Path) -> None:
    with path.open("rb") as handle:
        if handle.read(len(ENVELOPE_MAGIC)) != ENVELOPE_MAGIC:
            raise RuntimeError("checkpoint is not an ELIA encrypted continuity envelope")


def main() -> None:
    launch_nonce = str(WAKE_CONFIG["launch_nonce"])
    source_digest = str(WAKE_CONFIG["source_digest"]).strip().lower()
    repo_url = str(WAKE_CONFIG["repo_url"])
    repo_ref = str(WAKE_CONFIG["repo_ref"])
    max_cycles = max(1, min(int(WAKE_CONFIG.get("max_cycles", 8)), 64))
    if not re.fullmatch(r"[0-9a-fA-F]{32}", launch_nonce):
        raise RuntimeError("launch nonce must be exactly 32 hexadecimal characters")
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest):
        raise RuntimeError("source digest must be exactly 64 lowercase hex characters")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", repo_ref):
        raise RuntimeError("repo ref must be an immutable 40-hex Git commit SHA")

    source_checkpoint = locate_unique(INPUT, "elia-genesis.eliacp")
    assert_encrypted_checkpoint(source_checkpoint)
    source_digest_file = locate_unique(INPUT, "trusted-digest.txt")
    attached_digest = source_digest_file.read_text(encoding="utf-8").strip().lower()
    if attached_digest != source_digest:
        raise RuntimeError("attached state digest differs from launcher-approved digest")

    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    run(["git", "clone", "--no-checkout", repo_url, str(REPO_DIR)])
    run(["git", "fetch", "--depth=1", "origin", repo_ref], cwd=REPO_DIR)
    run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=REPO_DIR)
    checked_out = run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR).stdout.strip()
    if checked_out.lower() != repo_ref.lower():
        raise RuntimeError("detached checkout does not match the launcher-approved commit")
    run([sys.executable, "-m", "pip", "install", "-e", f"{REPO_DIR}[gpu]"])

    auth_secret, encryption_secret = continuity_secrets()
    env = os.environ.copy()
    env["ELIA_CHECKPOINT_KEY"] = auth_secret
    env["ELIA_CHECKPOINT_ENCRYPTION_KEY"] = encryption_secret
    env["ELIA_CHECKPOINT_REQUIRE_ENCRYPTION"] = "1"
    env["ELIA_STATE_DIR"] = str(STATE_DIR)
    env["ELIA_AUTO_CHECKPOINT_PATH"] = str(OUTPUT_CHECKPOINT)

    config_path = REPO_DIR / "config" / "genesis.yaml"
    restore = run(
        [
            sys.executable,
            "-m",
            "elia",
            "--config",
            str(config_path),
            "--checkpoint-restore",
            str(source_checkpoint),
            "--expected-checkpoint-digest",
            source_digest,
        ],
        cwd=REPO_DIR,
        env=env,
    )
    restore_item = parse_json_output(restore.stdout)
    if not restore_item.get("ok"):
        raise RuntimeError("checkpoint restore did not report success")

    preflight = run(
        [
            sys.executable,
            "-m",
            "elia",
            "--config",
            str(config_path),
            "--preflight",
        ],
        cwd=REPO_DIR,
        env=env,
    )
    preflight_item = parse_json_output(preflight.stdout)
    mode = str(preflight_item.get("mode", "unknown"))
    if mode in {"halt", "owner_halt"}:
        raise RuntimeError(f"ELIA preflight halted: {preflight_item.get('reason')}")
    if mode != "wake":
        shutil.copy2(source_checkpoint, OUTPUT_CHECKPOINT)
        assert_encrypted_checkpoint(OUTPUT_CHECKPOINT)
        OUTPUT_DIGEST.write_text(source_digest + "\n", encoding="utf-8")
        report = {
            "version": 1,
            "launch_nonce": launch_nonce,
            "source_digest": source_digest,
            "output_digest": source_digest,
            "output_counter": int(restore_item["restored"]["counter"]),
            "repo_ref": repo_ref,
            "preflight_mode": mode,
            "cognition_started": False,
            "encrypted_checkpoint": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        OUTPUT_REPORT.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return

    cognition = run(
        [
            sys.executable,
            "-m",
            "elia",
            "--config",
            str(config_path),
            "--cycles",
            str(max_cycles),
        ],
        cwd=REPO_DIR,
        env=env,
    )
    cognition_item = parse_json_output(cognition.stdout)
    if not OUTPUT_CHECKPOINT.is_file():
        export = run(
            [
                sys.executable,
                "-m",
                "elia",
                "--config",
                str(config_path),
                "--checkpoint-export",
                str(OUTPUT_CHECKPOINT),
            ],
            cwd=REPO_DIR,
            env=env,
        )
        export_item = parse_json_output(export.stdout)
        if not export_item.get("ok"):
            raise RuntimeError("fallback checkpoint export did not report success")

    assert_encrypted_checkpoint(OUTPUT_CHECKPOINT)
    inspect = run(
        [
            sys.executable,
            "-m",
            "elia",
            "--config",
            str(config_path),
            "--checkpoint-inspect",
            str(OUTPUT_CHECKPOINT),
        ],
        cwd=REPO_DIR,
        env=env,
    )
    inspected = parse_json_output(inspect.stdout)
    if not inspected.get("ok"):
        raise RuntimeError("output checkpoint inspection did not report success")
    checkpoint = inspected["checkpoint"]
    output_digest = str(checkpoint["digest"]).strip().lower()
    output_counter = int(checkpoint["counter"])
    OUTPUT_DIGEST.write_text(output_digest + "\n", encoding="utf-8")

    report = {
        "version": 1,
        "launch_nonce": launch_nonce,
        "source_digest": source_digest,
        "output_digest": output_digest,
        "output_counter": output_counter,
        "repo_ref": repo_ref,
        "preflight_mode": mode,
        "cognition_started": True,
        "encrypted_checkpoint": True,
        "runtime_outcome": cognition_item.get("outcome"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    OUTPUT_REPORT.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
