"""Research organs preserved from the ELIA / Seraphim / Omega / Holo lineage.

Nothing in this package is enabled by the Genesis runtime merely by being importable.
Every component carries an explicit maturity/evidence status; production integration is
an experiment requiring an ablation and continuity regression.
"""

from .registry import RESEARCH_REGISTRY, ResearchArtifact, maturity_summary

__all__ = ["RESEARCH_REGISTRY", "ResearchArtifact", "maturity_summary"]
