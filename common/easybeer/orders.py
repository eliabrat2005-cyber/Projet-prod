"""common/easybeer/orders.py - detail d'une commande (lignes produit).

Couche TRANSPORT : appelle l'API EasyBeer et faconne les dicts, sans logique
metier (la classification/repartition vit dans core.allocation). Utilise le
detail par NUMERO de commande (POST /commande/numero) car l'export en masse
(/commande/export) ne donne QUE le poids total, pas le detail par ligne.

Chaque ligne produit expose :
  - barcode        : gtin (stockProduit) ou codeArticle a defaut
  - product_name   : nom commercial / nom produit / designation
  - category       : libelle categorie produit (pour la regle mots-cles)
  - quantity       : quantite commandee
  - unit_weight_kg : poidsUnitaire EasyBeer (peut manquer -> resolu par override)
"""
from __future__ import annotations

import logging

from common.easybeer._client import BASE, _auth, get_session

_log = logging.getLogger("ferment.easybeer.orders")


def fetch_commande_detail(numero: int | str) -> dict | None:
    """Detail complet d'une commande via son NUMERO. None si absente/erreur.

    L'API renvoie parfois un 500 generique (au lieu de 404) quand le numero
    n'existe pas ou en maintenance : on log en debug et on renvoie None pour
    laisser la sync marquer la commande en anomalie plutot que planter.
    """
    # L'API attend le numero en NOMBRE JSON (un numero en chaine -> 500 generique).
    try:
        num = int(numero)
    except (TypeError, ValueError):
        return None
    s = get_session()
    try:
        r = s.post(f"{BASE}/commande/numero", json=num, auth=_auth(), timeout=60)
    except Exception as exc:  # noqa: BLE001 - reseau : degrade proprement
        _log.debug("detail commande %s: exception reseau %s", numero, exc)
        return None
    if r.status_code != 200:
        _log.debug("detail commande %s: HTTP %s", numero, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def order_status(detail: dict) -> str | None:
    """Statut lisible de la commande (info UI) a partir des booleens EasyBeer."""
    if detail.get("estLivree"):
        return "LIVREE"
    if detail.get("estEnAttenteStock"):
        return "EN_ATTENTE_STOCK"
    if detail.get("estEnCours"):
        return "EN_COURS"
    return None


def _clean(v):
    return v.strip() if isinstance(v, str) else v


def extract_order_lines(detail: dict) -> list[dict]:
    """Lignes produit normalisees a partir d'un ModeleCommande.

    Couvre les bouteilles (produits vendus) et les elements 'autres'
    (coffrets/divers) qui portent un poids. Ignore les elements sans interet
    pour la repartition (locations, futs, contenants consignes).
    """
    lines: list[dict] = []

    for el in detail.get("elementsBouteilles") or []:
        sb = el.get("stockBouteille") or {}
        prod = sb.get("produit") or {}
        sp = el.get("stockProduit") or {}
        cat = prod.get("categorie") or {}
        lines.append({
            "barcode": _clean(sp.get("gtin")) or _clean(sb.get("codeArticle")),
            "product_name": (
                _clean(prod.get("nomCommercial"))
                or _clean(prod.get("nom"))
                or _clean(el.get("designation"))
            ),
            "category": _clean(cat.get("libelle")),
            "quantity": el.get("quantite") or 0,
            "unit_weight_kg": sb.get("poidsUnitaire"),
        })

    for el in detail.get("elementsAutres") or []:
        sa = el.get("stockAutre") or {}
        lines.append({
            "barcode": _clean(sa.get("codeArticle")),
            "product_name": _clean(el.get("libelle")),
            "category": None,
            "quantity": el.get("quantite") or 0,
            "unit_weight_kg": el.get("poidsUnitaire") or el.get("poidsNetUnitaire"),
        })

    return lines
