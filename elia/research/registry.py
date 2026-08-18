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
    "stateful_memory_cache": ResearchArtifact(
        "stateful_memory_cache",
        "holo-seraphim",
        "prototype",
        "Keep recurrent research-memory state scoped by stream without making it part of immutable identity.",
        (
            "Extracted from HoloSeraphim recurrent cache requirements.",
            "Genesis durable identity still lives in verified external state, not an ephemeral tensor cache.",
        ),
    ),
    "runtime_compatibility_checker": ResearchArtifact(
        "runtime_compatibility_checker",
        "infrastructure",
        "proven",
        "Classify dependency/environment readiness before expensive experiments.",
        (
            "Encodes repeated TPU/JAX/Kaggle failure lessons: API mismatch and missing auth are not model-quality evidence.",
            "Repository regression tests exercise required/optional dependency classification.",
        ),
    ),
    "dataset_cocktail_registry": ResearchArtifact(
        "dataset_cocktail_registry",
        "infrastructure",
        "prototype",
        "Declare weighted datasets with explicit gating/auth readiness checks before loading.",
        (
            "Extracted from archived gated/unsupported dataset failures.",
            "The registry does not download data or bypass access controls.",
        ),
    ),
    "smoke_first_runner": ResearchArtifact(
        "smoke_first_runner",
        "infrastructure",
        "proven",
        "Prevent expensive experimental paths unless a cheap smoke predicate succeeds.",
        (
            "Generalizes the smoke-first discipline already used by Genesis CI and Kaggle lifecycle work.",
            "Regression tests prove the full path is not called after failed smoke.",
        ),
    ),
    "memory_backend_ablation": ResearchArtifact(
        "memory_backend_ablation",
        "memory",
        "prototype",
        "Run deterministic reference comparisons across Scroll, Fractal, LRU and Holo memory backends.",
        (
            "Provides a common executable baseline harness before model-scale benchmarking.",
            "Reference metrics are diagnostics and are not language-model performance claims.",
        ),
    ),
    "epistemic_diversity_ablation": ResearchArtifact(
        "epistemic_diversity_ablation",
        "epistemic",
        "prototype",
        "Compare Pearson-12 cognitive diversity against homogeneous, random-role and domain-expert controls under equal compute budgets.",
        (
            "Genesis 1.6 includes an executable equal-call/token-budget harness.",
            "Built-in lexical diversity and falsifier/counterexample coverage are not accuracy metrics; superiority requires external ground truth/evaluation.",
        ),
    ),
    "cognitive_biography_hysteresis": ResearchArtifact(
        "cognitive_biography_hysteresis",
        "epistemic",
        "hypothesis",
        "Test whether differentiated organ histories cause persistent behavioral differences after role-policy prompts are reset or homogenized.",
        (
            "The hypothesis follows the project observation that durable memory should causally alter later reasoning rather than merely decorate prompts.",
            "No repository-local experiment yet establishes a hysteresis effect or its magnitude.",
        ),
    ),
    "latent_communication": ResearchArtifact(
        "latent_communication",
        "epistemic",
        "hypothesis",
        "Test local-model hidden-state/concept channels as an optional communication substrate between cognitive organs versus text/evidence packets.",
        (
            "This remains research-only: ordinary remote model APIs do not expose a trustworthy hidden-state channel.",
            "Any latent-channel claim must beat text/evidence-packet baselines on downstream task quality under controlled compute, not merely preserve more internal features.",
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
