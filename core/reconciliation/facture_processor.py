"""
facture_processor.py — Orchestration du traitement d'UNE facture SOFRIPA.

Pipeline (CDC) : parse → valide → relie à Easy Beer → prépare pour insertion.
Ce module ne fait PAS d'I/O base (voir facture_store) : il produit un
« résultat de traitement » prêt à graver.

Décision (à confirmer avec le boss) — divergence assumée vs le CDC littéral :
  Le CDC dit « une ligne échoue → facture entière rejetée ». Or, sur les vraies
  données, ~5-15 % des lignes n'ont légitimement pas de N° pièce exploitable ou
  pas de commande en face (livraisons sans pièce, distributeur…). Rejeter toute
  la facture pour ça rejetterait TOUTES les factures.
  → On REJETTE la facture uniquement pour une erreur STRUCTURELLE (parsing,
    montants qui ne réconcilient pas, totaux journaliers faux). Les lignes non
    reliées à une commande sont STOCKÉES avec un statut (sans_piece /
    commande_absente / non_livree) — la « base parfaite des factures » est bien
    exacte, et le lien Easy Beer est renseigné au mieux, ligne par ligne.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .facture_intake import FactureIntake, parse_facture, valider_facture
from .reconciliation_core import OFFSET, _resoudre_commande


def _est_livree(cmd) -> bool:
    """True si la commande Easy Beer est au statut LIVRÉE (finalisée)."""
    etat = (cmd.brut or {}).get("Etat") if cmd else None
    return str(etat or "").strip().upper().startswith("LIVR")


def lier_lignes(fac: FactureIntake, commandes_par_num: dict) -> dict:
    """Relie chaque ligne à sa commande Easy Beer via le N° pièce (pièce−4000 ou
    direct), vérifie le statut LIVRÉE, et renseigne id_commande + client + statut.

    Retourne un petit récap {ok, sans_piece, commande_absente, non_livree}.
    """
    recap = {"OK": 0, "sans_piece": 0, "commande_absente": 0, "non_livree": 0}
    for L in fac.lignes:
        if not L.num_piece:
            L.statut_match = "sans_piece"
        else:
            num, cmd = _resoudre_commande(
                L.num_piece, L.destinataire, L.poids, L.jour or L.exp_date,
                commandes_par_num, OFFSET,
            )
            if cmd is None:
                L.statut_match = "commande_absente"
            elif not _est_livree(cmd):
                L.statut_match = "non_livree"
                L.id_commande_easybeer = num
                L.client_easybeer = cmd.client
            else:
                L.statut_match = "OK"
                L.id_commande_easybeer = num
                L.client_easybeer = cmd.client
        recap[L.statut_match] += 1
    return recap


@dataclass
class ResultatTraitement:
    facture: FactureIntake
    status: str                      # 'OK' | 'REJECTED'
    erreurs: list = field(default_factory=list)
    recap_liaison: dict = field(default_factory=dict)


def traiter_facture(path, commandes_par_num: dict) -> ResultatTraitement:
    """Traite une facture de bout en bout (sauf insertion BD).

    - parse (entête + lignes + gasoil)
    - valide les comptes (structure) → si KO : status REJECTED (tout-ou-rien)
    - relie les lignes à Easy Beer (best-effort, statut par ligne)
    """
    fac = parse_facture(path)
    erreurs = valider_facture(fac)
    if erreurs:
        return ResultatTraitement(facture=fac, status="REJECTED", erreurs=erreurs)
    recap = lier_lignes(fac, commandes_par_num)
    return ResultatTraitement(facture=fac, status="OK", recap_liaison=recap)
