from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResearchArtifact:
    name: str
    family: str
    maturity: str
    purpose: str
    evidence: tuple[str, ...]
    default_runtime: bool = False

    def as_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["evidence"] = list(self.evidence)
        return item


RESEARCH_REGISTRY: dict[str, ResearchArtifact] = {
    "ouroboros_x0": ResearchArtifact(
        "ouroboros_x0",
        "seraphim",
        "prototype",
        "Inject a persistent/anchored hidden-state component through transformer depth using an explicit decay schedule.",
        (
            "Canonical ELIA/Seraphim plugin plan retains Ouroboros/x0 hidden-state injection as a priority module.",
            "No repository-local benchmark yet proves a default production gain across models.",
        ),
    ),
    "topological_loss": ResearchArtifact(
        "topological_loss",
        "seraphim",
        "prototype",
        "Regularize representation geometry during fine-tuning to preserve structural relations rather than only token loss.",
        (
            "Retained as a priority fine-tuning component in the canonical Seraphim plugin specification.",
            "Requires task/model-specific ablation before production use.",
        ),
    ),
    "fractal_memory": ResearchArtifact(
        "fractal_memory",
        "seraphim",
        "prototype",
        "Surprisal-gated multi-scale durable memory derived from ScrollMemory experiments.",
        (
            "Canonical memory plan evolves ScrollMemory toward FractalMemory with surprisal-gated writes.",
            "Runtime integration remains experimental.",
        ),
    ),
    "lru_scan": ResearchArtifact(
        "lru_scan",
        "holo",
        "prototype",
        "Log-domain recurrent associative-scan baseline with learned/input-dependent forget behavior.",
        (
            "Archived 10k enwiki8 comparison recorded LRU validation BPB about 1.6257 versus Holo about 2.0474 and materially faster execution.",
            "Historical notebook result is evidence for keeping LRU as a strong baseline, not a universal benchmark claim.",
        ),
    ),
    "holo_scan": ResearchArtifact(
        "holo_scan",
        "holo",
        "prototype",
        "Associative recurrent scan inspired by HoloMemoryKernel with explicit forget gates.",
        (
            "Reusable Holo component retained as a memory backend research branch.",
            "Full complex-state Holo architecture is not Genesis core.",
        ),
    ),
    "holo_complex_model": ResearchArtifact(
        "holo_complex_model",
        "holo",
        "archived",
        "Full byte-level complex-state Holo model with complex normalization/phase machinery.",
        (
            "Historical Holo val BPB about 2.0474 at 10k enwiki8 steps under one archived run.",
            "TPU scaling notebooks include environment, API, dataset and OOM/runtime failures and are failure-analysis evidence rather than production proof.",
            "One TinyStories TPU notebook appears to have completed 10k steps around loss 2.1759 and ~23k tok/s, but is not a validated baseline benchmark.",
        ),
    ),
    "context_anchor": ResearchArtifact(
        "context_anchor",
        "omega",
        "prototype",
        "Inject an explicit context/identity anchor into recurrent or depth-wise processing.",
        (
            "Elia Omega v7.1 historical ablations identified ContextAnchor among the strongest individual positive signals.",
            "Needs reproduction in the current codebase/model stack.",
        ),
    ),
    "bounded_depth_film": ResearchArtifact(
        "bounded_depth_film",
        "omega",
        "prototype",
        "Feature-wise linear modulation conditioned on bounded recurrent/depth state.",
        (
            "Elia Omega v7.1 historical ablations identified bounded-depth FiLM among the strongest individual signals.",
            "No claim of universal improvement is made.",
        ),
    ),
    "omega_filter": ResearchArtifact(
        "omega_filter",
        "omega",
        "prototype",
        "A weakly positive adaptive filtering component from the Omega line.",
        (
            "Historical Omega evidence characterized OmegaFilter as a weak positive signal.",
        ),
    ),
    "tricore": ResearchArtifact(
        "tricore",
        "omega",
        "prototype",
        "Shared-weight recurrent multi-cycle transformer/core computation.",
        (
            "Historical Omega line uses recurrent/shared-weight cycles; auxiliary cycle supervision remained unproven.",
            "Elastic depth remains a hypothesis rather than a validated default.",
        ),
    ),
    "complex_rmsnorm": ResearchArtifact(
        "complex_rmsnorm",
        "holo",
        "hypothesis",
        "Phase-preserving complex-valued normalization for later memory/model research.",
        (
            "Retained only as a later research adapter and polar-vs-Cartesian ablation target.",
        ),
    ),
    "hybrid_optimizer": ResearchArtifact(
        "hybrid_optimizer",
        "seraphim",
        "hypothesis",
        "Optional optimizer wrapper for future combined objectives/parameter groups.",
        (
            "Canonical plugin plan marks HybridOptimizer optional rather than v1 core.",
        ),
    ),
}


def maturity_summary() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "proven": [],
        "prototype": [],
        "archived": [],
        "hypothesis": [],
    }
    for name, artifact in RESEARCH_REGISTRY.items():
        result.setdefault(artifact.maturity, []).append(name)
    for names in result.values():
        names.sort()
    return result
