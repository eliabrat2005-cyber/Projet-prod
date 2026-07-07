"""Tests unitaires du coeur de la repartition transport (core.allocation)."""
from __future__ import annotations

import os
import tempfile

import pytest

from core.allocation import classification as cls
from core.allocation.compute import build_allocation, split_cost, split_weights
from core.allocation.models import (
    ENTITY_FS,
    ENTITY_SPRAGA,
    ENTITY_UNKNOWN,
    STATUS_ANOMALIE,
    STATUS_PROVISOIRE,
    AllocationLine,
)


def _line(entity, qty, uw, **kw):
    lw = (qty * uw) if uw is not None else None
    return AllocationLine(
        barcode=kw.get("barcode"),
        product_name=kw.get("product_name"),
        entity=entity,
        quantity=qty,
        unit_weight_kg=uw,
        line_weight_kg=lw,
    )


# ── split_weights ─────────────────────────────────────────────────────────
def test_split_weights_sums_by_entity():
    lines = [_line(ENTITY_FS, 10, 6.0), _line(ENTITY_SPRAGA, 10, 4.0)]
    assert split_weights(lines) == (60.0, 40.0, 100.0)


def test_split_weights_ignores_unknown_and_weightless():
    lines = [
        _line(ENTITY_FS, 10, 6.0),
        _line(ENTITY_UNKNOWN, 5, 2.0),   # ignore
        _line(ENTITY_SPRAGA, 1, None),   # ignore (pas de poids)
    ]
    assert split_weights(lines) == (60.0, 0.0, 60.0)


# ── split_cost : garantie centime ─────────────────────────────────────────
def test_split_cost_basic_60_40():
    assert split_cost(100.0, 60.0, 100.0) == (60.0, 40.0)


def test_split_cost_rounding_absorbed_on_spraga():
    # 100 / 3 -> pct_fs = 1/3 ; cost_fs arrondi, Spraga absorbe le reste.
    cost_fs, cost_spraga = split_cost(100.0, 1.0, 3.0)
    assert cost_fs == 33.33
    assert cost_spraga == 66.67
    assert round(cost_fs + cost_spraga, 2) == 100.0


def test_split_cost_absorb_on_fs():
    cost_fs, cost_spraga = split_cost(100.0, 1.0, 3.0, absorb=ENTITY_FS)
    assert cost_spraga == 66.67
    assert cost_fs == 33.33
    assert round(cost_fs + cost_spraga, 2) == 100.0


@pytest.mark.parametrize("total,wfs,wtot", [
    (14593.04, 123.4, 456.7),
    (0.01, 1.0, 3.0),
    (999.99, 250.0, 1000.0),
    (7.77, 1.0, 7.0),
])
def test_split_cost_always_sums_to_total(total, wfs, wtot):
    cost_fs, cost_spraga = split_cost(total, wfs, wtot)
    assert round(cost_fs + cost_spraga, 2) == round(total, 2)


def test_split_cost_zero_weight_raises():
    with pytest.raises(ValueError):
        split_cost(100.0, 0.0, 0.0)


# ── build_allocation : cas nominal ────────────────────────────────────────
def test_build_allocation_nominal():
    lines = [_line(ENTITY_FS, 10, 6.0), _line(ENTITY_SPRAGA, 10, 4.0)]
    a = build_allocation(
        order_number=2731, order_client="LA VIE CLAIRE", order_status="LIVREE",
        invoice_number="0007191", period_month="2026-05",
        transport_cost_total=100.0, lines=lines,
    )
    assert a.allocation_status == STATUS_PROVISOIRE
    assert a.weight_fs_kg == 60.0 and a.weight_spraga_kg == 40.0
    assert a.pct_fs == 0.6 and a.pct_spraga == 0.4
    assert a.cost_fs == 60.0 and a.cost_spraga == 40.0
    assert a.anomaly_reason is None


def test_build_allocation_mono_entity_100_0():
    lines = [_line(ENTITY_FS, 10, 6.0)]
    a = build_allocation(
        order_number=1, order_client=None, order_status=None,
        invoice_number=None, period_month=None,
        transport_cost_total=50.0, lines=lines,
    )
    assert a.allocation_status == STATUS_PROVISOIRE
    assert a.pct_fs == 1.0 and a.pct_spraga == 0.0
    assert a.cost_fs == 50.0 and a.cost_spraga == 0.0


# ── build_allocation : anomalies ──────────────────────────────────────────
def test_anomaly_unknown_reference():
    lines = [_line(ENTITY_FS, 10, 6.0), _line(ENTITY_UNKNOWN, 5, 2.0, product_name="Mystere")]
    a = build_allocation(
        order_number=1, order_client=None, order_status=None,
        invoice_number=None, period_month=None,
        transport_cost_total=100.0, lines=lines,
    )
    assert a.allocation_status == STATUS_ANOMALIE
    assert "non classee" in a.anomaly_reason
    assert "Mystere" in a.anomaly_reason
    assert a.cost_fs is None


def test_anomaly_zero_total_weight():
    lines = [_line(ENTITY_FS, 0, 6.0)]  # qty 0 -> line_weight 0
    a = build_allocation(
        order_number=1, order_client=None, order_status=None,
        invoice_number=None, period_month=None,
        transport_cost_total=100.0, lines=lines,
    )
    assert a.allocation_status == STATUS_ANOMALIE
    # line_weight_kg == 0 -> detecte comme poids manquant
    assert a.anomaly_reason


def test_anomaly_missing_cost():
    lines = [_line(ENTITY_FS, 10, 6.0), _line(ENTITY_SPRAGA, 10, 4.0)]
    a = build_allocation(
        order_number=1, order_client=None, order_status=None,
        invoice_number=None, period_month=None,
        transport_cost_total=None, lines=lines,
    )
    assert a.allocation_status == STATUS_ANOMALIE
    assert "cout transport absent" in a.anomaly_reason
    # les poids restent calcules meme en anomalie
    assert a.weight_total_kg == 100.0


def test_anomaly_no_lines():
    a = build_allocation(
        order_number=1, order_client=None, order_status=None,
        invoice_number=None, period_month=None,
        transport_cost_total=100.0, lines=[],
    )
    assert a.allocation_status == STATUS_ANOMALIE
    assert "aucune ligne" in a.anomaly_reason


# ── classification : mapping categorie + override produit ─────────────────
def _cat_csv(d, rows):
    p = os.path.join(d, "cat.csv")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("category,entity,active\n")
        for cat, ent in rows:
            fh.write(f"{cat},{ent},true\n")
    return p


def test_classify_by_category_fs():
    with tempfile.TemporaryDirectory() as d:
        cp = _cat_csv(d, [("Infusion Probiotique", "FERMENT_STATION")])
        ent, w, esrc, wsrc = cls.classify(
            barcode=None, product_name="Infusion Menthe",
            category="Infusion Probiotique", unit_weight_kg=6.7, categories_path=cp,
        )
        assert ent == ENTITY_FS and esrc == "category"
        assert w == 6.7 and wsrc == "easybeer"


def test_classify_by_category_spraga():
    with tempfile.TemporaryDirectory() as d:
        cp = _cat_csv(d, [("Kombucha", "SPRAGA")])
        ent, _, esrc, _ = cls.classify(
            barcode=None, product_name="Kombucha X", category="Kombucha",
            categories_path=cp,
        )
        assert ent == ENTITY_SPRAGA and esrc == "category"


def test_classify_unknown_when_category_not_mapped():
    with tempfile.TemporaryDirectory() as d:
        cp = _cat_csv(d, [("Infusion Probiotique", "FERMENT_STATION")])
        ent, w, esrc, wsrc = cls.classify(
            barcode=None, product_name="Jus de pomme", category="Jus",
            categories_path=cp,
        )
        assert ent == ENTITY_UNKNOWN and esrc == ""
        assert w is None and wsrc == ""


def test_classify_product_override_wins_over_category():
    with tempfile.TemporaryDirectory() as d:
        cp = _cat_csv(d, [("Infusion Probiotique", "FERMENT_STATION")])
        op = os.path.join(d, "ov.csv")
        with open(op, "w", encoding="utf-8") as fh:
            fh.write("barcode,product_name,entity,unit_weight_kg,active\n")
            fh.write("999,Cas special,SPRAGA,0.7,true\n")
        ent, w, esrc, wsrc = cls.classify(
            barcode="999", product_name="X", category="Infusion Probiotique",
            override_path=op, categories_path=cp,
        )
        assert ent == ENTITY_SPRAGA and esrc == "override"  # override > categorie
        assert w == 0.7 and wsrc == "override"


def test_classify_shipped_categories_are_fs():
    # Le CSV livre mappe les 7 categories reelles a Ferment Station.
    ent, _, esrc, _ = cls.classify(
        barcode=None, category="Kéfir de fruits - SYMBIOSE",
    )
    assert ent == ENTITY_FS and esrc == "category"
    cls.reload_overrides()  # reset caches vers les CSV par defaut
