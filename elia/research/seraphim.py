from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .decay import DecaySchedule


def ouroboros_inject(
    hidden: Any,
    anchor_x0: Any,
    *,
    depth: int,
    schedule: DecaySchedule | None = None,
    strength: float = 1.0,
    learned_rho: float | None = None,
) -> Any:
    """Inject an anchored x0 component into a hidden state.

    Works with scalar/NumPy/Torch objects supporting multiplication and addition. The
    operation is intentionally stateless so it can wrap existing HuggingFace models
    rather than define a new model from scratch.
    """

    schedule = schedule or DecaySchedule("silver")
    strength_value = float(strength)
    if not math.isfinite(strength_value):
        raise ValueError("Ouroboros injection strength must be finite")
    attenuation = schedule.attenuation(depth, learned_rho=learned_rho)
    return hidden + anchor_x0 * (strength_value * attenuation)


def _require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - optional research dependency
        raise RuntimeError("Seraphim tensor research requires the optional 'research' dependencies") from exc
    return torch


def pairwise_distance_matrix(embeddings: Any) -> Any:
    torch = _require_torch()
    if embeddings.ndim < 2:
        raise ValueError("embeddings must have at least 2 dimensions")
    flat = embeddings.reshape(-1, embeddings.shape[-1]).float()
    if flat.shape[-1] < 1 or not bool(torch.isfinite(flat).all()):
        raise ValueError("embeddings must have a non-empty finite feature dimension")
    return torch.cdist(flat, flat, p=2)


def topological_loss(
    embeddings: Any,
    reference_embeddings: Any,
    *,
    local_weight: float = 1.0,
    global_weight: float = 0.25,
) -> Any:
    """Reference topology-preservation loss for fine-tuning.

    The loss compares normalized pairwise-distance geometry against a detached
    reference representation. A stronger local term preserves nearest-neighbor
    relations while a weaker global term discourages wholesale geometric collapse.
    This is the repository's explicit prototype objective, not a claim that the
    historical TopologicalLoss notebooks used this exact implementation.
    """

    torch = _require_torch()
    current = pairwise_distance_matrix(embeddings)
    reference = pairwise_distance_matrix(reference_embeddings).detach()
    if current.shape != reference.shape:
        raise ValueError("current and reference embeddings must contain the same item count")
    for name, raw in (("local_weight", local_weight), ("global_weight", global_weight)):
        value = float(raw)
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name} must be finite and non-negative")
    eps = torch.finfo(current.dtype).eps
    current_n = current / (current.mean().detach() + eps)
    reference_n = reference / (reference.mean().detach() + eps)

    size = current_n.shape[0]
    if size <= 1:
        return current_n.sum() * 0.0
    diagonal_mask = ~torch.eye(size, dtype=torch.bool, device=current_n.device)
    global_term = torch.mean((current_n[diagonal_mask] - reference_n[diagonal_mask]) ** 2)

    nearest = torch.argsort(reference_n, dim=-1)[:, 1 : min(5, size)]
    rows = torch.arange(size, device=current_n.device).unsqueeze(1).expand_as(nearest)
    local_term = torch.mean((current_n[rows, nearest] - reference_n[rows, nearest]) ** 2)
    return float(local_weight) * local_term + float(global_weight) * global_term


def surprisal_from_token_loss(token_loss: Any) -> Any:
    """Map non-negative token NLL to ``1 - p(token)`` as a bounded write gate.

    The result is a monotone bounded unexpectedness proxy, not Shannon surprisal
    itself (the input NLL already is surprisal in nats when natural logs are used).
    """

    try:
        torch = _require_torch()
    except RuntimeError:
        torch = None
    if torch is not None and isinstance(token_loss, torch.Tensor):
        loss = token_loss.float()
        if not bool(torch.isfinite(loss).all()) or bool((loss < 0).any()):
            raise ValueError("token NLL must be finite and non-negative")
        return -torch.expm1(-loss)

    value = float(token_loss)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("token NLL must be finite and non-negative")
    return -math.expm1(-value)


@dataclass(slots=True)
class ObjectiveWeights:
    language_model: float = 1.0
    topology: float = 0.0
    continuity: float = 0.0

    def validate(self) -> None:
        for name, value in vars(self).items():
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"objective weight {name} must be finite and non-negative")


def hybrid_objective(
    *,
    lm_loss: Any,
    topology_loss_value: Any | None = None,
    continuity_loss: Any | None = None,
    weights: ObjectiveWeights | None = None,
) -> Any:
    """Explicit objective combiner retained instead of an opaque optimizer mutation."""

    weights = weights or ObjectiveWeights()
    weights.validate()
    result = lm_loss * float(weights.language_model)
    if topology_loss_value is not None and weights.topology:
        result = result + topology_loss_value * float(weights.topology)
    if continuity_loss is not None and weights.continuity:
        result = result + continuity_loss * float(weights.continuity)
    return result


def build_learned_decay_module(initial_rho: float = 0.5):
    """Return a tiny Torch module exposing a learnable bounded rho parameter."""

    torch = _require_torch()
    initial = float(initial_rho)
    if not math.isfinite(initial) or not 0.0 < initial < 1.0:
        raise ValueError("initial_rho must be finite and strictly within (0, 1)")
    initial_logit = torch.logit(torch.tensor(initial, dtype=torch.float32))

    def initialize(module: Any) -> None:
        torch.nn.Module.__init__(module)
        module.logit_rho = torch.nn.Parameter(initial_logit.clone())

    def forward(module: Any) -> Any:
        return torch.sigmoid(module.logit_rho)

    learned_decay_type = type(
        "LearnedDecay",
        (torch.nn.Module,),
        {
            "__init__": initialize,
            "forward": forward,
            "__module__": __name__,
        },
    )
    return learned_decay_type()
