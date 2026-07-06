"""
gasoil.py — Répartition de la majoration gasoil sur les lignes d'une facture.

SOFRIPA facture un supplément carburant GLOBAL par facture (Maj Go + Maj GNR).
Le CDC (Nicolas) demande de le répartir sur chaque ligne AU PRORATA de son
montant brut (transport + frais admin), indépendamment des dates :

    somme_brut       = Σ montant_brut[i]
    surtaxe[i]       = maj_total × (montant_brut[i] / somme_brut)
    montant_final[i] = montant_brut[i] + surtaxe[i]

Règle « zéro erreur » du CDC : après répartition, Σ montant_final doit égaler
EXACTEMENT (somme_brut + maj_total) au centime. On arrondit chaque surtaxe à
2 décimales puis on absorbe le résidu d'arrondi sur la plus grosse ligne.

Fonction PURE (pas d'I/O, pas d'UI) — testable en isolation.
"""
from __future__ import annotations


def allouer_gasoil(montants_bruts: list[float], maj_total: float) -> list[dict]:
    """Répartit ``maj_total`` sur les lignes au prorata de leur montant brut.

    Retourne une liste alignée sur ``montants_bruts`` :
        [{"brut": .., "surtaxe": .., "final": ..}, ...]
    avec Σ final == round(Σ brut + maj_total, 2) au centime près.
    """
    somme_brut = round(sum(montants_bruts), 2)
    n = len(montants_bruts)
    if n == 0:
        return []
    if somme_brut <= 0:
        # Pas de base de répartition : aucune surtaxe (cas dégénéré).
        return [{"brut": b, "surtaxe": 0.0, "final": round(b, 2)} for b in montants_bruts]

    # Répartition au CENTIME près (méthode du plus fort reste) :
    #  1. part exacte de chaque ligne, en centimes ;
    #  2. on garde la partie entière (arrondi vers le bas) ;
    #  3. on distribue les centimes restants aux lignes dont le reste est le plus
    #     grand, un par un.
    # → Σ surtaxes = maj_total PILE, ET aucune ligne ne dévie de plus d'1 centime
    #   de sa part exacte.
    maj_cents = round(maj_total * 100)
    exacts = [maj_cents * (b / somme_brut) for b in montants_bruts]
    base = [int(e) for e in exacts]                      # arrondi vers le bas (centimes)
    restants = maj_cents - sum(base)                     # centimes encore à distribuer
    # indices triés par reste décroissant (les plus « lésés » servis d'abord)
    ordre = sorted(range(n), key=lambda i: exacts[i] - base[i], reverse=True)
    for k in range(max(0, restants)):
        base[ordre[k % n]] += 1

    lignes = []
    for b, cents in zip(montants_bruts, base):
        surtaxe = round(cents / 100, 2)
        lignes.append({
            "brut": round(b, 2),
            "surtaxe": surtaxe,
            "final": round(round(b, 2) + surtaxe, 2),
        })
    return lignes
