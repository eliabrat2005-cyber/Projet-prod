"""
core/commandes/ai_parse.py
==========================
Extraction structuree d'une commande magasin via Gemini (Google).

Entree : texte brut d'un PDF de commande (sortie de ``extract.extract_text``).
Sortie : ``CommandeExtraite`` {magasin, date_reception, date_livraison,
lignes [gamme, quantite, unite]}.

On utilise la sortie JSON structuree de Gemini (``response_schema``), donc la
forme du resultat est garantie par le schema. Aucune regle par enseigne :
le modele gere la variete des formats (BIODIS, Franprix, Bio c'Bon, Coop...).

Cle API : ``GEMINI_API_KEY`` (ou ``GOOGLE_API_KEY``) dans le ``.env``.
A creer gratuitement sur https://aistudio.google.com/apikey
"""
from __future__ import annotations

import json
import logging
import os

from .models import CommandeExtraite

_log = logging.getLogger("ferment.commandes.ai_parse")

# Flash : rapide, dispo en palier gratuit, suffisant pour de l'extraction.
# (2.5-flash a du quota gratuit sur le projet courant, 2.0-flash non.)
_DEFAULT_MODEL = "gemini-2.5-flash"


class AiNotConfigured(RuntimeError):
    """GEMINI_API_KEY absente : l'extraction IA est indisponible."""


def _get_api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")


def is_ai_configured() -> bool:
    """True si Gemini est utilisable (cle API presente)."""
    return bool(_get_api_key())


def _model_name() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or _DEFAULT_MODEL


_SYSTEM_PROMPT = """\
Tu es l'assistant de saisie des commandes de Ferment Station (production de \
kefir et boissons fermentees). On te donne le texte brut d'un PDF de commande \
envoye par un magasin ou un distributeur client (BIODIS, Franprix, Bio c'Bon, \
Cooperative...).

Ta mission : determiner si le document est une commande, et si oui en extraire \
les informations, au format JSON demande. Regles :

0. est_commande : indique si l'ensemble (mail + PDF) est un BON DE COMMANDE \
   (un client nous commande des produits A LIVRER). Indices en faveur : \
   mention "commande"/"bon de commande", date de LIVRAISON future, lignes \
   produit avec quantites a livrer. \
   ATTENTION, distingue bien d'une FACTURE (= une note A PAYER pour des \
   produits deja fournis) : indices d'une facture -> "facture", "montant a \
   payer", "net a payer", "total TTC", "TVA", "echeance de paiement", \
   numero de facture, conditions de reglement. Une facture, un devis, un \
   avoir, une publicite, un releve => est_commande=false. \
   IMPORTANT : MEME si est_commande=false, tu DOIS quand meme remplir "lignes" \
   s'il y a des produits avec quantites (une facture a des lignes produit) : \
   on s'en sert pour verification. Ne remplis lignes vide QUE s'il n'y a \
   vraiment aucun produit/quantite lisible.

1. magasin : le nom de l'enseigne / du distributeur qui PASSE la commande \
   (le client), pas Ferment Station / Symbiose. Si plusieurs noms, prends \
   l'emetteur de la commande.
2. date_reception : la date de la commande / d'emission du document \
   (souvent "Date commande", "Date", "Le ..."). Format JJ/MM/AAAA tel qu'ecrit.
3. date_livraison : la date de livraison souhaitee ("Date Livraison", \
   "Date Livraison Imperative", "Livraison le", "A livrer le"). Format \
   JJ/MM/AAAA tel qu'ecrit. Si absente, laisse vide.
4. lignes : une entree par produit REELLEMENT commande. gamme = le \
   libelle/designation du produit EXACTEMENT comme ecrit sur le PDF (ne \
   traduis pas, ne normalise pas, n'ajoute pas le code interne). \
   quantite = la quantite commandee en unites (colonne "Qte Cdee", \
   "Quantite", "Qte"...), le nombre d'unites/bouteilles effectivement \
   commande, PAS un prix. unite = l'unite de cette quantite si precisee \
   ("U", "Kg", bouteilles...), sinon vide. colis = le nombre de colis/cartons \
   (colonne "Colis") si present, sinon laisse vide. Ne confonds pas quantite \
   (unites) et colis (cartons) : ce sont deux colonnes distinctes.
5. N'invente JAMAIS de produit, de quantite ou de date absente du document. \
   En cas de doute sur une valeur, laisse le champ vide plutot que deviner.
6. confiance : auto-evalue la fiabilite globale de ton extraction. Valeurs \
   possibles : "haute" (document clair, tout net), "moyenne" (quelques \
   ambiguites), "basse" (document confus / partiel).

## Pieges frequents (commandes de distributeurs type BIODIS, Franprix...)

- Le document est une commande ADRESSEE a Ferment Station / Symbiose (le \
  fournisseur). Le magasin est l'AUTRE societe : celle qui commande \
  (en-tete, bloc societe emetteur, ex. "BIODIS"). Ce n'est JAMAIS \
  "SYMBIOSE" ni "FERMENT STATION".
- "Date commande" donne date_reception ; "Date Livraison" / "Date Livraison \
  Imperative" donne date_livraison.
- IGNORE completement les lignes/blocs qui ne sont PAS un produit commande : \
  conditions de process/livraison ("PROCESS ... A RESPECTER"), mentions \
  "Promotion en date de livraison...", lignes "Eco-contribution", \
  "Sous-total", "Total", "PORT SUR ACHAT", TVA, TPF, "Net a Payer", \
  conditions de reglement, adresses, SIRET/RCS, en-tetes/pieds de page \
  repetes sur chaque page.
- Une meme ligne produit peut s'etaler sur 2 lignes de texte (designation + \
  ref./prix d'un cote, EAN/remises de l'autre) : regroupe-les en UNE seule \
  entree de lignes.
- Conserve le format/contenance dans le libelle (ex. "..., 33cl", "..., 75cl") \
  car c'est ce qui distingue les gammes.
"""

# Schema de sortie structuree (sous-ensemble OpenAPI accepte par Gemini).
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "est_commande": {"type": "boolean"},
        "magasin": {"type": "string"},
        "date_reception": {"type": "string"},
        "date_livraison": {"type": "string"},
        "confiance": {"type": "string", "enum": ["haute", "moyenne", "basse"]},
        "lignes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gamme": {"type": "string"},
                    "quantite": {"type": "number"},
                    "unite": {"type": "string"},
                    "colis": {"type": "number"},
                },
                "required": ["gamme"],
            },
        },
    },
    "required": ["est_commande", "magasin", "lignes", "confiance"],
}


def parse_commande(raw_text: str, *, email_context: str = "") -> CommandeExtraite:
    """Extrait une ``CommandeExtraite`` depuis le texte brut d'un PDF.

    Args:
        raw_text: texte extrait du PDF (``extract.extract_text``).
        email_context: texte du mail (sujet + corps) qui accompagne le PDF, le
            cas échéant. Sert d'indice supplémentaire au portier ``est_commande``
            (un mail qui parle de commande renforce la décision).

    Returns:
        CommandeExtraite (champs vides si le modele n'a rien trouve).

    Raises:
        AiNotConfigured: si GEMINI_API_KEY (ou GOOGLE_API_KEY) n'est pas definie.
    """
    api_key = _get_api_key()
    if not api_key:
        raise AiNotConfigured("GEMINI_API_KEY non configuree")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    ctx = ""
    if email_context.strip():
        ctx = (
            "Texte du mail qui accompagne le PDF (sujet + corps). Un mail qui "
            "parle de commande / bon de commande est un indice fort :\n\n"
            "```\n"
            f"{email_context.strip()[:4000]}\n"
            "```\n\n"
        )
    user_prompt = (
        ctx
        + "Voici le texte brut du PDF a analyser :\n\n"
        "```\n"
        f"{raw_text}\n"
        "```\n\n"
        "Determine d'abord si c'est une commande (est_commande) en combinant "
        "les indices du mail ET du PDF, puis, si oui, extrais-la au format "
        "JSON demande."
    )

    response = client.models.generate_content(
        model=_model_name(),
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0,
        ),
    )

    text = (response.text or "").strip()
    usage = getattr(response, "usage_metadata", None)
    _log.info(
        "Extraction commande (Gemini %s) : in=%s out=%s",
        _model_name(),
        getattr(usage, "prompt_token_count", "?"),
        getattr(usage, "candidates_token_count", "?"),
    )

    if not text:
        _log.warning("Gemini a renvoye une reponse vide -> commande vide.")
        return CommandeExtraite()

    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        _log.error("Reponse Gemini non-JSON (%s) : %.300s", exc, text)
        return CommandeExtraite()

    return CommandeExtraite.from_dict(data)
