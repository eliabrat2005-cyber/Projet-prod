"""Classification d'une ligne : (entite, poids unitaire) par CATEGORIE + override.

Priorite (choix metier : classification par categorie produit) :
  1. Override PRODUIT ``data/entites_produits.csv`` (par code-barres/gtin) - la
     verite terrain pour une exception ponctuelle. Fixe entite ET/OU poids.
  2. Mapping CATEGORIE ``data/entites_categories.csv`` : la categorie EasyBeer
     du produit -> entite. C'est le mecanisme principal.
  3. Sinon -> ENTITY_UNKNOWN : la commande passe en ANOMALIE, on mappe la
     categorie (utile quand Spraga enverra ses 1eres commandes : nouvelle
     categorie non mappee -> anomalie visible). Aucune supposition aveugle.

Le poids unitaire vient d'abord d'EasyBeer (poidsUnitaire) ; a defaut de
l'override produit. Absent des deux -> poids None -> anomalie en aval.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
import unicodedata

from core.allocation.models import ENTITY_FS, ENTITY_SPRAGA, ENTITY_UNKNOWN

_log = logging.getLogger("ferment.allocation.classification")

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)
_DEFAULT_PRODUCTS_CSV = os.path.join(_DATA_DIR, "entites_produits.csv")
_DEFAULT_CATEGORIES_CSV = os.path.join(_DATA_DIR, "entites_categories.csv")

# Caches {cle: ...} + le chemin charge (pour invalider si on change de fichier).
_PROD_CACHE: dict[str, dict] | None = None
_PROD_PATH: str | None = None
_CAT_CACHE: dict[str, str] | None = None
_CAT_PATH: str | None = None
_LOCK = threading.Lock()


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _is_active(v) -> bool:
    return _norm(v) not in ("false", "0", "non", "no")


def _entity_or_none(v) -> str | None:
    ent = (v or "").strip().upper()
    return ent if ent in (ENTITY_FS, ENTITY_SPRAGA) else None


# ── Override produit (par gtin/code-barres) ───────────────────────────────
def _load_products(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                bc = (row.get("barcode") or "").strip()
                if not bc or bc.startswith("#") or not _is_active(row.get("active")):
                    continue
                w = row.get("unit_weight_kg")
                try:
                    w = float(str(w).replace(",", ".")) if w not in (None, "") else None
                except ValueError:
                    w = None
                out[bc] = {"entity": _entity_or_none(row.get("entity")),
                           "unit_weight_kg": w}
    except OSError as e:  # pragma: no cover
        _log.warning("Lecture override produits %s impossible: %s", path, e)
    return out


# ── Mapping categorie -> entite ───────────────────────────────────────────
def _load_categories(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                cat = (row.get("category") or "").strip()
                if not cat or cat.startswith("#") or not _is_active(row.get("active")):
                    continue
                ent = _entity_or_none(row.get("entity"))
                if ent:
                    out[_norm(cat)] = ent
    except OSError as e:  # pragma: no cover
        _log.warning("Lecture categories %s impossible: %s", path, e)
    return out


def _products(path: str | None = None) -> dict[str, dict]:
    global _PROD_CACHE, _PROD_PATH
    p = path or _DEFAULT_PRODUCTS_CSV
    with _LOCK:
        if _PROD_CACHE is None or _PROD_PATH != p:
            _PROD_CACHE, _PROD_PATH = _load_products(p), p
        return _PROD_CACHE


def _categories(path: str | None = None) -> dict[str, str]:
    global _CAT_CACHE, _CAT_PATH
    p = path or _DEFAULT_CATEGORIES_CSV
    with _LOCK:
        if _CAT_CACHE is None or _CAT_PATH != p:
            _CAT_CACHE, _CAT_PATH = _load_categories(p), p
        return _CAT_CACHE


def reload_overrides(products_path: str | None = None,
                     categories_path: str | None = None) -> tuple[int, int]:
    """Force le rechargement des 2 tables (apres edition CSV). (nb_produits, nb_cats)."""
    global _PROD_CACHE, _PROD_PATH, _CAT_CACHE, _CAT_PATH
    with _LOCK:
        _PROD_CACHE = _PROD_PATH = _CAT_CACHE = _CAT_PATH = None
    return len(_products(products_path)), len(_categories(categories_path))


def classify(
    *,
    barcode: str | None,
    product_name: str | None = None,
    category: str | None = None,
    unit_weight_kg: float | None = None,
    override_path: str | None = None,
    categories_path: str | None = None,
) -> tuple[str, float | None, str, str]:
    """Retourne (entity, unit_weight_kg, entity_source, weight_source).

    - entity_source : 'override' | 'category' | '' (unknown)
    - weight_source : 'easybeer' | 'override' | '' (inconnu)
    """
    ov = _products(override_path).get((barcode or "").strip()) if barcode else None

    # Entite : override produit prioritaire, sinon mapping categorie.
    entity, entity_source = ENTITY_UNKNOWN, ""
    if ov and ov.get("entity"):
        entity, entity_source = ov["entity"], "override"
    else:
        cat_ent = _categories(categories_path).get(_norm(category))
        if cat_ent:
            entity, entity_source = cat_ent, "category"

    # Poids : EasyBeer prioritaire, sinon override produit.
    weight, weight_source = None, ""
    if unit_weight_kg is not None and unit_weight_kg > 0:
        weight, weight_source = float(unit_weight_kg), "easybeer"
    elif ov and ov.get("unit_weight_kg"):
        weight, weight_source = float(ov["unit_weight_kg"]), "override"

    return entity, weight, entity_source, weight_source
