"""
core/commandes/
================
Cœur métier « commandes magasins » - sans UI, testable.

Pipeline : PDF de commande (Franprix, Bio c'Bon, Coop…) reçu par email
→ texte (``extract``) → extraction structurée par Claude (``ai_parse``)
→ ``CommandeExtraite`` {magasin, dates, lignes [gamme, quantité, unité]}.

Aucune dépendance NiceGUI / pages : ce module est appelé par
``common/services/commande_service.py`` (orchestration + DB).
"""
