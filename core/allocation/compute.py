"""Calcul de la repartition : split au poids + arrondi centime + anomalies."""
from __future__ import annotations

from core.allocation.models import (
    ENTITY_FS,
    ENTITY_SPRAGA,
    ENTITY_UNKNOWN,
    STATUS_ANOMALIE,
    STATUS_PROVISOIRE,
    Allocation,
    AllocationLine,
)


def split_weights(lines: list[AllocationLine]) -> tuple[float, float, float]:
    """Somme les poids de ligne par entite -> (poids_fs, poids_spraga, total).

    Les lignes UNKNOWN ou sans poids ne sont PAS sommees ici : elles sont
    detectees en amont (build_allocation) comme anomalie. On les ignore pour
    ne pas fausser un total qui de toute facon ne sera pas utilise.
    """
    w_fs = sum(
        (ln.line_weight_kg or 0.0)
        for ln in lines
        if ln.entity == ENTITY_FS and ln.line_weight_kg
    )
    w_spraga = sum(
        (ln.line_weight_kg or 0.0)
        for ln in lines
        if ln.entity == ENTITY_SPRAGA and ln.line_weight_kg
    )
    return round(w_fs, 3), round(w_spraga, 3), round(w_fs + w_spraga, 3)


def split_cost(
    cost_total: float,
    weight_fs: float,
    weight_total: float,
    *,
    absorb: str = ENTITY_SPRAGA,
) -> tuple[float, float]:
    """Ventile ``cost_total`` au prorata du poids -> (cost_fs, cost_spraga).

    Garantie centime : cost_fs + cost_spraga == round(cost_total, 2) EXACTEMENT.
    L'ecart d'arrondi est absorbe par une seule entite (``absorb``, Spraga par
    defaut - decision D6 du PRD).
    """
    total = round(float(cost_total), 2)
    if weight_total <= 0:
        raise ValueError("weight_total doit etre > 0 pour repartir un cout")

    pct_fs = weight_fs / weight_total
    if absorb == ENTITY_FS:
        # On arrondit Spraga, FS absorbe le reste.
        cost_spraga = round(total * (1 - pct_fs), 2)
        cost_fs = round(total - cost_spraga, 2)
    else:
        # Defaut : on arrondit FS, Spraga absorbe le reste.
        cost_fs = round(total * pct_fs, 2)
        cost_spraga = round(total - cost_fs, 2)
    return cost_fs, cost_spraga


def build_allocation(
    *,
    order_number: int,
    order_client: str | None,
    order_status: str | None,
    invoice_number: str | None,
    period_month: str | None,
    transport_cost_total: float | None,
    lines: list[AllocationLine],
    absorb: str = ENTITY_SPRAGA,
) -> Allocation:
    """Construit une Allocation PROVISOIRE (ou ANOMALIE) a partir des lignes.

    Regles d'anomalie (PRD sect. 11), dans l'ordre :
      1. aucune ligne / reference non classee (UNKNOWN) / poids de ligne manquant
      2. poids total nul
      3. cout de transport absent (rien a repartir)
    Une anomalie n'empeche pas d'afficher les poids deja calcules.
    """
    alloc = Allocation(
        order_number=order_number,
        order_client=order_client,
        order_status=order_status,
        invoice_number=invoice_number,
        period_month=period_month,
        transport_cost_total=(
            round(float(transport_cost_total), 2)
            if transport_cost_total is not None
            else None
        ),
        lines=lines,
    )

    w_fs, w_spraga, w_total = split_weights(lines)
    alloc.weight_fs_kg = w_fs
    alloc.weight_spraga_kg = w_spraga
    alloc.weight_total_kg = w_total

    # 1) references non classees ou poids de ligne manquant
    reasons: list[str] = []
    if not lines:
        reasons.append("aucune ligne")
    unknown = [ln for ln in lines if ln.entity == ENTITY_UNKNOWN]
    if unknown:
        noms = ", ".join(sorted({(ln.product_name or ln.barcode or "?") for ln in unknown}))
        reasons.append(f"reference(s) non classee(s): {noms}")
    sans_poids = [
        ln for ln in lines
        if ln.entity in (ENTITY_FS, ENTITY_SPRAGA) and not ln.line_weight_kg
    ]
    if sans_poids:
        noms = ", ".join(sorted({(ln.product_name or ln.barcode or "?") for ln in sans_poids}))
        reasons.append(f"poids manquant: {noms}")

    # 2) poids total nul
    if not reasons and w_total <= 0:
        reasons.append("poids total nul")

    # 3) cout absent
    if not reasons and transport_cost_total is None:
        reasons.append("cout transport absent (non rapproche)")

    if reasons:
        alloc.allocation_status = STATUS_ANOMALIE
        alloc.anomaly_reason = " ; ".join(reasons)
        return alloc

    # Cas nominal : split au poids.
    alloc.pct_fs = round(w_fs / w_total, 6)
    alloc.pct_spraga = round(1 - alloc.pct_fs, 6)
    cost_fs, cost_spraga = split_cost(
        transport_cost_total, w_fs, w_total, absorb=absorb
    )
    alloc.cost_fs = cost_fs
    alloc.cost_spraga = cost_spraga
    alloc.allocation_status = STATUS_PROVISOIRE
    return alloc
