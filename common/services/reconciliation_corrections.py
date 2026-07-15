"""common/services/reconciliation_corrections.py
================================================
Couche de CORRECTIONS opérateur, appliquée PAR-DESSUS les snapshots de
réconciliation (qui, eux, sont recalculés à chaque synchro). L'opérateur peut :
  - éditer une ligne réconciliée (client, poids, coût, HT),
  - masquer une ligne (deleted),
  - forcer la validation d'une facture rejetée.
Tout est tracé (journal). Le registre factures reste immuable.

Couche domaine : accès DB via db.conn, pas de NiceGUI.
"""
from __future__ import annotations

import json
import logging

from db.conn import run_sql_with_tenant

_log = logging.getLogger("ferment.reconciliation_corrections")

# Champs éditables d'une ligne réconciliée (NULL en base = pas de surcharge).
_CHAMPS = ("client", "poids_eb", "poids_sofripa", "cout_transport", "montant_ht")


# ── Audit ─────────────────────────────────────────────────────────────────
def _audit(tenant_id, cible, action, details, by):
    run_sql_with_tenant(
        """INSERT INTO reconciliation_correction_audit
             (tenant_id, cible, action, details, changed_by)
           VALUES (:t, :c, :a, CAST(:d AS JSONB), :by)""",
        {"t": tenant_id, "c": cible, "a": action, "d": json.dumps(details), "by": by},
        tenant_id=tenant_id,
    )


# ── Corrections de LIGNE ──────────────────────────────────────────────────
def upsert_line_correction(tenant_id, order_number, *, by, reason=None, **champs):
    """Enregistre/actualise la correction d'une ligne (champs surchargés)."""
    vals = {k: champs.get(k) for k in _CHAMPS}
    run_sql_with_tenant(
        """
        INSERT INTO reconciliation_line_corrections
            (tenant_id, order_number, deleted, client, poids_eb, poids_sofripa,
             cout_transport, montant_ht, reason, updated_by, updated_at)
        VALUES (:t, :n, false, :client, :poids_eb, :poids_sofripa, :cout_transport,
                :montant_ht, :reason, :by, now())
        ON CONFLICT (tenant_id, order_number) DO UPDATE SET
            deleted=false, client=EXCLUDED.client, poids_eb=EXCLUDED.poids_eb,
            poids_sofripa=EXCLUDED.poids_sofripa, cout_transport=EXCLUDED.cout_transport,
            montant_ht=EXCLUDED.montant_ht, reason=EXCLUDED.reason,
            updated_by=EXCLUDED.updated_by, updated_at=now()
        """,
        {"t": tenant_id, "n": order_number, "reason": reason, "by": by, **vals},
        tenant_id=tenant_id,
    )
    _audit(tenant_id, f"ligne {order_number}", "EDIT",
           {k: v for k, v in vals.items() if v is not None} | {"reason": reason}, by)


def delete_line(tenant_id, order_number, *, by, reason=None):
    """Masque une ligne réconciliée (l'opérateur la juge fausse/en double)."""
    run_sql_with_tenant(
        """
        INSERT INTO reconciliation_line_corrections
            (tenant_id, order_number, deleted, reason, updated_by, updated_at)
        VALUES (:t, :n, true, :reason, :by, now())
        ON CONFLICT (tenant_id, order_number) DO UPDATE SET
            deleted=true, reason=EXCLUDED.reason, updated_by=EXCLUDED.updated_by,
            updated_at=now()
        """,
        {"t": tenant_id, "n": order_number, "reason": reason, "by": by},
        tenant_id=tenant_id,
    )
    _audit(tenant_id, f"ligne {order_number}", "DELETE", {"reason": reason}, by)


def reset_line(tenant_id, order_number, *, by):
    """Annule toute correction sur une ligne (retour à l'original)."""
    run_sql_with_tenant(
        "DELETE FROM reconciliation_line_corrections WHERE tenant_id=:t AND order_number=:n",
        {"t": tenant_id, "n": order_number}, tenant_id=tenant_id,
    )
    _audit(tenant_id, f"ligne {order_number}", "RESET", {}, by)


def list_line_corrections(tenant_id) -> dict[int, dict]:
    """Toutes les corrections de ligne du tenant, indexées par order_number."""
    rows = run_sql_with_tenant(
        """SELECT order_number, deleted, client, poids_eb, poids_sofripa,
                  cout_transport, montant_ht FROM reconciliation_line_corrections
           WHERE tenant_id=:t""",
        {"t": tenant_id}, tenant_id=tenant_id,
    )
    return {r["order_number"]: r for r in rows} if isinstance(rows, list) else {}


# ── Validation forcée d'une facture ───────────────────────────────────────
def force_validate_facture(tenant_id, id_facture_source, *, by, reason=None):
    run_sql_with_tenant(
        """
        INSERT INTO facture_status_overrides
            (tenant_id, id_facture_source, forced_status, reason, updated_by, updated_at)
        VALUES (:t, :f, 'OK', :reason, :by, now())
        ON CONFLICT (tenant_id, id_facture_source) DO UPDATE SET
            forced_status='OK', reason=EXCLUDED.reason,
            updated_by=EXCLUDED.updated_by, updated_at=now()
        """,
        {"t": tenant_id, "f": id_facture_source, "reason": reason, "by": by},
        tenant_id=tenant_id,
    )
    _audit(tenant_id, f"facture {id_facture_source}", "VALIDATE", {"reason": reason}, by)


def list_facture_overrides(tenant_id) -> dict[str, str]:
    rows = run_sql_with_tenant(
        "SELECT id_facture_source, forced_status FROM facture_status_overrides WHERE tenant_id=:t",
        {"t": tenant_id}, tenant_id=tenant_id,
    )
    return {r["id_facture_source"]: r["forced_status"] for r in rows} if isinstance(rows, list) else {}


# ── Application par-dessus un Resultat ─────────────────────────────────────
def appliquer_corrections(res, corrections: dict[int, dict]):
    """Applique les corrections aux lignes d'un Resultat (mutation in-place).

    Édite les champs surchargés, recalcule les dérivés (écart, €/kg…), retire
    les lignes masquées. Ne touche pas aux lignes sans correction.
    """
    if not corrections:
        return res
    gardees = []
    for L in res.lignes:
        c = corrections.get(L.numero)
        if not c:
            gardees.append(L)
            continue
        if c.get("deleted"):
            continue
        if c.get("client") is not None:
            L.client = c["client"]
        for f in ("poids_eb", "poids_sofripa", "cout_transport", "montant_ht"):
            if c.get(f) is not None:
                setattr(L, f, float(c[f]))
        # Recalcul des dérivés après édition.
        if L.poids_sofripa is not None and L.poids_eb is not None:
            L.ecart_kg = round(L.poids_sofripa - L.poids_eb, 2)
            L.ecart_pct = (L.ecart_kg / L.poids_eb) if L.poids_eb else None
        L.eur_par_kg = (
            (L.cout_transport / L.poids_sofripa)
            if (L.cout_transport and L.poids_sofripa) else None
        )
        L.transport_sur_ht = (
            (L.cout_transport / L.montant_ht)
            if (L.cout_transport and L.montant_ht) else None
        )
        gardees.append(L)
    res.lignes = gardees
    return res
