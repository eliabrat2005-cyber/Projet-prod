"""
pages/tresorerie.py
===================
Page Trésorerie — vue CASH SORTANT : encours fournisseurs (Pennylane).

⚠️ Périmètre limité par le token Pennylane actuel : seules les factures
fournisseurs sont accessibles (customer_invoices et bank_accounts → 403). Cette
page ne montre donc que les paiements à sortir. La trésorerie complète
(créances clients, solde bancaire, prévision entrées−sorties) viendra avec un
token Pennylane élargi.

⚠️ Le « reste à régler » vient de remaining_amount_with_tax côté Pennylane : il
reflète le RAPPROCHEMENT COMPTABLE, pas forcément le règlement réel. Une facture
payée mais pas encore rapprochée en banque y figure encore. À interpréter comme
un encours « à régler / à rapprocher », à confirmer avec la compta.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from nicegui import ui

from pages.auth import require_auth
from pages.theme import (
    COLORS,
    error_banner,
    kpi_card,
    page_layout,
    page_loading,
    section_title,
)

_log = logging.getLogger("ferment.tresorerie")


def _eur(v) -> str:
    return f"{v:,.0f} €".replace(",", " ") if v is not None else "—"


@ui.page("/tresorerie")
def page_tresorerie():
    user = require_auth()
    if not user:
        return

    ui.add_head_html(
        "<style>.reconcil-kpis .kpi-card{min-width:215px}"
        ".reconcil-kpis .kpi-card .text-h6{white-space:nowrap}</style>"
    )

    with page_layout("Trésorerie", "account_balance", "/tresorerie"):
        ui.label(
            "Encours fournisseurs (cash à sortir), d'après Pennylane."
        ).classes("text-body2").style(f"color: {COLORS['ink2']}")

        # ── Avertissements de périmètre / fiabilité ──────────────────────
        with ui.card().classes("w-full").props("flat bordered").style(
            f"border-color: {COLORS['warning']}55; background: {COLORS['warning']}0D"
        ):
            with ui.card_section().classes("row items-start gap-3 q-pa-sm"):
                ui.icon("info", size="sm").style(f"color: {COLORS['warning']}")
                ui.label(
                    "Vue partielle : seul le cash SORTANT (fournisseurs) est "
                    "disponible — les créances clients et le solde bancaire "
                    "nécessitent un token Pennylane élargi. Le « reste à régler » "
                    "reflète le rapprochement comptable Pennylane (une facture déjà "
                    "payée mais non rapprochée y figure encore) : à confirmer avec la compta."
                ).classes("text-caption").style(f"color: {COLORS['ink2']}")

        results = ui.column().classes("w-full gap-4")

        async def _run():
            results.clear()
            try:
                async with page_loading("Récupération des factures fournisseurs…"):
                    from core.treasury.payables import (
                        lire_dettes_fournisseurs,
                        par_fournisseur,
                        prioriser,
                        synthese_dettes,
                    )
                    today = date.today().isoformat()

                    def _work():
                        dettes = lire_dettes_fournisseurs()
                        return (
                            dettes,
                            synthese_dettes(dettes, today),
                            par_fournisseur(dettes, today),
                            prioriser(dettes, today),
                        )

                    dettes, syn, groupes, prio = await asyncio.to_thread(_work)
            except Exception as exc:  # noqa: BLE001
                _log.exception("Trésorerie : chargement échoué")
                with results:
                    error_banner(f"Échec du chargement : {exc}")
                return

            _render(dettes, syn, groupes, prio, today)

        def _detail_table(items, today, *, red_amount=False):
            """Tableau d'échéancier (échéance · fournisseur · n° · reste · statut)."""
            cols = [
                {"name": "deadline", "label": "Échéance", "field": "deadline",
                 "align": "left", "sortable": True},
                {"name": "supplier", "label": "Fournisseur", "field": "supplier",
                 "align": "left", "sortable": True},
                {"name": "num", "label": "N° facture", "field": "num", "align": "left"},
                {"name": "remaining", "label": "Reste à régler", "field": "remaining",
                 "align": "right", "sortable": True},
                {"name": "statut", "label": "Statut", "field": "statut", "align": "left"},
            ]
            rows = [
                {
                    "deadline": d["deadline"] or "—",
                    "supplier": d["supplier"],
                    "num": d["invoice_number"] or "—",
                    "remaining": _eur(d["remaining"]),
                    "_amount": d["remaining"],
                    "statut": "Échu" if (d["deadline"] or "9999") < today else "À venir",
                }
                for d in items
            ]
            tbl = ui.table(
                columns=cols, rows=rows, row_key="num",
                pagination={"rowsPerPage": 25},
            ).classes("w-full").props(
                'flat bordered dense :rows-per-page-options="[25,50,100]"'
            )
            tbl.add_slot("body-cell-statut", r"""
                <q-td :props="props">
                  <q-badge :color="props.value === 'Échu' ? 'red-6' : 'orange-7'"
                           :label="props.value" />
                </q-td>
            """)
            if red_amount:
                tbl.add_slot("body-cell-remaining", r"""
                    <q-td :props="props" class="text-right">
                      <span style="color:#DC2626;font-weight:700">{{ props.value }}</span>
                    </q-td>
                """)
            return tbl

        def _render(dettes, syn, groupes, prio, today):
            grosses_urgentes, grosses_non_urg, reste = prio
            with results:
                # ── Synthèse (KPIs) ──────────────────────────────────────
                section_title("Encours fournisseurs", "payments")
                with ui.row().classes("w-full gap-3 wrap reconcil-kpis"):
                    kpi_card("account_balance_wallet", "Reste à régler (total)",
                             _eur(syn["total"]))
                    kpi_card("warning", "Échu (deadline passée)",
                             _eur(syn["retard_total"]), COLORS["error"])
                    kpi_card("event", "À régler sous 30 j",
                             _eur(syn["sous30_total"]), COLORS["orange"])
                    kpi_card("groups", "Fournisseurs concernés",
                             str(syn["nb_fournisseurs"]), COLORS["blue"])

                if not dettes:
                    ui.label("Aucun encours fournisseur.").classes(
                        "text-body2 q-mt-md"
                    ).style(f"color: {COLORS['ink2']}")
                    return

                # ── ① PRIORITÉ : grosses dettes urgentes (rouge léger + ⚠️) ──
                if grosses_urgentes:
                    montant = sum(d["remaining"] for d in grosses_urgentes)
                    with ui.card().classes("w-full q-mt-sm").props("flat bordered").style(
                        "border:1px solid #FCA5A5; background:#FEF2F2; border-radius:10px"
                    ):
                        with ui.card_section().classes("q-pa-md column gap-2"):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("warning", size="sm").style("color:#DC2626")
                                ui.label(
                                    f"À régler en priorité — grosses dettes urgentes "
                                    f"({len(grosses_urgentes)}) · {_eur(montant)}"
                                ).classes("text-subtitle1").style(
                                    "color:#991B1B;font-weight:700"
                                )
                            ui.label(
                                "Gros montants dont l'échéance est passée ou proche "
                                "(≤ 30 j) — à traiter en premier."
                            ).classes("text-caption").style("color:#B91C1C")
                            _detail_table(grosses_urgentes, today, red_amount=True)

                # ── ② Grosses dettes à anticiper (rouge, non urgentes) ───
                if grosses_non_urg:
                    montant = sum(d["remaining"] for d in grosses_non_urg)
                    with ui.row().classes("items-center gap-2 q-mt-md"):
                        ui.icon("priority_high", size="sm").style("color:#DC2626")
                        ui.label(
                            f"Grosses dettes à anticiper ({len(grosses_non_urg)}) "
                            f"· {_eur(montant)}"
                        ).classes("text-subtitle1").style("color:#DC2626;font-weight:600")
                    ui.label(
                        "Montants importants pas encore urgents — à provisionner."
                    ).classes("text-caption q-mb-xs").style(f"color: {COLORS['ink2']}")
                    _detail_table(grosses_non_urg, today, red_amount=True)

                # ── ③ Le reste ───────────────────────────────────────────
                if reste:
                    section_title(f"Autres encours ({len(reste)})", "receipt_long")
                    _detail_table(reste, today)

                # ── Synthèse par fournisseur (tous encours) ──────────────
                section_title(f"Par fournisseur ({len(groupes)})", "store")
                g_cols = [
                    {"name": "f", "label": "Fournisseur", "field": "f", "align": "left",
                     "sortable": True},
                    {"name": "nb", "label": "Factures", "field": "nb", "align": "right",
                     "sortable": True},
                    {"name": "total", "label": "Reste à régler", "field": "total",
                     "align": "right", "sortable": True},
                    {"name": "retard", "label": "dont échu", "field": "retard",
                     "align": "right", "sortable": True},
                ]
                g_rows = [
                    {"f": g["fournisseur"], "nb": g["nb"],
                     "total": _eur(g["total"]), "retard": _eur(g["retard"])}
                    for g in groupes
                ]
                ui.table(
                    columns=g_cols, rows=g_rows, row_key="f",
                    pagination={"rowsPerPage": 25},
                ).classes("w-full").props(
                    'flat bordered dense :rows-per-page-options="[25,50,100]"'
                )

        ui.timer(0.2, _run, once=True)  # lancement auto à l'arrivée
