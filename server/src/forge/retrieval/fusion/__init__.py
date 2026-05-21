"""融合层: 多路召回融合 + 父块聚合."""

from .base import (
    AggregatedParent,
    Aggregator,
    FusedHit,
    Fusion,
)
from .factory import (
    AggregatorFactory,
    FusionFactory,
    register_aggregator,
    register_fusion,
)

__all__ = [
    "Fusion",
    "FusedHit",
    "FusionFactory",
    "register_fusion",
    "Aggregator",
    "AggregatedParent",
    "AggregatorFactory",
    "register_aggregator",
]
