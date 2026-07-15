"""Tests des modèles défensifs core/commandes/models.py."""
from __future__ import annotations

from core.commandes.models import (
    CommandeExtraite,
    LigneCommande,
    _as_float,
    _extract_taille,
    _iso_date_or_none,
)

# ─── _as_float ────────────────────────────────────────────────────────────

def test_as_float_variants():
    assert _as_float(12) == 12.0
    assert _as_float("1 200,5") == 1200.5
    assert _as_float("1 200") == 1200.0  # espace insécable (milliers)
    assert _as_float("48") == 48.0
    assert _as_float("") is None
    assert _as_float(None) is None
    assert _as_float("abc") is None
    assert _as_float(True) is None  # un booléen n'est pas une quantité


# ─── _iso_date_or_none ──────────────────────────────────────────────────────

def test_iso_date_normalisation():
    assert _iso_date_or_none("15/06/2026") == "2026-06-15"
    assert _iso_date_or_none("15-06-26") == "2026-06-15"
    assert _iso_date_or_none("2026-06-15") == "2026-06-15"
    assert _iso_date_or_none("2026-06-15T00:00:00") == "2026-06-15"
    assert _iso_date_or_none("") is None
    assert _iso_date_or_none("pas une date") is None
    assert _iso_date_or_none("32/13/2026") is None  # jour/mois invalides


# ─── LigneCommande.from_dict ─────────────────────────────────────────────────

def test_ligne_from_dict_defensif():
    # La taille (33cl) est extraite du libellé et retirée de la gamme.
    li = LigneCommande.from_dict({
        "gamme": "Kéfir Original 33cl", "quantite": "48", "unite": "cartons", "colis": 4,
    })
    assert li.gamme == "Kéfir Original"
    assert li.taille == "33 cl"
    assert li.quantite == 48.0
    assert li.unite == "cartons"
    assert li.colis == 4.0


# ─── _extract_taille / séparation gamme ──────────────────────────────────────

def test_extract_taille():
    assert _extract_taille("Kéfir de fruit, mangue passion, 33cl Bio") == (
        "Kéfir de fruit, mangue passion, Bio", "33 cl",
    )
    assert _extract_taille("Infusion mélisse 75 cl") == ("Infusion mélisse", "75 cl")
    assert _extract_taille("Produit sans contenance") == ("Produit sans contenance", "")


def test_ligne_taille_explicite_preservee():
    # Si la taille est déjà fournie séparément, on ne touche pas à la gamme.
    li = LigneCommande.from_dict({"gamme": "Kéfir Original", "taille": "33 cl", "quantite": 12})
    assert li.gamme == "Kéfir Original"
    assert li.taille == "33 cl"


def test_ligne_roundtrip_taille():
    li = LigneCommande.from_dict({"gamme": "Kéfir pêche 33cl", "quantite": 10})
    again = LigneCommande.from_dict(li.to_dict())
    assert again.gamme == "Kéfir pêche"
    assert again.taille == "33 cl"
    assert again.quantite == 10.0


def test_ligne_from_dict_alias_et_manquants():
    # alias produit/libelle + qty, champs manquants tolérés
    li = LigneCommande.from_dict({"produit": "Citron", "qty": 10})
    assert li.gamme == "Citron"
    assert li.quantite == 10.0
    assert li.unite == ""
    # entrée non-dict → libellé brut, pas de crash
    li2 = LigneCommande.from_dict("Gingembre")
    assert li2.gamme == "Gingembre"
    assert li2.quantite is None


# ─── CommandeExtraite.from_dict ──────────────────────────────────────────────

def test_commande_from_dict_complet():
    cmd = CommandeExtraite.from_dict({
        "magasin": "Franprix Bio",
        "date_reception": "15/06/2026",
        "date_livraison": "18/06/2026",
        "confiance": "haute",
        "lignes": [
            {"gamme": "Kéfir Original", "quantite": 48, "unite": "cartons"},
            {"gamme": "Kéfir Citron", "quantite": 24},
            {"gamme": "", "quantite": 5},  # ligne sans libellé → filtrée
        ],
    })
    assert cmd.magasin == "Franprix Bio"
    assert cmd.date_reception == "2026-06-15"
    assert cmd.date_livraison == "2026-06-18"
    assert cmd.confiance == "haute"
    assert len(cmd.lignes) == 2  # la ligne vide est filtrée
    assert cmd.total_quantite == 72.0


def test_commande_from_dict_degrade():
    # entrée vide / mal typée → objet vide, jamais d'exception
    assert CommandeExtraite.from_dict(None).magasin == ""
    assert CommandeExtraite.from_dict({}).lignes == []
    # confiance invalide → vidée
    assert CommandeExtraite.from_dict({"confiance": "tres haute"}).confiance == ""
    # lignes non-liste → ignorées
    assert CommandeExtraite.from_dict({"lignes": "oops"}).lignes == []


def test_commande_est_commande():
    # Par défaut (champ absent) : on considère que c'en est une.
    assert CommandeExtraite.from_dict({"magasin": "X", "lignes": []}).est_commande is True
    # Explicitement faux -> écarté.
    assert CommandeExtraite.from_dict(
        {"est_commande": False, "magasin": "", "lignes": []}
    ).est_commande is False
    # Tolérant aux chaînes.
    assert CommandeExtraite.from_dict(
        {"est_commande": "false", "lignes": []}
    ).est_commande is False
    assert CommandeExtraite.from_dict(
        {"est_commande": "true", "lignes": []}
    ).est_commande is True


def test_commande_roundtrip_to_dict():
    cmd = CommandeExtraite.from_dict({
        "magasin": "Coop",
        "lignes": [{"gamme": "Kéfir", "quantite": 12, "unite": "colis"}],
        "confiance": "moyenne",
    })
    d = cmd.to_dict()
    cmd2 = CommandeExtraite.from_dict(d)
    assert cmd2.magasin == "Coop"
    assert cmd2.lignes[0].gamme == "Kéfir"
    assert cmd2.lignes[0].quantite == 12.0
    assert cmd2.confiance == "moyenne"
