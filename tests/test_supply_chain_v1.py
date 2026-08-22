from __future__ import annotations

import ast
from pathlib import Path
import re
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_code_does_not_use_optimization_sensitive_assertions() -> None:
    runtime_files = [
        *sorted((ROOT / "elia").rglob("*.py")),
        *sorted((ROOT / "scripts").rglob("*.py")),
        ROOT / "runtime" / "kaggle" / "runner_template.py",
    ]
    violations: list[tuple[str, int]] = []
    for path in runtime_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            (str(path.relative_to(ROOT)), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        )
    assert violations == []


def test_every_external_github_action_is_pinned_to_a_full_commit_sha() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        yaml.safe_load(text)
        references = re.findall(r"\buses:\s*([^@\s]+)@([^\s#]+)", text)
        for action, reference in references:
            assert re.fullmatch(r"[0-9a-f]{40}", reference), (
                workflow.name,
                action,
                reference,
            )


def test_codeql_and_dependency_update_workflows_are_present() -> None:
    codeql_text = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(
        encoding="utf-8"
    )
    codeql = yaml.safe_load(
        codeql_text
    )
    assert codeql["permissions"]["security-events"] == "write"
    assert codeql["jobs"]["analyze"]["timeout-minutes"] == 20
    assert codeql_text.count(
        "github/codeql-action/"
        "init@db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28"
    ) == 1
    assert codeql_text.count(
        "github/codeql-action/"
        "analyze@db488ddef3bf6cb639b32c2e9a7c0a7ea8271d28"
    ) == 1

    dependabot = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {item["package-ecosystem"] for item in dependabot["updates"]}
    assert ecosystems == {"pip", "github-actions"}

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "sha256sum --check dist/SHA256SUMS" in ci_text
    assert "cyclonedx-bom==7.3.1" in ci_text
    assert "--output-reproducible" in ci_text
    assert "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d" in ci_text
    assert "packaged source manifest differs" in ci_text
    assert "Full typed correctness gate" in ci_text
    assert "--warn-unused-ignores" in ci_text
    assert "elia scripts" in ci_text
    assert "--follow-imports=skip" not in ci_text
    assert "SOURCE_DATE_EPOCH=\"$(git log -1 --format=%ct)\"" in ci_text
    assert "release_tools/normalize_sdist.py" in ci_text
    assert 'cmp "$RUNNER_TEMP"/elia-dist-a/*.whl' in ci_text
    assert 'cmp "$RUNNER_TEMP"/elia-dist-a/*.tar.gz' in ci_text


def test_gpu_direct_dependencies_and_operational_entrypoints_are_immutable() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    gpu = project["project"]["optional-dependencies"]["gpu"]
    assert "torch==2.13.0" in gpu
    assert "torchvision==0.28.0" in gpu
    assert "Pillow==12.3.0" in gpu
    assert "accelerate==1.14.0" in gpu
    assert "bitsandbytes==0.50.1" in gpu
    transformers = next(item for item in gpu if item.startswith("transformers @ git+"))
    assert re.search(r"@[0-9a-f]{40}$", transformers)

    brain_text = (ROOT / "elia" / "brain.py").read_text(encoding="utf-8")
    assert "dtype=torch.float16" in brain_text
    assert "torch_dtype=" not in brain_text

    scripts = project["project"]["scripts"]
    assert scripts["elia-kaggle-wake"] == "scripts.kaggle_wake:main"
    assert scripts["elia-kaggle-bootstrap"] == "scripts.bootstrap_kaggle_state:main"
    kaggle_assets = project["tool"]["setuptools"]["data-files"][
        "share/elia-wild/runtime/kaggle"
    ]
    assert "runtime/kaggle/runner_template.py" in kaggle_assets
