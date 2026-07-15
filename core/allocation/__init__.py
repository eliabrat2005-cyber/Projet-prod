"""core.allocation - repartition des couts de transport FS <-> Spraga.

Logique metier PURE (aucune UI, aucun appel API). Trois briques :

- models       : entites, ligne classee, repartition (dataclasses).
- classification : barcode/produit -> (entite, poids) via regle + table override.
- compute      : split au poids + arrondi au centime + cycle de vie / anomalies.

L'orchestration (lecture reconciliation + detail EasyBeer + persistance) vit
dans common/services/allocation_sync.py ; la couche core reste testable seule.
"""
from __future__ import annotations

from core.allocation.compute import build_allocation, split_cost, split_weights
from core.allocation.models import (
    ENTITY_FS,
    ENTITY_SPRAGA,
    ENTITY_UNKNOWN,
    STATUS_ANOMALIE,
    STATUS_PROVISOIRE,
    STATUS_VALIDEE,
    Allocation,
    AllocationLine,
)

__all__ = [
    "ENTITY_FS",
    "ENTITY_SPRAGA",
    "ENTITY_UNKNOWN",
    "STATUS_ANOMALIE",
    "STATUS_PROVISOIRE",
    "STATUS_VALIDEE",
    "Allocation",
    "AllocationLine",
    "build_allocation",
    "split_cost",
    "split_weights",
]
