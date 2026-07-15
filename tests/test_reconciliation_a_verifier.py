"""Test du détecteur d'anomalies « À vérifier » de la page réconciliation.

Le poids SOFRIPA est BRUT (emballage inclus), l'EB est NET : on ne signale que
les écarts ANORMAUX en % (SOFRIPA très sous l'EB, ou poids EB anormalement
petit), pas le sur-poids d'emballage normal.
"""
from __future__ import annotations

from types import SimpleNamespace

from pages.reconciliation_transport import _lignes_a_verifier


def _L(numero, ecart_pct=0.05):
    return SimpleNamespace(
        numero=numero, ecart_pct=ecart_pct, ecart_kg=None,
        client="X", poids_eb=100.0, poids_sofripa=105.0, cout_transport=50.0,
        statut="OK",
    )


def test_surpoids_emballage_normal_pas_signale():
    # +5 % à +40 % = emballage/palette normal -> RAS.
    assert _lignes_a_verifier([_L(1, 0.05), _L(2, 0.40)]) == []


def test_doublon_commande_signale():
    res = _lignes_a_verifier([_L(7205), _L(7205), _L(9)])
    assert {L.numero for L, _ in res} == {7205}
    assert all("2×" in r for _, r in res)


def test_poids_eb_trop_petit_signale():
    # EB=100, SOFRIPA=800 -> +700 % : EB sous-compte (cas plateforme).
    res = _lignes_a_verifier([_L(1, ecart_pct=7.0)])
    assert len(res) == 1 and "anormalement petit" in res[0][1]


def test_sofripa_sous_eb_signale():
    # SOFRIPA 93 % sous l'EB (ex. livraison partielle) -> signalé.
    res = _lignes_a_verifier([_L(1, ecart_pct=-0.93)])
    assert len(res) == 1 and "sous le poids EB" in res[0][1]


def test_petit_negatif_non_signale():
    # -10 % = bruit de mesure, pas une anomalie.
    assert _lignes_a_verifier([_L(1, ecart_pct=-0.10)]) == []


def test_ecart_pct_none_non_signale():
    # Ligne sans poids comparable (ecart_pct None) -> pas d'anomalie de poids.
    assert _lignes_a_verifier([_L(1, ecart_pct=None)]) == []
