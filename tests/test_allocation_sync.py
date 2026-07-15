"""Tests d'orchestration de la synchro repartition (sans DB ni API reelle)."""
from __future__ import annotations

import os

from common.easybeer.orders import extract_order_lines, order_status
from common.services import allocation_store, allocation_sync
from core.allocation.models import STATUS_ANOMALIE, STATUS_PROVISOIRE


# ── extract_order_lines (parsing pur du ModeleCommande) ───────────────────
def _fake_detail():
    return {
        "estLivree": True,
        "client": {"nom": "LA VIE CLAIRE"},
        "elementsBouteilles": [
            {
                "quantite": 100, "designation": "Kefir Original 33cl",
                "stockBouteille": {"poidsUnitaire": 0.6, "codeArticle": "KEF33",
                                   "produit": {"nom": "Kefir Original",
                                               "nomCommercial": "Kefir Original 33cl",
                                               "categorie": {"libelle": "Kefir"}}},
                "stockProduit": {"gtin": "3760000000012"},
            },
            {
                "quantite": 100, "designation": "Kombucha Gingembre 33cl",
                "stockBouteille": {"poidsUnitaire": 0.4, "codeArticle": "KOM33",
                                   "produit": {"nom": "Kombucha Gingembre",
                                               "categorie": {"libelle": "Kombucha"}}},
                "stockProduit": {"gtin": "3770000000019"},
            },
        ],
        "elementsAutres": [],
    }


def test_extract_order_lines_shapes_bottles():
    lines = extract_order_lines(_fake_detail())
    assert len(lines) == 2
    kef = lines[0]
    assert kef["barcode"] == "3760000000012"
    assert kef["product_name"] == "Kefir Original 33cl"
    assert kef["category"] == "Kefir"
    assert kef["quantity"] == 100
    assert kef["unit_weight_kg"] == 0.6


def test_order_status_livree():
    assert order_status({"estLivree": True}) == "LIVREE"
    assert order_status({"estEnAttenteStock": True}) == "EN_ATTENTE_STOCK"
    assert order_status({}) is None


# ── synchroniser (tout mocke) ─────────────────────────────────────────────
def _patch_common(monkeypatch, *, validated=None, captured=None):
    monkeypatch.setattr(
        allocation_sync, "_collect_orders",
        lambda t, a, b: {2731: {"cost": 100.0, "client": "LA VIE CLAIRE",
                                "piece": "0007191", "period_month": "2026-05"}},
    )
    monkeypatch.setattr(
        allocation_store, "list_allocations",
        lambda t, *a, **kw: [
            {"order_number": n, "allocation_status": "VALIDEE"}
            for n in (validated or [])
        ],
    )
    if captured is not None:
        monkeypatch.setattr(
            allocation_store, "upsert_provisional",
            lambda t, alloc: captured.append(alloc),
        )


def test_synchroniser_writes_split(monkeypatch, tmp_path):
    captured = []
    _patch_common(monkeypatch, captured=captured)
    # Override produit : mappe les 2 gtins du detail -> FS / Spraga (poids via EB).
    op = os.path.join(tmp_path, "ov.csv")
    with open(op, "w", encoding="utf-8") as fh:
        fh.write("barcode,product_name,entity,unit_weight_kg,active\n")
        fh.write("3760000000012,Kefir,FERMENT_STATION,,true\n")
        fh.write("3770000000019,Kombucha,SPRAGA,,true\n")
    stats = allocation_sync.synchroniser(
        "tenant-1", "2026-05", "2026-05",
        fetch_detail=lambda num: _fake_detail(), override_path=op,
    )
    assert stats["vues"] == 1 and stats["ecrites"] == 1 and stats["anomalies"] == 0
    assert len(captured) == 1
    a = captured[0]
    assert a.order_number == 2731
    assert a.allocation_status == STATUS_PROVISOIRE
    assert a.order_status == "LIVREE"
    # 100x0.6 = 60 FS ; 100x0.4 = 40 Spraga ; cout 100 -> 60/40.
    assert a.weight_fs_kg == 60.0 and a.weight_spraga_kg == 40.0
    assert a.cost_fs == 60.0 and a.cost_spraga == 40.0
    assert round(a.cost_fs + a.cost_spraga, 2) == 100.0


def test_synchroniser_skips_validated(monkeypatch):
    captured = []
    calls = []
    _patch_common(monkeypatch, validated=[2731], captured=captured)

    def _should_not_fetch(num):
        calls.append(num)
        return _fake_detail()

    stats = allocation_sync.synchroniser(
        "tenant-1", "2026-05", "2026-05", fetch_detail=_should_not_fetch,
    )
    assert stats["ignorees"] == 1 and stats["ecrites"] == 0
    assert calls == []            # une commande figee n'est meme pas refetchee
    assert captured == []


def test_synchroniser_anomaly_when_detail_missing(monkeypatch):
    captured = []
    _patch_common(monkeypatch, captured=captured)
    stats = allocation_sync.synchroniser(
        "tenant-1", "2026-05", "2026-05", fetch_detail=lambda num: None,
    )
    assert stats["anomalies"] == 1
    assert captured[0].allocation_status == STATUS_ANOMALIE
    assert "indisponible" in captured[0].anomaly_reason
