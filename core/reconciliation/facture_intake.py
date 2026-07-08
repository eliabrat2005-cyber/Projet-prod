"""
facture_intake.py — Lecteur ENRICHI d'une facture SOFRIPA (Phase 1 « intake »).

Extrait la structure COMPLÈTE demandée par le CDC :
  - entête : n° facture, date, Maj Go, Maj GNR, HT, TVA, TTC ;
  - chaque ligne de transport : date d'expédition, n° OT, n° pièce (clé de
    matching), expéditeur, destinataire, poids + unité, transport, frais admin,
    montant_brut (= transport + frais admin).

Puis on branche la répartition gasoil (gasoil.allouer_gasoil) pour obtenir le
montant_final par ligne, avec Σ montant_final = HT au centime.

Différent de io_files.lire_facture (utilisé par la réconciliation, inchangé) :
ici on veut TOUS les champs + l'entête, pour le registre immuable.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .gasoil import allouer_gasoil

# Bandes de colonnes (positions x) de la facture SOFRIPA — calées sur ce format.
COLS = {
    "date": (0, 60), "desig": (60, 300), "poids": (300, 345), "colis": (345, 374),
    "palette": (374, 417), "qte": (417, 457), "unite": (457, 498),
    "pu": (498, 516), "montant": (516, 999),
}


@dataclass
class LigneFactureIntake:
    exp_date: str | None = None       # date propre à la ligne (jj/mm)
    jour: str | None = None           # jour d'expédition (entête « Expédition du jj/mm/aa »)
    num_ot: str | None = None         # N° OT (ordre de transport SOFRIPA)
    num_piece: str | None = None      # N° pièce (clé de matching : pièce−4000 = cmd)
    expediteur: str | None = None
    destinataire: str | None = None
    poids: float | None = None
    unite: str | None = None          # KGS ou PAL
    transport: float | None = None    # coût transport de la ligne
    frais_admin: float | None = None  # frais administratif (~2,13)
    montant_brut: float | None = None # transport + frais_admin (avant gasoil)
    surtaxe_gasoil: float | None = None  # rempli après allocation
    montant_final: float | None = None   # brut + surtaxe (après gasoil)
    # Liaison Easy Beer (Étape 2)
    id_commande_easybeer: int | None = None
    client_easybeer: str | None = None
    statut_match: str | None = None      # OK | sans_piece | commande_absente | non_livree


@dataclass
class FactureIntake:
    id_facture: str | None = None
    date_facture: str | None = None
    maj_go: float = 0.0
    maj_gnr: float = 0.0
    maj_total: float = 0.0
    montant_ht: float | None = None
    taux_tva: float | None = None
    montant_tva: float | None = None
    montant_ttc: float | None = None
    lignes: list = field(default_factory=list)
    totaux_journaliers: dict = field(default_factory=dict)  # {jj/mm/aa: total_montant}


def _num(s: str) -> float | None:
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _band(x: float) -> str | None:
    for k, (a, b) in COLS.items():
        if a <= x < b:
            return k


def _montant_rmost(toks) -> float | None:
    """Nombre le plus à droite dans la bande 'montant'."""
    cands = sorted((x, t) for x, t in toks if _band(x) == "montant")
    return _num(cands[-1][1]) if cands else None


def parse_facture(path) -> FactureIntake:
    """Lit un PDF SOFRIPA -> FactureIntake (entête + lignes + gasoil réparti)."""
    import pdfplumber

    fac = FactureIntake()
    rows: list[tuple[float, list]] = []  # (y global, tokens) dans l'ordre du doc
    full_text = ""
    with pdfplumber.open(path) as pdf:
        for pnum, page in enumerate(pdf.pages):
            full_text += (page.extract_text() or "") + "\n"
            lines = defaultdict(list)
            for w in page.extract_words(keep_blank_chars=False):
                lines[round(w["top"])].append((w["x0"], w["text"]))
            for y in sorted(lines):
                rows.append((pnum * 10000 + y, sorted(lines[y])))

    # ── Entête (n° facture, date, majorations + totaux) ──────────────────
    m = re.search(r"\bSO0?\d{5,6}\b", full_text)
    fac.id_facture = m.group(0) if m else None
    m = re.search(r"FACTURE\s+(\d{2}/\d{2}/\d{2})", full_text)
    fac.date_facture = m.group(1) if m else None
    # Ligne de totaux : "1 768,66 104,99 12 518,68 20,00 2 503,74 15 022,42 EUR"
    m = re.search(
        r"([\d  ]+,\d{2})\s+([\d  ]+,\d{2})\s+([\d  ]+,\d{2})\s+"
        r"(\d{1,2},\d{2})\s+([\d  ]+,\d{2})\s+([\d  ]+,\d{2})\s*EUR",
        full_text,
    )
    if m:
        fac.maj_go = _num(m.group(1)) or 0.0
        fac.maj_gnr = _num(m.group(2)) or 0.0
        fac.montant_ht = _num(m.group(3))
        fac.taux_tva = _num(m.group(4))
        fac.montant_tva = _num(m.group(5))
        fac.montant_ttc = _num(m.group(6))
    fac.maj_total = round(fac.maj_go + fac.maj_gnr, 2)

    # ── Lignes de transport (machine à états) ────────────────────────────
    # Deux formats de ligne existent :
    #   - normal  : montant transport sur la ligne « ... <poids> KGS/PAL <PU> <montant> »
    #   - forfait : montant transport sur la ligne « FRAIS <poids> ... FO <montant> »
    # → on prend le DERNIER montant vu dans le bloc (avant FRAIS ADMINISTRATIF).
    cur: LigneFactureIntake | None = None
    cand_transport: float | None = None
    jour_courant: str | None = None

    def _fermer():
        """Clôt la livraison courante (append). Fonctionne MÊME sans ligne
        « FRAIS ADMINISTRATIF » (cas TAXI COLIS). N'append que si un montant
        transport a été capté."""
        nonlocal cur, cand_transport
        if cur is not None and cand_transport is not None:
            cur.transport = cand_transport
            if cur.frais_admin is None:
                cur.frais_admin = 0.0
            cur.montant_brut = round(
                (cur.transport or 0.0) + (cur.frais_admin or 0.0), 2
            )
            fac.lignes.append(cur)
        cur = None
        cand_transport = None

    for _y, toks in rows:
        txt = " ".join(t for _, t in toks)
        seq = [t for _, t in toks]

        mj = re.search(r"Exp[ée]dition du\s+(\d{2}/\d{2}/\d{2})", txt)
        if mj:
            jour_courant = mj.group(1)
            continue

        # Ligne de SURCOÛT autonome (ex. « SURCOÛT SALON 1,00 120,00 120,00 ») :
        # un supplément facturé, PAS une livraison (ni destinataire ni pièce). On
        # la clôt comme une ligne à part entière pour qu'elle compte dans le total
        # du jour ET dans Σ montant = HT (sinon la facture est rejetée à tort).
        if txt.strip().upper().startswith("SURCO"):
            mv = _montant_rmost(toks)
            if mv is None and seq:
                mv = _num(seq[-1])
            if mv:
                _fermer()          # clôt une éventuelle livraison en cours
                sc = LigneFactureIntake()
                sc.jour = jour_courant
                sc.destinataire = " ".join(
                    t for t in seq if not re.fullmatch(r"[\d ., ]+", t)
                ).strip() or "SURCOUT"
                sc.transport = mv
                sc.frais_admin = 0.0
                sc.montant_brut = mv
                fac.lignes.append(sc)
            continue

        if "EXP." in txt and "DEST" not in txt:
            _fermer()                       # clôt la livraison précédente (ex. TAXI COLIS)
            cur = LigneFactureIntake()
            cur.jour = jour_courant
            dt = [t for x, t in toks if _band(x) == "date"
                  and re.match(r"\d{2}/\d{2}", t)]
            cur.exp_date = dt[0] if dt else None
            desig = [t for x, t in toks if _band(x) == "desig" and t not in ("EXP.", ":")]
            cur.expediteur = " ".join(desig).strip() or None
            continue

        if cur is None:
            continue

        # Artefacts de bas/haut de page : ne pas polluer le montant transport.
        if ("Report" in txt or "Nbre OT" in txt or "N° COMPTE" in txt
                or "Page " in txt or txt.startswith("FACTURE")):
            continue

        if "DEST.:" in txt:
            ot = [t for x, t in toks if _band(x) == "date" and re.match(r"^\d{8,}$", t)]
            cur.num_ot = ot[0] if ot else None
            desig = [t for x, t in toks if _band(x) == "desig"]
            if desig and desig[0] == "DEST.:":
                desig = desig[1:]
            cur.destinataire = " ".join(desig).strip() or None
            continue

        # N° pièce : « 00006800 » (normal) ou « C0076789 » (taxi colis)
        if re.match(r"^(0000\d{4}|C\d{5,8})$", txt.strip()):
            cur.num_piece = txt.strip()
            continue

        # Ligne « FRAIS ADMINISTRATIF ... 2,13 » → clôture la livraison
        if "ADMINISTRATIF" in txt:
            cur.frais_admin = _montant_rmost(toks)
            _fermer()
            continue

        # Toute autre ligne du bloc : poids, unité, et candidat montant transport.
        pb = [t for x, t in toks if _band(x) == "poids"]
        if pb and cur.poids is None:
            cur.poids = _num(pb[0])
        for u in ("KGS", "PAL", "FO", "COL"):
            if u in seq:
                cur.unite = u
                i = seq.index(u)
                if i > 0 and _num(seq[i - 1]) is not None:
                    cur.poids = _num(seq[i - 1])
                break
        mv = _montant_rmost(toks)
        if mv is not None:
            cand_transport = mv

    _fermer()  # clôt la dernière livraison de la facture

    # ── Totaux journaliers imprimés (pour la validation) ─────────────────
    # « TOTAL JOURNALIER DU 16/04/26 : Nbre OT : 5 ... 371,01 » → dernier nombre.
    for m in re.finditer(
        r"TOTAL JOURNALIER DU\s+(\d{2}/\d{2}/\d{2}).*?([\d  ]+,\d{2})\s*$",
        full_text, re.MULTILINE,
    ):
        fac.totaux_journaliers[m.group(1)] = _num(m.group(2))

    # ── Répartition gasoil sur les lignes ────────────────────────────────
    bruts = [L.montant_brut or 0.0 for L in fac.lignes]
    alloc = allouer_gasoil(bruts, fac.maj_total)
    for L, a in zip(fac.lignes, alloc):
        L.surtaxe_gasoil = a["surtaxe"]
        L.montant_final = a["final"]

    return fac


def valider_facture(fac: FactureIntake, tol: float = 0.02) -> list[str]:
    """Contrôles « les comptes tombent juste » du CDC (hors EasyBeer, cf. Étape 2).

    Retourne la liste des erreurs (vide = facture valide). Règle tout-ou-rien :
    la moindre erreur → la facture sera rejetée en amont de l'insertion BD.
    """
    err: list[str] = []

    # 1. Champs entête présents
    if not fac.id_facture:
        err.append("N° de facture introuvable")
    if fac.montant_ht is None:
        err.append("Montant HT introuvable")
    if not fac.lignes:
        err.append("Aucune ligne de transport extraite")

    # 2. Montants de ligne positifs — SAUF transferts internes gratuits.
    # Un enlèvement interne vers un site SYMBIOSE (ex. Ivry) peut légitimement
    # être facturé 0 € : ce n'est PAS une erreur de lecture, on ne rejette pas
    # la facture pour ça (ces lignes sont de toute façon exclues de la
    # réconciliation, cf. _est_interne).
    from .reconciliation_core import _est_interne

    for i, L in enumerate(fac.lignes, 1):
        interne = _est_interne(L.destinataire)
        if (not L.transport or L.transport <= 0) and not interne:
            err.append(f"Ligne {i} (pièce {L.num_piece}) : transport ≤ 0")
        if L.poids is not None and L.poids <= 0 and not interne:
            err.append(f"Ligne {i} (pièce {L.num_piece}) : poids ≤ 0")

    # 3. Réconciliation globale : Σ montant_final == HT (après gasoil)
    if fac.montant_ht is not None:
        somme_final = round(sum(L.montant_final or 0.0 for L in fac.lignes), 2)
        if abs(somme_final - fac.montant_ht) > tol:
            err.append(
                f"Σ montants finaux {somme_final} ≠ HT {fac.montant_ht} "
                f"(écart {round(somme_final - fac.montant_ht, 2)})"
            )

    # 4. Réconciliation journalière : Σ brut du jour == total journalier imprimé
    par_jour: dict[str, float] = {}
    for L in fac.lignes:
        if L.jour:
            par_jour[L.jour] = round(par_jour.get(L.jour, 0.0) + (L.montant_brut or 0.0), 2)
    for jour, total_pdf in fac.totaux_journaliers.items():
        calc = par_jour.get(jour)
        if calc is None:
            err.append(f"Jour {jour} : total imprimé {total_pdf} mais aucune ligne")
        elif total_pdf is not None and abs(calc - total_pdf) > tol:
            err.append(
                f"Jour {jour} : Σ lignes {calc} ≠ total journalier {total_pdf} "
                f"(écart {round(calc - total_pdf, 2)})"
            )
    return err
