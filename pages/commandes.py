"""
pages/commandes.py
==================
Pages « Commandes reçues ».

- ``/commandes`` : commandes ACTIVES (Nouveau + À produire) + dépôt manuel.
  Petits boutons vers « À vérifier » et « Commandes passées ».
- ``/commandes/a-verifier`` : documents que l'IA n'a pas reconnus comme commande
  (factures probables…) mais qui ont des produits — à contrôler / promouvoir.
- ``/commandes/passees`` : commandes Envoyée (archivées).
- ``/commandes/{id}`` : la commande en grand (lecture seule) + transitions d'état.

Flux d'une commande : Nouveau -> À produire -> Envoyée.
Logique métier dans ``common/services/commande_service.py`` + ``core/commandes/``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

from nicegui import ui

from core.commandes.ai_parse import is_ai_configured
from core.commandes.models import LigneCommande
from pages.auth import require_auth
from pages.theme import (
    COLORS,
    confirm_dialog,
    error_banner,
    kpi_card,
    page_layout,
    section_title,
)

_log = logging.getLogger("ferment.commandes")

_STATUT_LABEL = {
    "nouveau": "Nouveau", "a_produire": "À produire", "envoyee": "Envoyée",
    "a_verifier": "À vérifier", "erreur": "À vérifier",
}
_STATUT_COLOR = {
    "nouveau": "blue-6", "a_produire": "orange-7", "envoyee": "green-7",
    "a_verifier": "grey-6", "erreur": "red-6",
}
_ACTIFS = ("nouveau", "a_produire")


def _fmt_date(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "-"


def _fmt_qte(v) -> str:
    return "-" if v is None else f"{v:g}"


def _fmt_qte_unite(li: LigneCommande) -> str:
    if li.quantite is None:
        return "-"
    q = f"{li.quantite:g}"
    return f"{q} {li.unite}".strip() if li.unite else q


def _date_state(d) -> str:
    """État livraison : 'past' (dépassée), 'soon' (<=1j), 'orange' (2-3j),
    'green' (>=4j), '' (inconnue)."""
    if not d:
        return ""
    try:
        n = (d - date.today()).days
    except Exception:  # noqa: BLE001
        return ""
    if n < 0:
        return "past"
    if n <= 1:
        return "soon"
    if n <= 3:
        return "orange"
    return "green"


_DATE_HEX = {
    "past": COLORS["error"], "soon": COLORS["error"],
    "orange": COLORS["orange"], "green": COLORS["success"],
}


def _date_hex(d) -> str:
    return _DATE_HEX.get(_date_state(d), COLORS["ink2"])


async def _maybe_await(cb) -> None:
    res = cb()
    if asyncio.iscoroutine(res):
        await res


def _livraison_card(detail) -> None:
    """Carte 'Livraison' de la page détail : barré si passée, ⚠ si imminent,
    neutre si la commande est déjà envoyée."""
    state = "" if detail.statut == "envoyee" else _date_state(detail.date_livraison)
    color = _DATE_HEX.get(state, COLORS["ink2"])
    deco = "line-through" if state == "past" else "none"
    with ui.card().classes("kpi-card q-pa-none flex-1").props("flat"):
        with ui.card_section().classes("row items-center gap-3 q-pa-md"):
            with ui.element("div").classes("q-pa-xs").style(
                f"background: {color}1A; border-radius: 6px"
            ):
                ui.icon("local_shipping", size="sm").style(f"color: {color}")
            with ui.column().classes("gap-0"):
                ui.label("Livraison").classes("text-caption").style(
                    f"color: {COLORS['ink2']}; font-weight: 500")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    if state == "soon":
                        ui.icon("warning", size="xs").style(f"color: {color}")
                    ui.label(_fmt_date(detail.date_livraison)).classes("text-h6").style(
                        f"color: {color}; font-weight: 600; text-decoration: {deco}")


# ─── Helpers partagés (PDF, suppression, statut, édition) ────────────────────

async def _download_pdf(tenant_id: str, commande_id: str) -> None:
    from common.services.commande_service import get_pdf

    res = await asyncio.to_thread(get_pdf, tenant_id, commande_id)
    if res is None:
        ui.notify("Aucun fichier source archivé pour cette commande.", type="warning")
        return
    data, fname = res
    ui.download.content(data, fname)


def _confirm_delete(tenant_id: str, commande_id: str, on_deleted) -> None:
    dlg, _msg, action = confirm_dialog(
        "Supprimer cette commande ?", "Cette action est définitive.",
        "Supprimer", action_icon="delete", danger=True,
    )

    async def _go():
        from common.services.commande_service import delete_commande

        dlg.close()
        await asyncio.to_thread(delete_commande, tenant_id, commande_id)
        ui.notify("Commande supprimée.", type="info")
        await _maybe_await(on_deleted)

    action.on_click(_go)
    dlg.open()


async def _set_statut(tenant_id: str, commande_id: str, statut: str, *, msg: str, goto) -> None:
    from common.services.commande_service import set_statut

    await asyncio.to_thread(set_statut, tenant_id, commande_id, statut)
    ui.notify(msg, type="positive")
    await _maybe_await(goto)


def _open_edit_dialog(tenant_id, detail, *, on_saved, on_deleted) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-lg").style(
        "min-width: 620px; max-width: 92vw"
    ):
        ui.label("Modifier la commande").classes("text-h6").style(
            f"color: {COLORS['ink']}; font-weight: 600"
        )
        magasin_in = ui.input("Magasin", value=detail.magasin).props(
            "outlined dense"
        ).classes("w-full")
        with ui.row().classes("w-full gap-3"):
            drec_in = ui.input(
                "Date réception",
                value=detail.date_reception.isoformat() if detail.date_reception else "",
            ).props("outlined dense").classes("flex-1")
            dliv_in = ui.input(
                "Date livraison",
                value=detail.date_livraison.isoformat() if detail.date_livraison else "",
            ).props("outlined dense").classes("flex-1")
        ui.label("Format date : AAAA-MM-JJ").classes("text-caption").style(
            f"color: {COLORS['ink2']}"
        )

        section_title("Lignes (gamme · taille · qté · unité · colis)", "list")
        lignes_box = ui.column().classes("w-full gap-1")
        line_inputs: list[tuple] = []

        def _add_line_row(li: LigneCommande | None = None):
            li = li or LigneCommande(gamme="")
            with lignes_box:
                with ui.row().classes("w-full items-center gap-2 no-wrap") as row:
                    g = ui.input(value=li.gamme, placeholder="Gamme / produit").props(
                        "outlined dense"
                    ).classes("flex-1")
                    t = ui.input(value=li.taille, placeholder="Taille").props(
                        "outlined dense"
                    ).style("width: 75px")
                    q = ui.input(
                        value=_fmt_qte(li.quantite) if li.quantite is not None else "",
                        placeholder="Qté",
                    ).props("outlined dense").style("width: 75px")
                    u = ui.input(value=li.unite, placeholder="Unité").props(
                        "outlined dense"
                    ).style("width: 85px")
                    c = ui.input(
                        value=_fmt_qte(li.colis) if li.colis is not None else "",
                        placeholder="Colis",
                    ).props("outlined dense").style("width: 75px")
                    entry = (g, t, q, u, c, row)

                    def _remove(_=None, e=entry):
                        e[5].delete()
                        if e in line_inputs:
                            line_inputs.remove(e)

                    ui.button(icon="close", on_click=_remove).props(
                        "flat round dense color=grey-6"
                    )
                    line_inputs.append(entry)

        for li in detail.lignes:
            _add_line_row(li)
        if not detail.lignes:
            _add_line_row()

        ui.button("Ajouter une ligne", icon="add",
                  on_click=lambda: _add_line_row()).props(
            "flat dense color=green-8"
        ).classes("q-mt-xs")

        async def _save():
            from common.services.commande_service import update_commande
            from core.commandes.models import _as_float

            lignes = []
            for g, t, q, u, c, _row in line_inputs:
                gamme = (g.value or "").strip()
                if not gamme:
                    continue
                lignes.append(LigneCommande(
                    gamme=gamme, taille=(t.value or "").strip(),
                    quantite=_as_float(q.value), unite=(u.value or "").strip(),
                    colis=_as_float(c.value),
                ))
            try:
                await asyncio.to_thread(
                    update_commande, tenant_id, detail.id,
                    magasin=(magasin_in.value or "").strip(),
                    date_reception=(drec_in.value or "").strip(),
                    date_livraison=(dliv_in.value or "").strip(),
                    lignes=lignes,
                )  # NB : on ne touche PAS au statut ici (transitions par boutons)
            except Exception as exc:  # noqa: BLE001
                _log.exception("Sauvegarde commande échouée")
                ui.notify(f"Erreur : {exc}", type="negative")
                return
            dlg.close()
            ui.notify("Modifications enregistrées.", type="positive")
            await _maybe_await(on_saved)

        with ui.row().classes("w-full justify-between items-center q-mt-md"):
            ui.button("Supprimer", icon="delete_outline",
                      on_click=lambda: (dlg.close(), _confirm_delete(tenant_id, detail.id, on_deleted))
                      ).props("flat color=red-7")
            with ui.row().classes("gap-2"):
                ui.button("Annuler", on_click=dlg.close).props("flat color=grey-7")
                ui.button("Enregistrer", icon="save", on_click=_save).props(
                    "unelevated color=green-8"
                )
    dlg.open()


def _open_row_menu(tenant_id: str, row: dict, on_change) -> None:
    cid = row.get("id")
    if not cid:
        return

    async def _mod():
        from common.services.commande_service import get_commande

        menu.close()
        detail = await asyncio.to_thread(get_commande, tenant_id, cid)
        if detail is None:
            ui.notify("Commande introuvable.", type="negative")
            return
        _open_edit_dialog(tenant_id, detail, on_saved=on_change, on_deleted=on_change)

    with ui.dialog() as menu, ui.card().classes("q-pa-md").style("min-width: 240px"):
        ui.label(row.get("magasin") or "Commande").classes("text-subtitle1").style(
            f"color: {COLORS['ink']}; font-weight: 600"
        )
        ui.button("Ouvrir en grand", icon="open_in_full",
                  on_click=lambda: ui.navigate.to(f"/commandes/{cid}")).props(
            "flat color=grey-8 align=left").classes("w-full")
        ui.button("Modifier", icon="edit", on_click=_mod).props(
            "flat color=green-8 align=left").classes("w-full")
        ui.button("Supprimer", icon="delete_outline",
                  on_click=lambda: (menu.close(), _confirm_delete(tenant_id, cid, on_change))
                  ).props("flat color=red-7 align=left").classes("w-full")
        ui.button("Annuler", on_click=menu.close).props(
            "flat color=grey-7 align=left").classes("w-full")
    menu.open()


def _render_table(list_box, summaries, tenant_id, on_change) -> None:
    """Construit le tableau de commandes (clic ligne -> détail, menu ⋮)."""
    list_box.clear()
    if not summaries:
        with list_box:
            ui.label("Aucune commande ici pour l'instant.").classes(
                "text-body2 q-mt-md").style(f"color: {COLORS['ink2']}")
        return
    cols = [
        {"name": "date", "label": "Reçu le", "field": "date", "align": "left", "sortable": True},
        {"name": "magasin", "label": "Magasin", "field": "magasin", "align": "left", "sortable": True},
        {"name": "livraison", "label": "Livraison", "field": "livraison", "align": "left", "sortable": True},
        {"name": "nb", "label": "Lignes", "field": "nb", "align": "right"},
        {"name": "qte", "label": "Qté totale (U)", "field": "qte", "align": "right"},
        {"name": "colis", "label": "Nb colis", "field": "colis", "align": "right"},
        {"name": "statut", "label": "Statut", "field": "statut", "align": "center"},
        {"name": "actions", "label": "", "field": "actions", "align": "center"},
    ]
    rows = []
    for s in summaries:
        rows.append({
            "id": s.id,
            "date": _fmt_date(s.date_reception) if s.date_reception else (
                s.created_at.strftime("%d/%m/%Y") if s.created_at else "-"),
            "magasin": s.magasin or "(à renseigner)",
            "livraison": _fmt_date(s.date_livraison),
            # Pas de feu tricolore / barré pour une commande déjà envoyée.
            "liv_state": "" if s.statut == "envoyee" else _date_state(s.date_livraison),
            "nb": s.nb_lignes,
            "qte": _fmt_qte(s.total_quantite) if s.total_quantite else "-",
            "colis": _fmt_qte(s.total_colis) if s.total_colis else "-",
            "statut": s.statut,
        })
    with list_box:
        tbl = ui.table(columns=cols, rows=rows, row_key="id",
                       pagination={"rowsPerPage": 50}).classes("w-full").props(
            'flat bordered :rows-per-page-options="[50,100]"')
        tbl.add_slot("body-cell-livraison", r"""
            <q-td :props="props">
              <span v-if="props.row.liv_state==='past'"
                    style="color:#EF4444;text-decoration:line-through">{{ props.value }}</span>
              <span v-else-if="props.row.liv_state==='soon'" style="color:#EF4444;font-weight:700">
                <q-icon name="warning" size="14px" class="q-mr-xs"/>{{ props.value }}
              </span>
              <span v-else-if="props.row.liv_state==='orange'" style="color:#F59E0B;font-weight:600">{{ props.value }}</span>
              <span v-else-if="props.row.liv_state==='green'" style="color:#16A34A;font-weight:500">{{ props.value }}</span>
              <span v-else>{{ props.value }}</span>
            </q-td>
        """)
        tbl.add_slot("body-cell-statut", r"""
            <q-td :props="props" class="text-center">
              <q-badge :color="{nouveau:'blue-6',a_produire:'orange-7',envoyee:'green-7',a_verifier:'grey-6',erreur:'red-6'}[props.value] || 'grey-5'"
                       :label="{nouveau:'Nouveau',a_produire:'À produire',envoyee:'Envoyée',a_verifier:'À vérifier',erreur:'À vérifier'}[props.value] || props.value" />
            </q-td>
        """)
        tbl.add_slot("body-cell-actions", r"""
            <q-td :props="props" class="text-center">
              <q-btn flat round dense color="grey-7" icon="more_vert"
                     @click.stop="() => $parent.$emit('rowmenu', props.row)">
                <q-tooltip>Modifier / supprimer</q-tooltip>
              </q-btn>
            </q-td>
        """)
        tbl.on("rowClick", lambda e: ui.navigate.to(
            f"/commandes/{(e.args[1] if isinstance(e.args, list) and len(e.args) > 1 else {}).get('id')}"
        ) if isinstance(e.args, list) and len(e.args) > 1 and e.args[1].get("id") else None)
        tbl.on("rowmenu", lambda e: _open_row_menu(tenant_id, e.args, on_change))


# ─── Page détail en grand : /commandes/{id} ─────────────────────────────────

def _render_commande_big(tenant_id, detail) -> None:
    total_q = sum(li.quantite or 0.0 for li in detail.lignes)
    total_c = sum(li.colis or 0.0 for li in detail.lignes)

    with ui.row().classes("w-full items-center justify-between q-mb-xs"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("store", size="md").style(f"color: {COLORS['green']}")
            ui.label(detail.magasin or "(magasin à renseigner)").classes(
                "text-h5").style(f"color: {COLORS['ink']}; font-weight: 600")
            ui.badge(_STATUT_LABEL.get(detail.statut, detail.statut)).props(
                f"color={_STATUT_COLOR.get(detail.statut, 'grey-5')}")
        with ui.row().classes("gap-2 items-center"):
            if detail.pdf_filename:
                ui.button("Fichier source", icon="description",
                          on_click=lambda: _download_pdf(tenant_id, detail.id)).props(
                    "flat color=grey-7")
            ui.button("Modifier", icon="edit",
                      on_click=lambda: _open_edit_dialog(
                          tenant_id, detail,
                          on_saved=lambda: ui.navigate.to(f"/commandes/{detail.id}"),
                          on_deleted=lambda: ui.navigate.to("/commandes"),
                      )).props("flat color=green-8")

    src = "email" if detail.source == "email" else "dépôt manuel"
    ui.label(f"Reçu par {src}").classes("text-body2 q-mb-sm").style(
        f"color: {COLORS['ink2']}")

    if detail.statut in ("a_verifier", "erreur") and detail.parse_error:
        error_banner(
            f"{detail.parse_error}  —  vérifie puis confirme si c'est bien une commande.",
            dismissible=False,
        )

    with ui.row().classes("w-full gap-3 wrap q-mb-md"):
        kpi_card("event", "Réception", _fmt_date(detail.date_reception))
        _livraison_card(detail)
        kpi_card("liquor", "Total bouteilles", f"{total_q:g}", COLORS["blue"])
        kpi_card("inventory_2", "Total colis", f"{total_c:g}")

    # ── Barre de transitions d'état ───────────────────────────────────────
    with ui.row().classes("w-full items-center gap-2 q-mb-md"):
        st = detail.statut
        if st == "a_verifier" or st == "erreur":
            ui.button("✓ C'est une commande", icon="check_circle",
                      on_click=lambda: _set_statut(
                          tenant_id, detail.id, "nouveau",
                          msg="Validée : ajoutée aux commandes.",
                          goto=lambda: ui.navigate.to(f"/commandes/{detail.id}"))
                      ).props("unelevated color=green-8")
        if st == "nouveau":
            ui.button("Marquer « À produire »", icon="trending_flat",
                      on_click=lambda: _set_statut(
                          tenant_id, detail.id, "a_produire",
                          msg="Commande marquée « À produire ».",
                          goto=lambda: ui.navigate.to(f"/commandes/{detail.id}"))
                      ).props("unelevated color=orange-8")
        if st == "a_produire":
            ui.button("Marquer « Envoyée »", icon="local_shipping",
                      on_click=lambda: _set_statut(
                          tenant_id, detail.id, "envoyee",
                          msg="Commande envoyée : déplacée dans les commandes passées.",
                          goto=lambda: ui.navigate.to("/commandes"))
                      ).props("unelevated color=green-8")
        if st == "envoyee":
            ui.button("Rouvrir (À produire)", icon="undo",
                      on_click=lambda: _set_statut(
                          tenant_id, detail.id, "a_produire",
                          msg="Commande rouverte.",
                          goto=lambda: ui.navigate.to(f"/commandes/{detail.id}"))
                      ).props("flat color=grey-7")
        ui.button("Supprimer", icon="delete_outline",
                  on_click=lambda: _confirm_delete(
                      tenant_id, detail.id, lambda: ui.navigate.to("/commandes"))
                  ).props("flat color=red-7")

    section_title(f"Produits commandés ({len(detail.lignes)})", "list_alt")
    v_cols = [
        {"name": "gamme", "label": "Gamme", "field": "gamme", "align": "left", "sortable": True},
        {"name": "taille", "label": "Taille", "field": "taille", "align": "center"},
        {"name": "qte", "label": "Quantité", "field": "qte", "align": "right", "sortable": True},
        {"name": "colis", "label": "Nb colis", "field": "colis", "align": "right", "sortable": True},
    ]
    v_rows = [{
        "i": i, "gamme": li.gamme, "taille": li.taille or "-",
        "qte": _fmt_qte_unite(li),
        "colis": _fmt_qte(li.colis) if li.colis is not None else "-",
    } for i, li in enumerate(detail.lignes)]
    ui.table(columns=v_cols, rows=v_rows, row_key="i").classes("w-full").props(
        'flat bordered :rows-per-page-options="[0]"')


# ─── Vue secondaire générique (À vérifier / Passées) ─────────────────────────

def _secondary_page(route_title: str, statuts: tuple[str, ...], intro: str):
    user = require_auth()
    if not user:
        return
    tenant_id = user.get("tenant_id", "")
    with page_layout("Commandes reçues", "shopping_cart", "/commandes"):
        with ui.row().classes("items-center gap-2 q-mb-xs"):
            ui.button(icon="arrow_back",
                      on_click=lambda: ui.navigate.to("/commandes")).props(
                "flat round color=grey-8")
            ui.label(route_title).classes("text-h6").style(
                f"color: {COLORS['ink']}; font-weight: 600")
        ui.label(intro).classes("text-body2 q-mb-sm").style(f"color: {COLORS['ink2']}")
        list_box = ui.column().classes("w-full gap-2")

        async def _reload():
            from common.services.commande_service import list_commandes

            summaries = await asyncio.to_thread(
                list_commandes, tenant_id, statuts=statuts, limit=300)
            _render_table(list_box, summaries, tenant_id, _reload)

        ui.timer(0.2, _reload, once=True)


@ui.page("/commandes/a-verifier")
def page_commandes_a_verifier():
    _secondary_page(
        "À vérifier",
        ("a_verifier", "erreur"),
        "Documents reçus avec des produits, mais que l'IA n'a pas reconnus comme "
        "commande (factures probables…). Ouvre-les pour vérifier ; si c'en est une, "
        "clique « C'est une commande ».",
    )


# ─── Page détail en grand : /commandes/{id} (route dynamique en dernier) ─────

@ui.page("/commandes/{commande_id}")
def page_commande_detail(commande_id: str):
    user = require_auth()
    if not user:
        return
    tenant_id = user.get("tenant_id", "")
    with page_layout("Commandes reçues", "shopping_cart", "/commandes"):
        with ui.row().classes("items-center gap-2 q-mb-sm"):
            ui.button(icon="arrow_back",
                      on_click=lambda: ui.navigate.to("/commandes")).props(
                "flat round color=grey-8")
            ui.label("Retour aux commandes").classes("text-body2").style(
                f"color: {COLORS['ink2']}")
        body = ui.column().classes("w-full")

        async def _load():
            from common.services.commande_service import get_commande

            detail = await asyncio.to_thread(get_commande, tenant_id, commande_id)
            body.clear()
            with body:
                if detail is None:
                    error_banner("Commande introuvable.", dismissible=False)
                    return
                _render_commande_big(tenant_id, detail)

        ui.timer(0.1, _load, once=True)


# ─── Page principale : /commandes (commandes actives) ────────────────────────

@ui.page("/commandes")
def page_commandes():
    user = require_auth()
    if not user:
        return
    tenant_id = user.get("tenant_id", "")
    user_id = user.get("id")

    with page_layout("Commandes reçues", "shopping_cart", "/commandes"):
        ui.label(
            "Commandes des magasins reçues par email - lues automatiquement "
            "(PDF, txt, Excel…) et exploitables pour la production."
        ).classes("text-body2").style(f"color: {COLORS['ink2']}")

        if not is_ai_configured():
            with ui.card().classes("w-full").props("flat bordered").style(
                f"border-color: {COLORS['warning']}55; background: {COLORS['warning']}0D"
            ):
                with ui.card_section().classes("row items-start gap-3 q-pa-sm"):
                    ui.icon("warning", size="sm").style(f"color: {COLORS['warning']}")
                    ui.label(
                        "Extraction IA indisponible (GEMINI_API_KEY absente) : "
                        "les documents reçus seront archivés mais pas analysés."
                    ).classes("text-caption").style(f"color: {COLORS['ink2']}")

        # ── Dépôt manuel ────────────────────────────────────────────────
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.card_section().classes("q-pa-md column gap-2"):
                section_title("Déposer une commande (PDF)", "upload_file")
                ui.label(
                    "Filet de sécurité : si un mail passe à travers, dépose le "
                    "PDF ici - il sera lu et ajouté à la liste."
                ).classes("text-caption").style(f"color: {COLORS['ink2']}")
                upload_status = ui.label().classes("text-caption")

                async def _on_upload(e):
                    name = e.file.name
                    upload_status.text = f"Lecture de {name}…"
                    upload_status.style(f"color: {COLORS['ink2']}")
                    try:
                        pdf_bytes = await e.file.read()
                    except Exception as exc:  # noqa: BLE001
                        _log.exception("Lecture upload échouée")
                        upload_status.text = f"Erreur lecture : {exc}"
                        upload_status.style(f"color: {COLORS['error']}")
                        return
                    upload_status.text = f"Analyse de {name} en cours…"
                    try:
                        from common.services.commande_service import ingest_pdf

                        await asyncio.to_thread(
                            ingest_pdf, tenant_id, pdf_bytes,
                            filename=name, user_id=user_id, source="upload")
                    except Exception as exc:  # noqa: BLE001
                        _log.exception("Ingestion upload échouée")
                        upload_status.text = f"Erreur analyse : {exc}"
                        upload_status.style(f"color: {COLORS['error']}")
                        return
                    upload_status.text = f"✓ {name} ajouté."
                    upload_status.style(f"color: {COLORS['success']}")
                    ui.notify(f"Document « {name} » traité.", type="positive")
                    await _reload()

                ui.upload(on_upload=_on_upload, auto_upload=True, max_files=1,
                          label="Déposer un PDF de commande").props(
                    'accept=".pdf" flat bordered').classes("w-full")

        # ── Bouton discret « À vérifier » ───────────────────────────────
        nav_row = ui.row().classes("w-full gap-2 items-center q-mt-sm")
        kpis_box = ui.row().classes("w-full gap-3 wrap")
        # Section commandes à traiter (Nouveau + À produire)
        actives_section = ui.column().classes("w-full gap-2")
        # Section commandes envoyées (en bas de page)
        envoyees_section = ui.column().classes("w-full gap-2 q-mt-lg")

        def _render_nav(counts: dict):
            nav_row.clear()
            n_verif = counts.get("a_verifier", 0) + counts.get("erreur", 0)
            if n_verif:
                with nav_row:
                    ui.button(f"À vérifier ({n_verif})", icon="rule",
                              on_click=lambda: ui.navigate.to("/commandes/a-verifier")).props(
                        "flat dense color=red-7")

        def _render_kpis(counts: dict):
            kpis_box.clear()
            with kpis_box:
                kpi_card("inbox", "Actives",
                         str(counts.get("nouveau", 0) + counts.get("a_produire", 0)))
                kpi_card("fiber_new", "Nouvelles", str(counts.get("nouveau", 0)), COLORS["blue"])
                kpi_card("factory", "À produire", str(counts.get("a_produire", 0)), COLORS["orange"])

        async def _reload():
            from common.services.commande_service import count_by_statut, list_commandes

            try:
                counts = await asyncio.to_thread(count_by_statut, tenant_id)
                actives = await asyncio.to_thread(
                    list_commandes, tenant_id, statuts=_ACTIFS, limit=300)
                envoyees = await asyncio.to_thread(
                    list_commandes, tenant_id, statuts=("envoyee",), limit=300)
            except Exception as exc:  # noqa: BLE001
                _log.exception("Chargement commandes échoué")
                actives_section.clear()
                with actives_section:
                    error_banner(f"Échec du chargement : {exc}", retry_fn=lambda: _reload())
                return
            _render_nav(counts)
            _render_kpis(counts)

            # ── À traiter ────────────────────────────────────────────────
            actives_section.clear()
            with actives_section:
                section_title(f"À traiter ({len(actives)})", "assignment")
                actives_list = ui.column().classes("w-full")
            _render_table(actives_list, actives, tenant_id, _reload)

            # ── Envoyées (en bas, seulement s'il y en a) ─────────────────
            envoyees_section.clear()
            if envoyees:
                with envoyees_section:
                    section_title(f"Envoyées ({len(envoyees)})", "check_circle")
                    envoyees_list = ui.column().classes("w-full")
                _render_table(envoyees_list, envoyees, tenant_id, _reload)

        ui.timer(0.2, _reload, once=True)
