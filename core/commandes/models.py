"""
core/commandes/models.py
========================
Dataclasses typées pour une commande magasin extraite d'un PDF.

``from_dict`` est défensif : il accepte la sortie brute de Claude (ou d'une
ligne DB) sans planter sur un champ manquant / mal typé. C'est le contrat
unique entre l'IA, le service et l'UI.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

_CONFIANCES = {"haute", "moyenne", "basse"}

# Contenance d'une bouteille (ex. "33cl", "75 cl", "1L") -> séparée de la gamme.
_TAILLE_RE = re.compile(r"\b(\d{1,3})\s*cl\b", re.IGNORECASE)


def _extract_taille(libelle: str) -> tuple[str, str]:
    """Sépare la contenance (ex. '33cl') du libellé.

    Retourne ``(libelle_sans_taille, taille)``. ``taille`` est normalisée en
    ``"33 cl"`` ; vide si aucune contenance détectée. Le libellé est nettoyé
    des virgules/espaces orphelins laissés par le retrait.
    """
    m = _TAILLE_RE.search(libelle)
    if not m:
        return libelle.strip(), ""
    taille = f"{m.group(1)} cl"
    clean = libelle[: m.start()] + libelle[m.end():]
    clean = re.sub(r"\s{2,}", " ", clean)        # espaces multiples
    clean = re.sub(r"\s+([,;])", r"\1", clean)    # espace avant ponctuation
    clean = re.sub(r"([,;])\s*([,;])", r"\1", clean)  # ponctuation doublée
    clean = clean.strip().strip(",;").strip()
    return clean, taille


def _clean_str(v: Any) -> str:
    """Force une valeur en chaîne propre (jamais None)."""
    if v is None:
        return ""
    return str(v).strip()


def _as_float(v: Any) -> float | None:
    """Parse un nombre tolérant ('1 200,5', '1.200', 12) -> float, sinon None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    # Retire espaces (milliers) et insécables ; virgule décimale -> point.
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    # Garde uniquement chiffres, point et signe.
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s in {"-", ".", "-."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _as_bool(v: Any, *, default: bool) -> bool:
    """Parse un booléen tolérant. ``default`` si la valeur est absente/inconnue.

    On ne renvoie ``False`` que si la valeur le dit explicitement, pour ne
    jamais écarter une vraie commande par excès de prudence.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"false", "non", "no", "0", "n"}:
        return False
    if s in {"true", "oui", "yes", "1", "o", "y"}:
        return True
    return default


def _iso_date_or_none(v: Any) -> str | None:
    """Normalise une date en ISO (YYYY-MM-DD) si reconnaissable, sinon None.

    Accepte déjà-ISO, JJ/MM/AAAA, JJ-MM-AAAA, JJ.MM.AAAA (année 2 ou 4 chiffres).
    On ne devine pas : si le format est inconnu, on renvoie None (l'UI laissera
    l'opérateur corriger).
    """
    s = _clean_str(v)
    if not s:
        return None
    # Déjà ISO ?
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # JJ/MM/AAAA ou variantes
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        try:
            di, mi, yi = int(d), int(mo), int(y)
            if 1 <= mi <= 12 and 1 <= di <= 31:
                return f"{yi:04d}-{mi:02d}-{di:02d}"
        except ValueError:
            return None
    return None


@dataclass
class LigneCommande:
    """Une ligne de commande : gamme/produit (libellé brut du PDF) + quantités.

    ``quantite`` = quantité commandée en unités (bouteilles, colonne "Qté Cdée").
    ``colis`` = nombre de colis/cartons (colonne "Colis"), si présent.
    """

    gamme: str
    quantite: float | None = None
    unite: str = ""
    colis: float | None = None
    taille: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> LigneCommande:
        if not isinstance(d, dict):
            gamme, taille = _extract_taille(_clean_str(d))
            return cls(gamme=gamme, taille=taille)
        raw_gamme = _clean_str(d.get("gamme") or d.get("produit") or d.get("libelle"))
        taille = _clean_str(d.get("taille"))
        # Si la taille n'est pas déjà séparée, on l'extrait du libellé et on la retire.
        if taille:
            gamme = raw_gamme
        else:
            gamme, taille = _extract_taille(raw_gamme)
        return cls(
            gamme=gamme,
            quantite=_as_float(d.get("quantite") if "quantite" in d else d.get("qty")),
            unite=_clean_str(d.get("unite") or d.get("unit")),
            colis=_as_float(d.get("colis") if "colis" in d else d.get("nb_colis")),
            taille=taille,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CommandeExtraite:
    """Résultat de l'extraction IA d'un PDF de commande magasin."""

    magasin: str = ""
    date_reception: str | None = None      # ISO YYYY-MM-DD
    date_livraison: str | None = None      # ISO YYYY-MM-DD
    lignes: list[LigneCommande] = field(default_factory=list)
    confiance: str = ""                    # 'haute' | 'moyenne' | 'basse'
    est_commande: bool = True              # le doc est-il bien une commande ?

    @classmethod
    def from_dict(cls, d: Any) -> CommandeExtraite:
        if not isinstance(d, dict):
            return cls()
        raw_lignes = d.get("lignes")
        lignes = (
            [LigneCommande.from_dict(x) for x in raw_lignes]
            if isinstance(raw_lignes, list)
            else []
        )
        # On ne garde que les lignes avec un libellé non vide.
        lignes = [li for li in lignes if li.gamme]
        confiance = _clean_str(d.get("confiance")).lower()
        if confiance not in _CONFIANCES:
            confiance = ""
        return cls(
            magasin=_clean_str(d.get("magasin")),
            date_reception=_iso_date_or_none(d.get("date_reception")),
            date_livraison=_iso_date_or_none(d.get("date_livraison")),
            lignes=lignes,
            confiance=confiance,
            est_commande=_as_bool(d.get("est_commande"), default=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "magasin": self.magasin,
            "date_reception": self.date_reception,
            "date_livraison": self.date_livraison,
            "lignes": [li.to_dict() for li in self.lignes],
            "confiance": self.confiance,
            "est_commande": self.est_commande,
        }

    @property
    def total_quantite(self) -> float:
        return sum(li.quantite or 0.0 for li in self.lignes)
