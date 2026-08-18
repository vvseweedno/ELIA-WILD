from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
import importlib
import importlib.util
import json
from pathlib import Path
import pkgutil
import re
import tomllib
from typing import Iterable

import yaml


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
TEXT_SUFFIXES = {".py", ".toml", ".yaml", ".yml", ".md", ".service", ".txt"}
PATH_PREFIXES = (
    "config/",
    "deploy/",
    "docs/",
    "elia/",
    "runtime/",
    "scripts/",
    "skills/",
    "tests/",
    "tools/",
    ".github/",
)
PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_:/.-])"
    r"((?:\.github|config|deploy|docs|elia|runtime|scripts|skills|tests|tools)/"
    r"[A-Za-z0-9_./-]+(?:\.py|\.ya?ml|\.md|\.toml|\.ipynb|\.service))"
)
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    code: str
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }


class RepositoryAudit:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root).resolve()
        self.findings: list[Finding] = []

    def add(self, severity: str, code: str, location: Path | str, message: str) -> None:
        try:
            rendered = str(Path(location).resolve().relative_to(self.root))
        except (ValueError, TypeError):
            rendered = str(location)
        self.findings.append(Finding(severity, code, rendered, message))

    def _iter_text_files(self) -> Iterable[Path]:
        ignored = {".git", ".elia", ".pytest_cache", "__pycache__", ".venv", "dist", "build"}
        for path in self.root.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.parts):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"README.md", ".gitignore"}:
                yield path

    def check_python_syntax_and_internal_imports(self) -> None:
        python_files = sorted(
            path
            for path in self.root.rglob("*.py")
            if ".git" not in path.parts and ".elia" not in path.parts and ".venv" not in path.parts
        )
        for path in python_files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeError) as exc:
                self.add("error", "python.syntax", path, str(exc))
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    if name == "elia" or name.startswith("elia."):
                        try:
                            spec = importlib.util.find_spec(name)
                        except (ImportError, AttributeError, ModuleNotFoundError, ValueError) as exc:
                            self.add("error", "python.import", path, f"cannot resolve {name!r}: {exc}")
                            continue
                        if spec is None:
                            self.add("error", "python.import", path, f"cannot resolve internal import {name!r}")

    def check_all_package_modules_import(self) -> None:
        package = importlib.import_module("elia")
        failures: list[str] = []
        for item in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            try:
                importlib.import_module(item.name)
            except Exception as exc:  # import smoke intentionally catches runtime import failures
                failures.append(f"{item.name}: {type(exc).__name__}: {exc}")
        for failure in failures:
            self.add("error", "python.import-smoke", "elia", failure[:2000])

    def check_entrypoints(self) -> None:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {})
        for name, target in sorted(scripts.items()):
            module_name, separator, symbol = str(target).partition(":")
            if not separator or not module_name or not symbol:
                self.add("error", "entrypoint.syntax", PYPROJECT, f"{name} has invalid target {target!r}")
                continue
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                self.add("error", "entrypoint.module", PYPROJECT, f"{name}: {module_name}: {type(exc).__name__}: {exc}")
                continue
            value = getattr(module, symbol, None)
            if not callable(value):
                self.add("error", "entrypoint.symbol", PYPROJECT, f"{name}: {target} is not callable")

    def check_genesis_paths(self) -> None:
        path = self.root / "config" / "genesis.yaml"
        if not path.is_file():
            self.add("error", "config.genesis", path, "canonical Genesis config is missing")
            return
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        identity = raw.get("identity") or {}
        config_relative = {
            "subject_core": identity.get("subject_core"),
            "continuity_constitution": identity.get("continuity_constitution"),
            "system_prompt": identity.get("system_prompt"),
            "epistemic_registry": raw.get("epistemic_registry", "epistemic.yaml"),
        }
        for key, value in config_relative.items():
            if not value:
                self.add("error", "config.path", path, f"missing {key}")
                continue
            candidate = (path.parent / str(value)).resolve()
            if not candidate.is_file():
                self.add("error", "config.path", path, f"{key} -> {value!r} does not exist")
        skills = raw.get("skills_dir", "skills")
        skills_path = (self.root / str(skills)).resolve()
        if not skills_path.is_dir():
            self.add("error", "config.skills", path, f"skills_dir -> {skills!r} does not exist")

    def check_organism_manifest(self) -> None:
        try:
            from elia.organism import OrganismManifest

            manifest = OrganismManifest.load()
            report = manifest.audit(expected_identity_id="elia-wild")
        except Exception as exc:
            self.add("error", "organism.load", "config/organism.yaml", f"{type(exc).__name__}: {exc}")
            return
        if not report.healthy:
            for item in report.findings:
                severity = item.severity if item.severity in {"error", "warning"} else "error"
                self.add(severity, "organism.audit", manifest.path, item.message)

    def check_skill_registry(self) -> None:
        try:
            from elia.config import load_config
            from elia.skills import SkillRegistry

            config = load_config(self.root / "config" / "genesis.yaml")
            registry = SkillRegistry(config.skills_dir)
            if not registry.names():
                self.add("error", "skills.empty", config.skills_dir, "no skills discovered")
        except Exception as exc:
            self.add("error", "skills.load", "skills", f"{type(exc).__name__}: {exc}")

    def check_literal_repository_paths(self) -> None:
        trim_chars = ".,;:)]}'\"`"
        for path in self._iter_text_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                continue
            for match in PATH_PATTERN.finditer(text):
                raw = match.group(1).rstrip(trim_chars)
                if not raw.startswith(PATH_PREFIXES):
                    continue
                candidate = self.root / raw
                if not candidate.exists():
                    line = text.count("\n", 0, match.start()) + 1
                    self.add(
                        "error",
                        "path.literal",
                        f"{path.relative_to(self.root)}:{line}",
                        f"referenced path does not exist: {raw}",
                    )

    def check_markdown_links(self) -> None:
        for path in self.root.rglob("*.md"):
            if any(part in {".git", ".elia", ".venv"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(text):
                target = match.group(1).strip().split()[0].strip("<>")
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = target.split("#", 1)[0]
                if not target:
                    continue
                candidate = (path.parent / target).resolve()
                if not candidate.exists():
                    line = text.count("\n", 0, match.start()) + 1
                    self.add(
                        "error",
                        "markdown.link",
                        f"{path.relative_to(self.root)}:{line}",
                        f"broken local link: {target}",
                    )

    def check_version_sync(self) -> None:
        pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        expected = str(pyproject["project"]["version"])
        try:
            from elia import __version__
        except Exception as exc:
            self.add("error", "version.import", "elia/__init__.py", str(exc))
            return
        if __version__ != expected:
            self.add("error", "version.sync", PYPROJECT, f"pyproject={expected}, elia.__version__={__version__}")

    def check_dependency_graph(self) -> None:
        modules: dict[Path, str] = {}
        for path in sorted((self.root / "elia").rglob("*.py")):
            rel = path.relative_to(self.root).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules[path] = ".".join(parts)
        known = set(modules.values())
        graph: dict[str, set[str]] = defaultdict(set)
        for path, module_name in modules.items():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            package_parts = module_name.split(".")[:-1]
            for node in ast.walk(tree):
                target: str | None = None
                if isinstance(node, ast.ImportFrom) and node.module is not None:
                    if node.level:
                        base = package_parts[: max(0, len(package_parts) - node.level + 1)]
                        target = ".".join([*base, node.module]) if node.module else ".".join(base)
                    else:
                        target = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in known:
                            graph[module_name].add(alias.name)
                    continue
                if target in known and target != module_name:
                    graph[module_name].add(target)

        index = 0
        stack: list[str] = []
        on_stack: set[str] = set()
        indices: dict[str, int] = {}
        low: dict[str, int] = {}
        cycles: list[list[str]] = []

        def visit(node: str) -> None:
            nonlocal index
            indices[node] = index
            low[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for nxt in graph.get(node, set()):
                if nxt not in indices:
                    visit(nxt)
                    low[node] = min(low[node], low[nxt])
                elif nxt in on_stack:
                    low[node] = min(low[node], indices[nxt])
            if low[node] == indices[node]:
                component: list[str] = []
                while stack:
                    item = stack.pop()
                    on_stack.remove(item)
                    component.append(item)
                    if item == node:
                        break
                if len(component) > 1:
                    cycles.append(sorted(component))

        for node in sorted(known):
            if node not in indices:
                visit(node)
        for component in cycles:
            self.add("warning", "python.import-cycle", "elia", " -> ".join(component))

    def run(self) -> dict[str, object]:
        checks = (
            self.check_python_syntax_and_internal_imports,
            self.check_all_package_modules_import,
            self.check_entrypoints,
            self.check_genesis_paths,
            self.check_organism_manifest,
            self.check_skill_registry,
            self.check_literal_repository_paths,
            self.check_markdown_links,
            self.check_version_sync,
            self.check_dependency_graph,
        )
        for check in checks:
            try:
                check()
            except Exception as exc:
                self.add("error", "audit.internal", check.__name__, f"{type(exc).__name__}: {exc}")
        errors = [item for item in self.findings if item.severity == "error"]
        warnings = [item for item in self.findings if item.severity == "warning"]
        return {
            "ok": not errors,
            "root": str(self.root),
            "errors": len(errors),
            "warnings": len(warnings),
            "findings": [item.as_dict() for item in self.findings],
        }


def main() -> None:
    report = RepositoryAudit().run()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
