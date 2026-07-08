"""Test du détecteur d'anomalies « À vérifier » de la page réconciliation."""
from __future__ import annotations

from types import SimpleNamespace

from pages.reconciliation_transport import _lignes_a_verifier


def _L(numero, ecart_kg=0.0, statut="OK"):
    return SimpleNamespace(
        numero=numero, ecart_kg=ecart_kg, statut=statut,
        client="X", poids_eb=10.0, poids_sofripa=10.0, cout_transport=50.0,
    )


def test_ligne_normale_pas_signalee():
    assert _lignes_a_verifier([_L(1), _L(2)]) == []


def test_doublon_commande_signale():
    res = _lignes_a_verifier([_L(7205), _L(7205), _L(9)])
    numeros = {L.numero for L, _ in res}
    assert numeros == {7205}
    assert all("2×" in raison for _, raison in res)


def test_ecart_poids_aberrant_signale():
    res = _lignes_a_verifier([_L(1, ecart_kg=1000.0)], seuil_kg=500.0)
    assert len(res) == 1
    assert "écart de poids" in res[0][1]


def test_ecart_sous_seuil_non_signale():
    assert _lignes_a_verifier([_L(1, ecart_kg=120.0)], seuil_kg=500.0) == []


def test_petit_ecart_negatif_non_signale():
    # Un petit écart négatif routinier n'est PAS une grosse anomalie ici.
    assert _lignes_a_verifier([_L(1, ecart_kg=-3.0, statut="À vérifier (négatif)")]) == []


def test_gros_ecart_negatif_signale():
    # Mais un écart négatif ÉNORME (ex. -1304 kg) l'est (par la magnitude).
    res = _lignes_a_verifier([_L(1, ecart_kg=-1304.0)], seuil_kg=500.0)
    assert len(res) == 1 and "écart de poids" in res[0][1]


def test_cumul_raisons():
    # doublon + écart aberrant sur la même commande -> 2 raisons
    res = _lignes_a_verifier([_L(5, ecart_kg=800.0), _L(5, ecart_kg=800.0)])
    assert len(res) == 2
    for _, raison in res:
        assert "2×" in raison and "écart de poids" in raison
