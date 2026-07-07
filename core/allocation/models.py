"""Modeles de la repartition transport (dataclasses pures)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Entites co-expediees ──────────────────────────────────────────────────
ENTITY_FS = "FERMENT_STATION"       # Symbiose (kefir) + Niko
ENTITY_SPRAGA = "SPRAGA"            # Kombucha
ENTITY_UNKNOWN = "UNKNOWN"          # non classe -> declenche une ANOMALIE

# ── Etats d'allocation (cf. PRD sect. 6) ──────────────────────────────────
STATUS_PROVISOIRE = "PROVISOIRE"    # calcul auto, recalcule a chaque sync
STATUS_VALIDEE = "VALIDEE"          # fige par l'operateur, jamais ecrase
STATUS_ANOMALIE = "ANOMALIE"        # poids nul / non classe / cout absent


@dataclass
class AllocationLine:
    """Une ligne de commande classee et pesee."""
    barcode: str | None
    product_name: str | None
    entity: str                      # ENTITY_FS | ENTITY_SPRAGA | ENTITY_UNKNOWN
    quantity: float
    unit_weight_kg: float | None     # poids unitaire resolu (EasyBeer ou override)
    line_weight_kg: float | None     # quantity x unit_weight_kg
    weight_source: str = ""          # 'easybeer' | 'override' | '' (inconnu)
    entity_source: str = ""          # 'rule' | 'override' | '' (inconnu)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AllocationLine:
        """Parsing defensif : tolere les cles manquantes/None."""
        return cls(
            barcode=d.get("barcode"),
            product_name=d.get("product_name"),
            entity=d.get("entity") or ENTITY_UNKNOWN,
            quantity=float(d.get("quantity") or 0),
            unit_weight_kg=_as_float_or_none(d.get("unit_weight_kg")),
            line_weight_kg=_as_float_or_none(d.get("line_weight_kg")),
            weight_source=d.get("weight_source") or "",
            entity_source=d.get("entity_source") or "",
        )


@dataclass
class Allocation:
    """Repartition du cout de transport d'une commande entre FS et Spraga."""
    order_number: int
    order_client: str | None = None
    order_status: str | None = None          # LIVREE | EN_COURS | ... (info)
    invoice_number: str | None = None
    period_month: str | None = None          # 'YYYY-MM'
    weight_fs_kg: float = 0.0
    weight_spraga_kg: float = 0.0
    weight_total_kg: float = 0.0
    pct_fs: float | None = None
    pct_spraga: float | None = None
    transport_cost_total: float | None = None
    cost_fs: float | None = None
    cost_spraga: float | None = None
    allocation_status: str = STATUS_PROVISOIRE
    anomaly_reason: str | None = None
    lines: list[AllocationLine] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lines"] = [ln.to_dict() for ln in self.lines]
        return d


def _as_float_or_none(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
