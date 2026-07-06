"""
Tests de la 2e passe de réconciliation (rapprochement déduit nom + ville + date)
ajoutée dans reconciliation_core : elle récupère les lignes SANS pièce quand la
marque + la ville + la date pointent de façon sûre vers une commande Easy Beer.

Voir core/reconciliation/reconciliation_core.py : _apparier_deduit / reconcilier.
"""
from core.reconciliation.reconciliation_core import (
    Commande,
    LigneFacture,
    _apparier_deduit,
    _norm_client,
    reconcilier,
)


def test_exp_date_completee_avec_annee():
    from core.reconciliation.io_api import _exp_date_annee
    # cas normal : facture de mars → expédition de mars 2026
    assert _exp_date_annee("16/03", "20/03/26") == "16/03/2026"
    # passage d'année : expédition en décembre facturée en janvier → année -1
    assert _exp_date_annee("28/12", "05/01/26") == "28/12/2025"
    # expédition début février, facture fin janvier (même campagne) → même année,
    # PAS l'année précédente (bug corrigé : pièce 254 BIOCOOP Les Fêtes)
    assert _exp_date_annee("01/02", "31/01/25") == "01/02/2025"
    # déjà une année ou entrée vide : inchangé
    assert _exp_date_annee("16/03/2026", "20/03/26") == "16/03/2026"
    assert _exp_date_annee(None, "20/03/26") is None


def _cmd(numero, client, date):
    """Commande EB minimale avec une date de livraison prévue (jj/mm/aaaa)."""
    return Commande(numero=numero, client=client, poids=None,
                    brut={"Date de livr. prévue": date})


# --------------------------------------------------------------------------- #
#  Normalisation (les 2 bugs corrigés)
# --------------------------------------------------------------------------- #
def test_norm_garde_contenu_alpha_parentheses():
    # « (SCAPNOR) » doit rester un token (raison sociale), pas être supprimé.
    assert "scapnor" in _norm_client("SOC COOP APPRO PARIS NORD (SCAPNOR)")


def test_norm_colle_apostrophe():
    # O'TERA -> OTERA pour matcher la casse Easy Beer.
    assert "otera" in _norm_client("O'TERA WASQUEHAL (59) WASQUEHAL")


def test_norm_retire_code_departement_pas_la_ville():
    toks = _norm_client("CARREFOUR (62) AIRE-SUR-LA-LYS")
    assert "carrefour" in toks and "aire" in toks and "lys" in toks
    assert "62" not in toks


def test_norm_ignore_mots_generiques():
    assert "magasin" not in _norm_client("LA VIE CLAIRE Arpajon (Magasin 130)")


# --------------------------------------------------------------------------- #
#  2e passe : cas nominaux
# --------------------------------------------------------------------------- #
def test_monosite_marque_seule_suffit():
    # BIODIS n'existe qu'en 1 exemplaire côté EB -> la marque seule identifie.
    facs = [LigneFacture(exp_date="19/05/26", client="BIODIS (35) NOYAL CHATILLON")]
    cmds = {3015: _cmd(3015, "BIODIS", "20/05/2026")}
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1 and not restants
    _, num, _, conf = apparies[0]
    assert num == 3015 and conf == "haute"


def test_marque_ville_discriminante():
    # La ville (AIRE / LYS, mono-site) désigne LE bon magasin, malgré 4 CARREFOUR
    # (la marque CARREFOUR, partagée, n'est PAS discriminante).
    facs = [LigneFacture(exp_date="19/03/26", client="CARREFOUR (62) AIRE-SUR-LA-LYS")]
    cmds = {
        2594: _cmd(2594, "CARREFOUR Aire sur la Lys", "19/03/2026"),
        2600: _cmd(2600, "CARREFOUR Hem", "19/03/2026"),
        2601: _cmd(2601, "CARREFOUR Lens", "19/03/2026"),
        2602: _cmd(2602, "CARREFOUR Douai", "19/03/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1
    _, num, _, conf = apparies[0]
    assert num == 2594 and conf == "haute"


def test_marque_seule_multisite_rejetee():
    # INTERMARCHE Puteaux : aucune ville commune + INTERMARCHE partagé par ≥3
    # magasins (non discriminant) -> non rapproché (honnête).
    facs = [LigneFacture(exp_date="15/01/26", client="INTERMARCHE (92) PUTEAUX")]
    cmds = {
        2306: _cmd(2306, "INTERMARCHE Asnières Voltaire", "15/01/2026"),
        2320: _cmd(2320, "INTERMARCHE Etampes", "15/01/2026"),
        2321: _cmd(2321, "INTERMARCHE Bagneux", "15/01/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1


def test_enseigne_deux_mots_sans_ville_rejetee():
    # « LA VIE CLAIRE Montevrain » n'existe pas côté EB : la marque VIE+CLAIRE
    # (2 mots) ne doit PAS suffire à s'apparier à d'autres LA VIE CLAIRE.
    facs = [LigneFacture(exp_date="22/01/26", client="LA VIE CLAIRE (77) MONTEVRAIN")]
    cmds = {
        182: _cmd(182, "LA VIE CLAIRE Fremicourt", "22/01/2026"),
        183: _cmd(183, "LA VIE CLAIRE Colonel Fabien", "22/01/2026"),
        196: _cmd(196, "LA VIE CLAIRE Les Ulis", "22/01/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1


def test_enseigne_deux_mots_avec_ville_matchee():
    # Le magasin existe (Arpajon) -> on l'apparie précisément, sans se laisser
    # noyer par les autres LA VIE CLAIRE du même jour.
    facs = [LigneFacture(exp_date="22/01/26", client="LA VIE CLAIRE (91) ARPAJON")]
    cmds = {
        182: _cmd(182, "LA VIE CLAIRE Fremicourt", "22/01/2026"),
        130: _cmd(130, "LA VIE CLAIRE Arpajon (Magasin 130)", "22/01/2026"),
        196: _cmd(196, "LA VIE CLAIRE Les Ulis", "22/01/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1 and apparies[0][1] == 130


def test_ancre_marque_rejette_mot_de_lieu():
    # « NATURALIA GARONOR (93) AULNAY-SOUS-BOIS » ne doit PAS s'apparier à
    # « LA VIE CLAIRE Bois-Colombes » via le seul mot de lieu « bois ».
    facs = [LigneFacture(exp_date="30/01/26",
                         client="NATURALIA GARONOR (93) AULNAY-SOUS-BOIS")]
    cmds = {100: _cmd(100, "LA VIE CLAIRE Bois Colombes", "30/01/2026")}
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1


def test_anti_coincidence_un_seul_mot():
    # « RELAIS VERT » ne doit PAS s'apparier à « Un Ecrin Vert » (mot « vert »
    # commun) ; il doit trouver le vrai RELAIS VERT.
    facs = [LigneFacture(exp_date="10/03/26", client="RELAIS VERT (84) CARPENTRAS")]
    cmds = {
        100: _cmd(100, "RELAIS VERT", "10/03/2026"),
        200: _cmd(200, "BIOCOOP Un Ecrin Vert", "10/03/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1 and apparies[0][1] == 100


def test_point_cardinal_ignore():
    # « sud » (direction) ne doit pas devenir un token de rapprochement.
    assert "sud" not in _norm_client("SYSTEME U SUD (34) BEZIERS")


def test_meme_magasin_departage_par_poids():
    # 2 commandes NATURALIA GARONOR le même jour -> le poids tranche.
    facs = [LigneFacture(exp_date="30/01/26", client="NATURALIA GARONOR (93) AULNAY",
                         poids=2776.0)]
    cmds = {
        190: _cmd(190, "NATURALIA GARONOR", "30/01/2026"),
        225: _cmd(225, "NATURALIA GARONOR", "30/01/2026"),
    }
    cmds[190].poids = 500.0     # loin
    cmds[225].poids = 2700.0    # proche des 2776 SOFRIPA
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1
    _, num, _, conf = apparies[0]
    assert num == 225 and conf == "date"


def test_hors_fenetre_date_rejete():
    facs = [LigneFacture(exp_date="19/05/26", client="BIODIS (35) NOYAL CHATILLON")]
    cmds = {3015: _cmd(3015, "BIODIS", "01/01/2026")}   # >6 jours
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1


def test_ambigu_departage_par_date():
    # 2 commandes même client, la plus proche en date gagne -> confiance « date ».
    facs = [LigneFacture(exp_date="10/03/26", client="RELAIS VERT (84) CARPENTRAS")]
    cmds = {
        2471: _cmd(2471, "RELAIS VERT", "12/03/2026"),   # écart 2 j
        2261: _cmd(2261, "RELAIS VERT", "08/03/2026"),   # écart 2 j -> égalité
    }
    # égalité stricte -> non tranché
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1

    cmds[2471] = _cmd(2471, "RELAIS VERT", "11/03/2026")  # écart 1 j -> gagne
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1
    _, num, _, conf = apparies[0]
    assert num == 2471 and conf == "date"


def test_unicite_une_commande_par_ligne():
    # 2 livraisons BIODIS proches -> 2 commandes DISTINCTES (pas de collision).
    facs = [
        LigneFacture(exp_date="18/03/26", client="BIODIS (35) NOYAL"),
        LigneFacture(exp_date="21/03/26", client="BIODIS (35) NOYAL"),
    ]
    cmds = {
        483: _cmd(483, "BIODIS", "19/03/2026"),
        490: _cmd(490, "BIODIS", "22/03/2026"),
    }
    apparies, restants = _apparier_deduit(facs, cmds)
    nums = [a[1] for a in apparies]
    assert len(apparies) == 2 and len(set(nums)) == 2


def test_une_commande_pour_deux_lignes_en_prend_une_seule():
    # une seule commande BIODIS pour 2 livraisons -> une seule ligne l'obtient.
    facs = [
        LigneFacture(exp_date="19/03/26", client="BIODIS (35) NOYAL"),
        LigneFacture(exp_date="20/03/26", client="BIODIS (35) NOYAL"),
    ]
    cmds = {483: _cmd(483, "BIODIS", "19/03/2026")}
    apparies, restants = _apparier_deduit(facs, cmds)
    assert len(apparies) == 1 and len(restants) == 1


def test_deduit_ne_reutilise_pas_commande_piece():
    # La commande prise par le rapprochement par pièce n'est pas réutilisée.
    facs = [
        LigneFacture(exp_date="19/03/26", client="BIODIS", piece="7483",
                     poids=10.0, montant=5.0),
        LigneFacture(exp_date="20/03/26", client="BIODIS (35) NOYAL",
                     poids=10.0, montant=5.0),
    ]
    cmds = {7483: _cmd(7483, "BIODIS", "19/03/2026")}
    res = reconcilier(facs, cmds)
    assert len(res.lignes) == 1 and res.lignes[0].methode == "piece"
    assert len(res.sans_piece) == 1


def test_client_inconnu_reste_sans_piste():
    facs = [LigneFacture(exp_date="14/01/26", client="MUSEUM HISTOIRE (75) PARIS")]
    cmds = {2261: _cmd(2261, "RELAIS VERT", "14/01/2026")}
    apparies, restants = _apparier_deduit(facs, cmds)
    assert not apparies and len(restants) == 1


# --------------------------------------------------------------------------- #
#  Intégration reconcilier() : la 2e passe promeut, sans polluer les matchs pièce
# --------------------------------------------------------------------------- #
def test_reconcilier_promeut_ligne_deduite():
    facs = [LigneFacture(exp_date="19/05/26", client="BIODIS (35) NOYAL",
                         poids=100.0, montant=187.0)]
    cmds = {3015: _cmd(3015, "BIODIS", "20/05/2026")}
    res = reconcilier(facs, cmds)
    assert len(res.lignes) == 1 and not res.sans_piece
    L = res.lignes[0]
    assert L.methode == "deduit" and L.confiance == "haute"
    assert res.kpis["livraisons_deduites"] == 1
    assert res.kpis["livraisons_par_piece"] == 0


def test_reconcilier_deduction_desactivable():
    facs = [LigneFacture(exp_date="19/05/26", client="BIODIS (35) NOYAL")]
    cmds = {3015: _cmd(3015, "BIODIS", "20/05/2026")}
    res = reconcilier(facs, cmds, deduction=False)
    assert not res.lignes and len(res.sans_piece) == 1


def test_ligne_piece_reste_methode_piece():
    # Une ligne avec pièce résolue garde methode="piece" (non touchée par la 2e passe).
    facs = [LigneFacture(exp_date="20/05/26", client="BIODIS", piece="7015",
                         poids=100.0, montant=187.0)]
    cmds = {7015: _cmd(7015, "BIODIS", "20/05/2026")}
    res = reconcilier(facs, cmds)
    assert len(res.lignes) == 1 and res.lignes[0].methode == "piece"
    assert res.kpis["livraisons_par_piece"] == 1
