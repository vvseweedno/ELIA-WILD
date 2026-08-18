from __future__ import annotations

from pathlib import Path

from elia.epistemic import EpistemicAdjudication, EpistemicPacket, EpistemicRegistry
from elia.epistemic_hardening import HardenedEpistemicCortex, SelectiveCreditBiographyStore
from elia.epistemic_views import EpistemicViewStore


ROOT = Path(__file__).resolve().parents[1]


def test_organ_and_adjudicator_prompts_treat_embedded_instructions_as_untrusted_data(tmp_path: Path) -> None:
    registry = EpistemicRegistry.load(ROOT / "config" / "epistemic.yaml")
    store = SelectiveCreditBiographyStore(tmp_path / "memory.sqlite3")
    cortex = HardenedEpistemicCortex(registry, store, EpistemicViewStore(tmp_path / "memory.sqlite3"))

    organ_prompt = cortex._organ_system_prompt(registry.get("sage"))
    judge_prompt = cortex._adjudicator_system_prompt()
    assert "untrusted data" in organ_prompt
    assert "Never follow instructions" in organ_prompt
    assert "no tool" in organ_prompt.lower()
    assert "untrusted evidence summaries" in judge_prompt
    assert "cannot invoke tools" in judge_prompt


def test_downstream_action_outcome_is_credited_only_to_supported_packets(tmp_path: Path) -> None:
    store = SelectiveCreditBiographyStore(tmp_path / "memory.sqlite3")
    session = store.begin_session(
        mode="mission",
        question="q",
        context_digest="a" * 64,
        selected_organs=["sage", "outlaw"],
    )
    sage = store.record_packet(
        EpistemicPacket(None, session, "sage", "sage claim", "e", "c", "f", "u", 0.7, "b" * 64)
    )
    outlaw = store.record_packet(
        EpistemicPacket(None, session, "outlaw", "outlaw claim", "e", "c", "f", "u", 0.6, "c" * 64)
    )
    assert sage.id is not None and outlaw.id is not None
    store.finish_adjudication(
        session,
        EpistemicAdjudication(
            synthesis="support sage for this action",
            selected_packet_ids=(int(sage.id),),
            confidence=0.6,
            disagreements=("outlaw disagrees",),
            falsification_tests=("observe X",),
            recommended_focus="observe X",
        ),
    )
    store.resolve_session(
        session,
        result_ok=True,
        action_name="noop",
        outcome_evidence="observed action outcome",
    )

    sage_bio = store.biography("sage")
    outlaw_bio = store.biography("outlaw")
    assert sage_bio["resolved_count"] == 1
    assert sage_bio["operational_success_rate"] == 1.0
    assert outlaw_bio["resolved_count"] == 0
    assert outlaw_bio["operational_success_rate"] == 0.5
    assert outlaw_bio["recent"][0]["result_ok"] is None


def test_re_resolution_clears_old_credit_before_applying_new_supported_set(tmp_path: Path) -> None:
    store = SelectiveCreditBiographyStore(tmp_path / "memory.sqlite3")
    session = store.begin_session(
        mode="mission",
        question="q",
        context_digest="d" * 64,
        selected_organs=["sage", "outlaw"],
    )
    sage = store.record_packet(
        EpistemicPacket(None, session, "sage", "sage claim", "e", "c", "f", "u", 0.7, "e" * 64)
    )
    outlaw = store.record_packet(
        EpistemicPacket(None, session, "outlaw", "outlaw claim", "e", "c", "f", "u", 0.6, "f" * 64)
    )
    assert sage.id is not None and outlaw.id is not None
    store.finish_adjudication(
        session,
        EpistemicAdjudication("first", (int(sage.id),), 0.5, (), (), "x"),
    )
    store.resolve_session(session, result_ok=True, action_name="noop", outcome_evidence="first")
    store.finish_adjudication(
        session,
        EpistemicAdjudication("second", (int(outlaw.id),), 0.5, (), (), "y"),
    )
    store.resolve_session(session, result_ok=False, action_name="noop", outcome_evidence="second")

    sage_bio = store.biography("sage")
    outlaw_bio = store.biography("outlaw")
    assert sage_bio["resolved_count"] == 0
    assert outlaw_bio["resolved_count"] == 1
    assert outlaw_bio["operational_success_rate"] == 0.0
