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

    lignes = []
    for b in montants_bruts:
        surtaxe = round(maj_total * (b / somme_brut), 2)
        lignes.append({"brut": round(b, 2), "surtaxe": surtaxe})

    # Correction d'arrondi : le total des surtaxes doit valoir maj_total pile.
    # On reporte l'écart (quelques centimes) sur la ligne au plus gros montant.
    residu = round(maj_total - sum(x["surtaxe"] for x in lignes), 2)
    if residu != 0.0 and lignes:
        i_max = max(range(n), key=lambda i: lignes[i]["brut"])
        lignes[i_max]["surtaxe"] = round(lignes[i_max]["surtaxe"] + residu, 2)

    for x in lignes:
        x["final"] = round(x["brut"] + x["surtaxe"], 2)
    return lignes
