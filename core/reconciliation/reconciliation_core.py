"""
reconciliation_core.py — LE CŒUR de la réconciliation (la "recette").

Ce module ne lit AUCUN fichier et n'écrit AUCUN Excel.
On lui donne :
  - des lignes de facture transporteur (LigneFacture)
  - des commandes Easy Beer (Commande), indexées par numéro
…et il rend un résultat structuré (appariements, écarts, statuts, KPIs).

But : cette logique se réutilise telle quelle, qu'on l'alimente par des
fichiers (aujourd'hui) ou par des API Easy Beer / Pennylane (demain), et
qu'on l'affiche dans un Excel ou dans l'app Ferment Station.
"""
import datetime
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field

_log = logging.getLogger("ferment.reconciliation_core")

# ---- Paramètres métier -------------------------------------------------------
# Deux régimes de numérotation coexistent entre le N° pièce SOFRIPA et le N°
# commande Easy Beer (voir _resoudre_commande) :
#   - « +4000 » (historique) : N° pièce = N° commande + 4000   (commandes ≤ 3190 → pièces 4100-7190)
#   - « direct »             : N° pièce = N° commande           (commandes ≥ 7191)
OFFSET = 4000          # offset du régime historique
SEUIL_PCT_DEFAUT = 0.25  # au-delà, l'écart de poids est "notable" (trop gros pour l'emballage)
FENETRE_DEDUIT = 6       # 2e passe : écart de date max (jours) marque+ville ↔ commande EB
SEUIL_DISCRIMINANT = 2   # 2e passe : un token est « discriminant » s'il ne désigne
                         # qu'≤ N clients EB (ville/magasin précis, marque mono-site) —
                         # au-delà c'est un simple nom d'enseigne (LA VIE CLAIRE…) partagé
                         # par des dizaines de magasins, insuffisant à lui seul.

# Mots génériques ignorés dans le rapprochement par nom (raison sociale, mention magasin…).
_STOP_ENSEIGNE = {
    "des", "les", "sur", "lez", "sous", "aux", "chez", "sas", "sarl", "eurl",
    "snc", "magasin", "code", "rayon", "sainte", "saint", "min", "aeroport",
    "aerop", "societe", "coop", "plateforme", "ptf",
    # points cardinaux / génériques : jamais une identité de magasin, provoquent
    # de faux appariements (« Système U SUD » ↔ « Passionfroid Paris SUD »).
    "nord", "sud", "est", "ouest", "centre",
}

# ---- Statuts possibles d'une ligne ------------------------------------------
STATUT_OK = "OK"
STATUT_NEGATIF = "À vérifier (négatif)"      # écart négatif = physiquement impossible
STATUT_NOTABLE = "Écart notable"             # écart > seuil
STATUT_PALETTE = "Palette (pas de poids)"    # ligne facturée sans poids (forfait palette)
STATUT_PAS_POIDS_EB = "Pas de poids EB"      # Easy Beer n'a pas de poids


# ============================ Structures de données ===========================
@dataclass
class LigneFacture:
    """Une ligne de la facture transporteur (ex : SOFRIPA)."""
    exp_date: str | None = None   # date d'expédition (jj/mm/aa)
    ot: str | None = None         # N° OT (ordre de transport)
    client: str | None = None     # destinataire imprimé sur la facture
    piece: str | None = None      # N° pièce (0000xxxx) — sert à l'appariement
    poids: float | None = None    # poids BRUT facturé (kg) : produit + emballage + palette
    montant: float | None = None  # coût transport de la ligne (€) — RÉEL (gasoil inclus)
    surtaxe_gasoil: float = 0.0   # part de majoration gasoil déjà comprise dans montant
    unite: str | None = None      # KGS | PAL | COL | FO — seul KGS est un vrai poids (kg)
    quantite: float | None = None # quantité facturée dans l'unité (nb palettes/colis/kg)
    facture: str | None = None    # n° de la facture SOFRIPA d'où vient la ligne


@dataclass
class Commande:
    """Une commande Easy Beer (les champs utiles à la réconciliation)."""
    numero: int
    client: str | None = None
    poids: float | None = None    # poids NET produit (kg)
    ht: float | None = None       # total HT de la commande (€)
    tournee: str | None = None
    brut: dict = field(default_factory=dict)  # toutes les colonnes Easy Beer (pour l'export)


@dataclass
class LigneReconciliee:
    """Le résultat d'un appariement facture ↔ commande, avec tous les calculs."""
    numero: int
    client: str | None
    ot: str | None
    piece: str | None
    poids_eb: float | None
    poids_sofripa: float | None
    ecart_kg: float | None
    ecart_pct: float | None
    cout_transport: float | None      # coût transport RÉEL (gasoil inclus)
    montant_ht: float | None
    transport_sur_ht: float | None
    eur_par_kg: float | None
    statut: str
    surtaxe_gasoil: float = 0.0        # part gasoil comprise dans cout_transport
    unite: str | None = None          # unité de facturation SOFRIPA (KGS|PAL|COL|FO)
    quantite: float | None = None     # quantité facturée (nb palettes/colis/kg)
    exp_date: str | None = None       # date d'expédition (jj/mm/aaaa) de la ligne facture
    facture: str | None = None        # n° de la facture SOFRIPA d'où vient la ligne
    commande: Commande | None = None  # référence vers la commande (colonnes brutes)
    methode: str = "piece"            # "piece" (sûr, via N° pièce) | "deduit" (nom+ville+date)
    confiance: str = ""               # "" (pièce) | "haute" (unique) | "date" (départagé par date)


@dataclass
class GroupeEnseigne:
    enseigne: str
    nb_livraisons: int = 0
    poids_sofripa: float = 0.0
    ecart_kg: float = 0.0
    cout_transport: float = 0.0
    montant_ht: float = 0.0

    @property
    def eur_par_kg(self):
        return (self.cout_transport / self.poids_sofripa) if self.poids_sofripa else None

    @property
    def transport_sur_ht(self):
        return (self.cout_transport / self.montant_ht) if self.montant_ht else None


@dataclass
class Resultat:
    """Tout ce que produit le cœur."""
    lignes: list                      # list[LigneReconciliee] (appariées)
    sans_piece: list                  # list[LigneFacture] non rapprochables
    internes: list                    # list[LigneFacture] transferts internes (exclus)
    par_enseigne: list                # list[GroupeEnseigne], triés par coût décroissant
    kpis: dict                        # indicateurs globaux
    # Suggestions pour les lignes sans pièce (alignées sur sans_piece, Commande|None).
    # ⚠️ SUPPOSITIONS à vérifier à la main — jamais utilisées dans les calculs/KPIs.
    sans_piece_suggestions: list = field(default_factory=list)


# ============================ Logique du cœur =================================
def _est_interne(client: str | None) -> bool:
    """Un transfert interne = mouvement entre les sites de Symbiose elle-même
    (production Ivry-sur-Seine ↔ plateforme SOFRIPA Wissous). Le destinataire
    commence par « SYMBIOSE » sous plusieurs variantes :
      « SYMBIOSE KEFIR (94) IVRY… », « SYMBIOSE (94) IVRY… »,
      « SYMBIOSE KEFIR CHEZ SOF (91) WISSOUS »…
    Aucun client externe ne s'appelle SYMBIOSE → on couvre tout le préfixe.
    """
    return (client or "").strip().upper().startswith("SYMBIOSE")


def _statut(poids_eb, poids_sof, ecart_kg, ecart_pct, seuil_pct) -> str:
    """Réplique en Python la règle qui était dans les formules Excel."""
    if poids_sof is None:
        return STATUT_PALETTE
    if poids_eb in (None, 0):
        return STATUT_PAS_POIDS_EB
    if ecart_kg is not None and ecart_kg < 0:
        return STATUT_NEGATIF
    if ecart_pct is not None and ecart_pct > seuil_pct:
        return STATUT_NOTABLE
    return STATUT_OK


def _enseigne_de(facture: LigneFacture, commande: Commande) -> str:
    """Regroupement : la tournée Easy Beer si dispo, sinon le client facture nettoyé."""
    if commande.tournee:
        return commande.tournee
    c = facture.client or ""
    m = re.search(r'\((\d{2})\)', c)   # retire le "(59)" etc. du client facture
    return (c[:m.start()].strip() if m else c.strip())


def to_dict(res: "Resultat") -> dict:
    """Sérialise un Resultat en dict JSON-compatible (pour stockage en base)."""
    return asdict(res)


def from_dict(d: dict) -> "Resultat":
    """Reconstruit un Resultat depuis un dict sérialisé par to_dict()."""
    def _cmd(c):
        return Commande(**c) if c else None

    lignes = []
    for lig in d.get("lignes", []):
        lig = dict(lig)
        lig["commande"] = _cmd(lig.get("commande"))
        lignes.append(LigneReconciliee(**lig))

    return Resultat(
        lignes=lignes,
        sans_piece=[LigneFacture(**f) for f in d.get("sans_piece", [])],
        internes=[LigneFacture(**f) for f in d.get("internes", [])],
        par_enseigne=[GroupeEnseigne(**g) for g in d.get("par_enseigne", [])],
        kpis=d.get("kpis", {}),
        sans_piece_suggestions=[_cmd(c) for c in d.get("sans_piece_suggestions", [])],
    )


def _norm_client(s) -> set:
    """Normalise un nom de client en jeu de tokens (minuscules, sans accents).

    - Retire seulement les codes département « (94) » ; GARDE le contenu alpha
      des parenthèses (ex. « (SCAPNOR) », « (Biomonde) ») qui porte souvent la
      vraie raison sociale.
    - Colle les apostrophes (« O'TERA » → « OTERA ») pour matcher la casse EB.
    - Ignore les mots génériques (SAS, Magasin, Sainte…).
    """
    s = re.sub(r"\(\d{2,3}\)", " ", s or "")                    # retire (94), (59)…
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))   # enlève les accents
    # Élisions françaises (d', l', j'…) → séparateur : « d'Avelin » → « Avelin ».
    s = re.sub(r"\b[ldjcmnst]'", " ", s, flags=re.IGNORECASE)
    # Apostrophe restante = interne à un nom de marque → collage : « O'TERA » → « OTERA ».
    s = s.replace("'", "").replace("’", "").replace("`", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return {t for t in s.split() if len(t) > 2 and t not in _STOP_ENSEIGNE}


def _tokens_marque(client) -> set:
    """Tokens de la PARTIE MARQUE du client SOFRIPA (avant le code dépt « (59) »).
    Sert d'ANCRE au rapprochement : un match doit partager au moins un token de
    marque (« naturalia », « carrefour »…), pas seulement un mot de lieu générique
    (« bois » dans Aulnay-sous-Bois ↔ Bois-Colombes) qui provoquerait un faux
    appariement entre deux enseignes différentes."""
    s = client or ""
    m = re.search(r"\(\d{2,3}\)", s)
    return _norm_client(s[: m.start()] if m else s)


def _date_commande(cmd) -> "datetime.date | None":
    """Date de référence d'une commande EB (livr. réelle > prévue > création)."""
    return _parse_date_fr(
        cmd.brut.get("Date de livr. réelle")
        or cmd.brut.get("Date de livr. prévue")
        or cmd.brut.get("Date de création")
    )


def _index_tokens(commandes_par_num) -> dict:
    """token normalisé -> ensemble des noms clients EB qui le contiennent.
    Sert à détecter les enseignes « mono-site » (1 seul client → la marque
    seule suffit à identifier, ex. BIODIS, RELAIS VERT)."""
    idx: dict = {}
    for cmd in commandes_par_num.values():
        for t in _norm_client(cmd.client):
            idx.setdefault(t, set()).add(cmd.client)
    return idx


def _apparier_deduit(factures_sans_piece, commandes_par_num, fenetre=FENETRE_DEDUIT,
                     exclues=None):
    """2e passe : rapproche les lignes SANS pièce d'une commande EB par
    marque + ville + date, quand c'est SÛR.

    Un candidat « fort » partage avec la commande : (1) un token DISCRIMINANT
    (ville/magasin ne désignant qu'≤ SEUIL clients EB), (2) un token de la MARQUE
    SOFRIPA (ancre, pas un simple mot de lieu), et (3) soit un token mono-site,
    soit ≥ 2 tokens communs (anti-coïncidence). Filtre par fenêtre de date.

    UNICITÉ : une commande EB = une livraison. Chaque commande n'est attribuée
    qu'à UNE ligne (affectation gloutonne : la ligne dont l'écart de date est le
    plus petit sert en premier). `exclues` = commandes déjà prises par le
    rapprochement par pièce (jamais réutilisées ici).

    Retourne (apparies, restants) où apparies = list[(facture, num, cmd, conf)].
    """
    exclues = set(exclues or ())
    tok_idx = _index_tokens(commandes_par_num)
    def discriminant(t):
        # token ne désignant qu'≤ SEUIL clients EB : une ville/un magasin précis
        # ou une marque mono-site. Un simple nom d'enseigne (« vie », « claire »,
        # « intermarche ») partagé par des dizaines de magasins ne l'est PAS.
        return 0 < len(tok_idx.get(t, ())) <= SEUIL_DISCRIMINANT

    def mono(t):
        # token désignant UN seul client EB (marque mono-site : BIODIS, VITAFRAIS…).
        return len(tok_idx.get(t, ())) == 1

    # ── Étape 1 : candidats forts par ligne, triés par (date, poids) ──────────
    par_ligne = []   # (facture, [cands triés], nb_commandes_distinctes)
    for f in factures_sans_piece:
        ftok = _norm_client(f.client)
        if not ftok:
            par_ligne.append((f, [], 0))
            continue
        fmarque = _tokens_marque(f.client)   # ancre : tokens de la marque
        d_fac = _parse_date_fr(f.exp_date)
        poids_f = f.poids
        cands = []  # (num, cmd, ecart_jours|None)
        for num, cmd in commandes_par_num.items():
            if num in exclues:
                continue
            inter = ftok & _norm_client(cmd.client)
            if not inter:
                continue
            if not any(discriminant(t) for t in inter):
                continue
            if fmarque and not (inter & fmarque):
                continue
            if not (any(mono(t) for t in inter) or len(inter) >= 2):
                continue
            d_cmd = _date_commande(cmd)
            if d_fac and d_cmd:
                ecart = abs((d_fac - d_cmd).days)
                if ecart > fenetre:
                    continue
            else:
                ecart = None
            cands.append((num, cmd, ecart))

        def _cle(c):
            ecart_j = c[2] if c[2] is not None else 10 ** 6
            ecart_p = (abs((poids_f or 0) - (c[1].poids or 0))
                       if (poids_f and c[1].poids) else 10 ** 9)
            return (ecart_j, ecart_p)

        # une entrée par commande distincte (la meilleure), triée
        best_par_num = {}
        for c in cands:
            if c[0] not in best_par_num or _cle(c) < _cle(best_par_num[c[0]]):
                best_par_num[c[0]] = c
        classe = sorted(best_par_num.values(), key=_cle)
        classe = [(c[0], c[1], _cle(c)) for c in classe]   # (num, cmd, cle)
        par_ligne.append((f, classe))

    # ── Étape 2 : affectation gloutonne, 1 commande par ligne ─────────────────
    # Priorité aux lignes dont le meilleur candidat colle le mieux en date : elles
    # réservent leur commande d'abord, les suivantes prennent la meilleure LIBRE.
    def _meilleur_ecart(item):
        classe = item[1]
        return classe[0][2] if classe else (10 ** 6, 0)

    ordre = sorted(range(len(par_ligne)), key=lambda i: _meilleur_ecart(par_ligne[i]))
    pris = set()
    resultat = {}   # index_ligne -> (num, cmd, conf)
    for i in ordre:
        f, classe = par_ligne[i]
        libres = [(num, cmd, cle) for num, cmd, cle in classe if num not in pris]
        if not libres:
            continue
        # n'attribuer que si le meilleur LIBRE est STRICTEMENT meilleur que le
        # suivant : sur une égalité parfaite (2 commandes aussi proches), on ne
        # devine pas → la ligne reste sans pièce (revue humaine).
        if len(libres) == 1 or libres[0][2] < libres[1][2]:
            num, cmd, _cle_v = libres[0]
            pris.add(num)
            resultat[i] = (num, cmd, "haute" if len(classe) == 1 else "date")

    apparies, restants = [], []
    for i, (f, _classe) in enumerate(par_ligne):
        if i in resultat:
            num, cmd, conf = resultat[i]
            apparies.append((f, num, cmd, conf))
        else:
            restants.append(f)
    return apparies, restants


def _resoudre_commande(piece, client, poids, exp_date, commandes_par_num, offset):
    """Résout la commande Easy Beer correspondant à un N° pièce SOFRIPA.

    Règle « candidats existants » (robuste, sans seuil ni date codés en dur) :
    on calcule les deux candidats possibles et on garde celui (ou ceux) qui
    existe(nt) réellement parmi les commandes :
      - cand « +4000 » = pièce − 4000   (régime historique)
      - cand « direct » = pièce          (régime nouveau, série 7191+)

    Sur les données actuelles, le trou de numérotation 3191-7190 (côté commande)
    garantit qu'au plus UN candidat existe → aucune ambiguïté possible.

    ⚠️ Le régime « direct » (7191+) est DÉDUIT : il repose sur la connaissance
    métier + la continuité de numérotation (l'ancien régime s'arrête pile à la
    commande 3190 → pièce 7190, le nouveau démarre à la commande 7191). Il n'a
    PAS encore pu être observé sur facture (1re facture de la série attendue en
    juin) — à CONFIRMER sur la 1re facture de juin.

    Retourne (num_commande, Commande) ou (None, None) si non résolu.
    """
    try:
        p = int(piece)
    except (TypeError, ValueError):
        return None, None

    candidats = []  # liste de (num, Commande), régime +4000 d'abord puis direct
    for num in (p - offset, p):
        cmd = commandes_par_num.get(num)
        if cmd is not None and all(num != n for n, _ in candidats):
            candidats.append((num, cmd))

    # Contrôle de COHÉRENCE DE DATE : un n° pièce qui tombe sur une commande
    # éloignée de plusieurs mois = FAUX appariement (coïncidence de numéro entre
    # deux périodes, ex. facture 2023 dont la pièce−4000 tombe sur une commande
    # 2025 réutilisant le même numéro). On le rejette → « sans pièce », honnête.
    # NB : on NE filtre PAS sur le client (SOFRIPA étiquette parfois le
    # distributeur, ex. SAMADA, là où EasyBeer a le magasin, ex. NATURALIA).
    candidats = [(n, c) for (n, c) in candidats if _date_coherente(exp_date, c)]

    if not candidats:
        return None, None
    if len(candidats) == 1:
        return candidats[0]

    # Collision (n et n-4000 valides ET dates cohérentes) : départage par
    # client puis poids, avec un avertissement loggé pour inspection.
    fac_tokens = _norm_client(client)

    def _cle(item):
        _num, cmd = item
        score_client = len(fac_tokens & _norm_client(cmd.client))
        ecart_poids = (
            abs(poids - cmd.poids)
            if (poids is not None and cmd.poids is not None)
            else float("inf")
        )
        return (score_client, -ecart_poids)

    candidats.sort(key=_cle, reverse=True)
    _log.warning(
        "N° pièce %s ambigu (candidats commandes %s) — départage par recoupement → %s",
        p, [n for n, _ in candidats], candidats[0][0],
    )
    return candidats[0]


def _date_coherente(exp_date, cmd, max_jours=90) -> bool:
    """True si la date d'expédition facture et la commande sont proches (≤ max_jours).

    Sert à rejeter les appariements par simple coïncidence de numéro entre deux
    périodes éloignées. Si l'une des deux dates est inconnue, bénéfice du doute
    (True) pour ne pas rejeter un vrai appariement sur une info manquante.
    """
    d_fac = _parse_date_fr(exp_date)
    d_cmd = _parse_date_fr(
        cmd.brut.get("Date de livr. réelle")
        or cmd.brut.get("Date de livr. prévue")
        or cmd.brut.get("Date de création")
    )
    if not d_fac or not d_cmd:
        return True
    return abs((d_fac - d_cmd).days) <= max_jours


def _parse_date_fr(v):
    """'17/04/26', '17/04/2026', '17/04/2026 à 09:00' ou datetime -> date. Sinon None."""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    m = re.match(r"(\d{2})/(\d{2})/(\d{2,4})", str(v).strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None


def _suggerer_commande(facture, commandes_par_num, fenetre_jours=3):
    """Suggestion de commande EB pour une ligne de facture SANS pièce exploitable.

    Croisement d'indices, comme l'ancien outil Excel (« Suggestion Easy Beer
    (à vérifier) ») :
      1. client (obligatoire) — tokens normalisés communs entre le client
         facture et le client commande ;
      2. date — expédition SOFRIPA vs livraison réelle EB (± fenetre_jours,
         si les deux dates sont connues) ;
      3. poids — départage final (le plus proche gagne).

    ⚠️ Retourne une SUPPOSITION (Commande) ou None — à vérifier à la main,
    jamais intégrée aux appariements ni aux KPIs.
    """
    fac_tokens = _norm_client(facture.client)
    if not fac_tokens:
        return None
    d_fac = _parse_date_fr(facture.exp_date)
    best, best_key, best_ecart_j = None, None, None
    for cmd in commandes_par_num.values():
        score = len(fac_tokens & _norm_client(cmd.client))
        if score == 0:
            continue
        d_cmd = _parse_date_fr(
            cmd.brut.get("Date de livr. réelle") or cmd.brut.get("Date de livr. prévue")
        )
        # La date ne sert qu'à DÉPARTAGER (un match de nom fort gagne toujours,
        # même si la date est lointaine — cas « INTERMARCHE Lepic »).
        ecart_j = abs((d_fac - d_cmd).days) if (d_fac and d_cmd) else 999
        d_poids = abs((facture.poids or 0) - (cmd.poids or 0))
        key = (score, -ecart_j, -d_poids)
        if best_key is None or key > best_key:
            best, best_key, best_ecart_j = cmd, key, ecart_j
    # Garde-fou : un match faible (1 seul mot commun) n'est suggéré que si la
    # date colle aussi — sinon c'est du bruit, on préfère « aucune piste ».
    if best_key is not None and best_key[0] <= 1 and best_ecart_j > fenetre_jours:
        return None
    return best


def _faire_ligne(f, num, cmd, seuil_pct, methode="piece", confiance="") -> "LigneReconciliee":
    """Construit une LigneReconciliee (calculs poids/coût) — partagé entre le
    rapprochement par pièce (sûr) et la 2e passe déduite (nom+ville+date)."""
    poids_eb = cmd.poids
    poids_sof = f.poids  # poids RÉEL en kg (col. Poids de la facture), toute unité
    ecart_kg = (poids_sof - poids_eb) if (poids_sof is not None and poids_eb is not None) else None
    ecart_pct = (ecart_kg / poids_eb) if (ecart_kg is not None and poids_eb) else None
    cout = f.montant
    ht = cmd.ht
    return LigneReconciliee(
        numero=num, client=cmd.client, ot=f.ot, piece=f.piece,
        poids_eb=poids_eb, poids_sofripa=poids_sof,
        ecart_kg=ecart_kg, ecart_pct=ecart_pct,
        cout_transport=cout, montant_ht=ht,
        transport_sur_ht=(cout / ht) if (cout is not None and ht) else None,
        eur_par_kg=(cout / poids_sof) if (cout is not None and poids_sof) else None,
        statut=_statut(poids_eb, poids_sof, ecart_kg, ecart_pct, seuil_pct),
        surtaxe_gasoil=f.surtaxe_gasoil,
        unite=getattr(f, "unite", None), quantite=getattr(f, "quantite", None),
        exp_date=f.exp_date, facture=getattr(f, "facture", None),
        commande=cmd, methode=methode, confiance=confiance,
    )


def reconcilier(factures, commandes_par_num, seuil_pct=SEUIL_PCT_DEFAUT,
                offset=OFFSET, deduction=True, commandes_reservees=None) -> Resultat:
    """
    factures            : iterable[LigneFacture]
    commandes_par_num   : dict[int, Commande]
    deduction           : active la 2e passe (rapprochement déduit nom+ville+date)
                          pour les lignes sans pièce exploitable.
    commandes_reservees : commandes EB déjà attribuées AILLEURS (autres mois) —
                          la 2e passe ne les réutilise pas (unicité inter-période).
    """
    lignes, sans_piece, internes = [], [], []

    for f in factures:
        if _est_interne(f.client):
            internes.append(f)
            continue
        if not f.piece:
            sans_piece.append(f)
            continue
        num, cmd = _resoudre_commande(
            f.piece, f.client, f.poids, f.exp_date, commandes_par_num, offset,
        )
        if cmd is None:
            sans_piece.append(f)   # pièce présente mais aucune commande en face (ou non résolue)
            continue
        lignes.append(_faire_ligne(f, num, cmd, seuil_pct))

    # ---- 2e passe : récupérer les lignes SANS pièce par marque + ville + date ----
    if deduction:
        # Les commandes déjà consommées par le rapprochement par pièce (ce mois)
        # OU réservées ailleurs (autres mois) ne peuvent PAS être réutilisées par
        # la 2e passe (une commande = une livraison).
        deja_prises = {L.numero for L in lignes} | set(commandes_reservees or ())
        apparies, sans_piece = _apparier_deduit(
            sans_piece, commandes_par_num, exclues=deja_prises,
        )
        for f, num, cmd, conf in apparies:
            lignes.append(_faire_ligne(f, num, cmd, seuil_pct,
                                       methode="deduit", confiance=conf))

    par_enseigne, kpis = _synthese_et_kpis(lignes, sans_piece, internes)
    return Resultat(lignes=lignes, sans_piece=sans_piece, internes=internes,
                    par_enseigne=par_enseigne, kpis=kpis,
                    sans_piece_suggestions=[
                        _suggerer_commande(f, commandes_par_num) for f in sans_piece
                    ])


def _synthese_et_kpis(lignes, sans_piece, internes) -> tuple[list, dict]:
    """Calcule (par_enseigne, kpis) à partir des lignes réconciliées. Partagé
    entre reconcilier() (1 période) et agreger() (fusion de plusieurs mois)."""
    # ---- Synthèse par enseigne ----
    groupes: dict = {}
    for L in lignes:
        key = _enseigne_de(LigneFacture(client=L.client, ot=L.ot, piece=L.piece,
                                        poids=L.poids_sofripa, montant=L.cout_transport),
                           L.commande or Commande(numero=L.numero, client=L.client))
        g = groupes.setdefault(key, GroupeEnseigne(enseigne=key))
        g.nb_livraisons += 1
        g.cout_transport += L.cout_transport or 0
        g.montant_ht += L.montant_ht or 0
        if L.poids_sofripa:
            g.poids_sofripa += L.poids_sofripa
        if L.poids_sofripa and L.poids_eb:
            g.ecart_kg += (L.poids_sofripa - L.poids_eb)
    par_enseigne = sorted(groupes.values(), key=lambda g: -g.cout_transport)

    # ---- KPIs globaux ----
    comparables = [L for L in lignes if L.poids_sofripa and L.poids_eb]
    poids_sof_total = sum(L.poids_sofripa for L in comparables)
    poids_eb_total = sum(L.poids_eb for L in comparables)
    cout_total = sum((L.cout_transport or 0) for L in lignes)
    ht_total = sum((L.montant_ht or 0) for L in lignes)
    sof_pos = [L for L in lignes if L.poids_sofripa]
    cout_sof_pos = sum((L.cout_transport or 0) for L in sof_pos)
    poids_sof_pos = sum(L.poids_sofripa for L in sof_pos)

    kpis = {
        "livraisons_appariees": len(lignes),
        "livraisons_par_piece": sum(1 for L in lignes if L.methode == "piece"),
        "livraisons_deduites": sum(1 for L in lignes if L.methode == "deduit"),
        "lignes_sans_piece": len(sans_piece),
        "transferts_internes": len(internes),
        "poids_sofripa_comparable_kg": round(poids_sof_total, 2),
        "poids_easybeer_comparable_kg": round(poids_eb_total, 2),
        "ecart_poids_total_kg": round(poids_sof_total - poids_eb_total, 2),
        "lignes_a_verifier_negatif": sum(1 for L in lignes if L.statut == STATUT_NEGATIF),
        "ecarts_notables": sum(1 for L in lignes if L.statut == STATUT_NOTABLE),
        "cout_transport_total_eur": round(cout_total, 2),
        "gasoil_total_eur": round(sum(L.surtaxe_gasoil or 0.0 for L in lignes), 2),
        "montant_ht_total_eur": round(ht_total, 2),
        "part_transport_dans_ht": round(cout_total / ht_total, 4) if ht_total else None,
        "cout_moyen_eur_par_kg": round(cout_sof_pos / poids_sof_pos, 3) if poids_sof_pos else None,
    }
    return par_enseigne, kpis


def agreger(resultats: list[Resultat]) -> Resultat:
    """Fusionne plusieurs Resultat mensuels en un seul (pour afficher une plage
    de mois). Concatène les lignes et recalcule enseignes + KPIs sur l'ensemble."""
    lignes, sans_piece, internes, suggestions = [], [], [], []
    for r in resultats:
        lignes.extend(r.lignes)
        sans_piece.extend(r.sans_piece)
        internes.extend(r.internes)
        suggestions.extend(
            r.sans_piece_suggestions or [None] * len(r.sans_piece)
        )
    par_enseigne, kpis = _synthese_et_kpis(lignes, sans_piece, internes)
    return Resultat(lignes=lignes, sans_piece=sans_piece, internes=internes,
                    par_enseigne=par_enseigne, kpis=kpis,
                    sans_piece_suggestions=suggestions)
