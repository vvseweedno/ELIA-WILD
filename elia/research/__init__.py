"""Research organs preserved from the ELIA / Seraphim / Omega / Holo lineage.

Nothing in this package is enabled by the Genesis runtime merely by being importable.
Every component carries an explicit maturity/evidence status; production integration is
an experiment requiring an ablation and continuity regression.
"""

from .bench import run_memory_backend_ablation, run_reference_cognitive_stress
from .cache import StatefulMemoryCache
from .registry import RESEARCH_REGISTRY, ResearchArtifact, maturity_summary
from .runtime import DatasetCocktailRegistry, DatasetSpec, RuntimeCompatibilityChecker, SmokeFirstRunner

__all__ = [
    "RESEARCH_REGISTRY",
    "ResearchArtifact",
    "maturity_summary",
    "StatefulMemoryCache",
    "DatasetCocktailRegistry",
    "DatasetSpec",
    "RuntimeCompatibilityChecker",
    "SmokeFirstRunner",
    "run_memory_backend_ablation",
    "run_reference_cognitive_stress",
]
