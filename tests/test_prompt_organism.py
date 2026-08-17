from __future__ import annotations

from pathlib import Path

from elia.prompting import PromptTemplate


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_rendered_cognitive_contract_contains_machine_readable_organism() -> None:
    template = PromptTemplate.load(repo_root() / "config" / "system_prompt.md")
    rendered = template.render(
        {
            "identity_contract": {"identity_id": "elia-wild"},
            "self_model": {"identity_id": "elia-wild"},
            "skills": {},
            "self_hypotheses": [],
            "metacognition": {},
        }
    )
    assert '"organism"' in rendered
    assert '"identity_id": "elia-wild"' in rendered
    assert '"core_organs"' in rendered
    assert "not identity authority" in rendered
    assert "Decision JSON schema" in rendered
