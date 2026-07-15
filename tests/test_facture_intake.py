"""
Tests de l'intake factures Pennylane/SOFRIPA — logique pure (pas de PDF réel,
le PDF étant hors dépôt ; le parsing est validé à la main sur factures réelles).
"""
from __future__ import annotations

from core.reconciliation.facture_intake import (
    FactureIntake,
    LigneFactureIntake,
    valider_facture,
)
from core.reconciliation.facture_processor import _est_livree, lier_lignes
from core.reconciliation.gasoil import allouer_gasoil
from core.reconciliation.reconciliation_core import Commande


# ─── Gasoil ──────────────────────────────────────────────────────────────────

def test_gasoil_invariant_somme_exacte():
    bruts = [80.23, 24.85, 55.0, 22.72, 137.4, 61.29, 2.13, 9.99, 400.5] * 15
    maj = 1873.65
    alloc = allouer_gasoil(bruts, maj)
    somme_final = round(sum(a["final"] for a in alloc), 2)
    assert somme_final == round(sum(bruts) + maj, 2)
    assert round(sum(a["surtaxe"] for a in alloc), 2) == maj


def test_gasoil_exemple_cdc_total():
    # Facture SO014744 du CDC : brut 12761,50 + maj 1831,54 = HT 14593,04
    bruts = [47.13, 12761.50 - 47.13]
    alloc = allouer_gasoil(bruts, 1831.54)
    assert round(sum(a["final"] for a in alloc), 2) == 14593.04


def test_gasoil_liste_vide():
    assert allouer_gasoil([], 100.0) == []


# ─── Validation ──────────────────────────────────────────────────────────────

def _facture_ok() -> FactureIntake:
    lignes = [
        LigneFactureIntake(jour="16/04/26", poids=100, transport=50.0,
                           frais_admin=2.13, montant_brut=52.13, montant_final=52.13),
        LigneFactureIntake(jour="16/04/26", poids=80, transport=30.0,
                           frais_admin=2.13, montant_brut=32.13, montant_final=32.13),
    ]
    return FactureIntake(
        id_facture="SO000001", date_facture="16/04/26", maj_total=0.0,
        montant_ht=84.26, lignes=lignes,
        totaux_journaliers={"16/04/26": 84.26},
    )


def test_valider_facture_ok():
    assert valider_facture(_facture_ok()) == []


def test_valider_rejette_total_journalier_faux():
    fac = _facture_ok()
    fac.totaux_journaliers["16/04/26"] = 999.99  # incohérent
    err = valider_facture(fac)
    assert any("journalier" in e.lower() for e in err)


def test_valider_rejette_ht_incoherent():
    fac = _facture_ok()
    fac.montant_ht = 999.99
    assert any("HT" in e for e in valider_facture(fac))


def test_valider_tolere_ligne_gratuite():
    # Règle « transport > 0 par ligne » RETIRÉE : une ligne à 0 € (taxi-colis
    # gratuit, transfert interne SYMBIOSE) ne rejette PLUS la facture tant que
    # Σ montant_final = HT. Seuls les totaux (global + journaliers) comptent.
    fac = _facture_ok()
    fac.lignes[0].transport = 0.0
    assert valider_facture(fac) == []


# ─── Liaison Easy Beer ───────────────────────────────────────────────────────

def _cmd(numero, client, etat, date="16/04/2026"):
    return Commande(numero=numero, client=client, poids=100.0, ht=200.0,
                    brut={"Etat": etat, "Date de livr. réelle": date})


def test_est_livree():
    assert _est_livree(_cmd(1, "X", "Livrée"))
    assert _est_livree(_cmd(1, "X", "LIVREE"))
    assert not _est_livree(_cmd(1, "X", "Validée"))


def test_lier_lignes_statuts():
    # pièce 6800 → cmd 2800 (LIVREE) ; pièce 6801 → cmd 2801 (non livrée) ;
    # pièce 9999 → aucune commande ; pas de pièce → sans_piece
    commandes = {
        2800: _cmd(2800, "OTERA", "Livrée"),
        2801: _cmd(2801, "BIOCOOP", "Validée"),
    }
    fac = FactureIntake(lignes=[
        LigneFactureIntake(num_piece="00006800", destinataire="OTERA",
                           jour="16/04/26", poids=100),
        LigneFactureIntake(num_piece="00006801", destinataire="BIOCOOP",
                           jour="16/04/26", poids=100),
        LigneFactureIntake(num_piece="00009999", destinataire="X",
                           jour="16/04/26", poids=100),
        LigneFactureIntake(num_piece=None, destinataire="Y",
                           jour="16/04/26", poids=100),
    ])
    recap = lier_lignes(fac, commandes)
    assert recap["OK"] == 1
    assert recap["non_livree"] == 1
    assert recap["commande_absente"] == 1
    assert recap["sans_piece"] == 1
    assert fac.lignes[0].id_commande_easybeer == 2800
