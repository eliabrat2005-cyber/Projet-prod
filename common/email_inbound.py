"""
common/email_inbound.py
=======================
Ingestion IMAP des mails de commande (étage ① du pipeline).

Poll périodique d'une boîte mail : récupère les nouveaux messages portant un
PDF en pièce jointe, et délègue à ``commande_service.ingest_pdf`` (source
``'email'``). La dédup se fait sur le ``Message-ID`` (index unique DB), donc
re-traiter un mail est sans danger.

⚠️ Désactivé tant que les variables IMAP ne sont pas dans le ``.env`` :

    IMAP_HOST          # ex: imap.gmail.com / ssl0.ovh.net  (obligatoire)
    IMAP_USER          # ex: commandes@symbiose-kefir.fr
    IMAP_PASSWORD      # mot de passe d'application (Gmail : App Password)
    IMAP_PORT          # défaut 993 (IMAPS)
    IMAP_FOLDER        # défaut INBOX (Gmail : nom d'un libellé, ex "Commandes")
    IMAP_POLL_SECONDS  # défaut 300 (5 min)
    IMAP_MAX_PER_CYCLE # défaut 25 ; nb max de mails récents examinés par cycle
    IMAP_SEARCH        # défaut "ALL" : on examine les mails récents qu'ils
                       # soient lus ou non (le statut lu/non-lu n'a aucune
                       # importance, la dédup se fait sur le Message-ID). On peut
                       # restreindre, ex 'FROM fournisseur@x.com', mais ce n'est
                       # pas nécessaire grâce au portier IA qui écarte les
                       # non-commandes.

Après modification du ``.env`` : redémarrer le service (``systemctl restart
ferment``). La boucle est branchée dans ``app_nicegui.py`` (@app.on_startup).
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message

_log = logging.getLogger("ferment.email_inbound")

_DEFAULT_PORT = 993
_DEFAULT_FOLDER = "INBOX"
_DEFAULT_POLL_SECONDS = 300
# ALL = on regarde les mails récents qu'ils soient lus ou non. La dédup se fait
# sur le Message-ID (pas sur le flag \Seen), donc un mail déjà ouvert par
# quelqu'un est quand même traité une seule fois.
_DEFAULT_SEARCH = "ALL"
_DEFAULT_MAX_PER_CYCLE = 25


# ─── Config ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImapConfig:
    host: str
    user: str
    password: str
    port: int = _DEFAULT_PORT
    folder: str = _DEFAULT_FOLDER
    poll_seconds: int = _DEFAULT_POLL_SECONDS
    search: str = _DEFAULT_SEARCH
    max_per_cycle: int = _DEFAULT_MAX_PER_CYCLE


def imap_config_from_env() -> ImapConfig | None:
    """Construit la config IMAP depuis l'environnement, ou ``None`` si absente."""
    host = os.getenv("IMAP_HOST", "").strip()
    user = os.getenv("IMAP_USER", "").strip()
    password = os.getenv("IMAP_PASSWORD", "")
    if not (host and user and password):
        return None

    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, "") or default)
        except ValueError:
            return default

    return ImapConfig(
        host=host,
        user=user,
        password=password,
        port=_int("IMAP_PORT", _DEFAULT_PORT),
        folder=os.getenv("IMAP_FOLDER", "").strip() or _DEFAULT_FOLDER,
        poll_seconds=_int("IMAP_POLL_SECONDS", _DEFAULT_POLL_SECONDS),
        search=os.getenv("IMAP_SEARCH", "").strip() or _DEFAULT_SEARCH,
        max_per_cycle=_int("IMAP_MAX_PER_CYCLE", _DEFAULT_MAX_PER_CYCLE),
    )


# ─── Modèle d'un mail de commande ─────────────────────────────────────────────

@dataclass
class OrderEmail:
    uid: bytes                       # UID IMAP (pour marquer \Seen)
    message_id: str
    sender: str
    subject: str
    body: str = ""                   # corps texte du mail (indice est_commande)
    # pièces jointes : [(filename, content_type, bytes)] — TOUS formats
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", "replace")


def _extract_body(msg: Message) -> str:
    """Corps texte du mail : préfère text/plain, sinon text/html dé-balisé."""
    html_fallback = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype == "text/plain":
            txt = _decode_payload(part)
            if txt.strip():
                return txt.strip()
        elif ctype == "text/html" and not html_fallback:
            html_fallback = _decode_payload(part)
    if html_fallback:
        # Retire les balises pour ne garder que le texte.
        return re.sub(r"\s{2,}", " ", re.sub(r"<[^>]+>", " ", html_fallback)).strip()
    return ""


def _extract_attachments(msg: Message) -> list[tuple[str, str, bytes]]:
    """Retourne TOUTES les pièces jointes [(filename, content_type, bytes)].

    Quel que soit le format (PDF, txt, csv, Excel, image...). On exclut les
    parties qui constituent le corps du mail (text/plain ou text/html sans nom
    de fichier ni disposition 'attachment')."""
    out: list[tuple[str, str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = _decode(part.get_filename())
        disposition = (part.get_content_disposition() or "")
        if not filename and disposition != "attachment":
            continue  # c'est le corps du mail, pas une pièce jointe
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        ctype = (part.get_content_type() or "").lower()
        out.append((filename or "piece-jointe", ctype, bytes(payload)))
    return out


# ─── Fetch (bloquant - appeler via asyncio.to_thread) ─────────────────────────

def _peek_message_id(conn: imaplib.IMAP4_SSL, num: bytes) -> str:
    """Lit UNIQUEMENT l'en-tête Message-ID (léger, sans marquer le mail lu)."""
    try:
        status, data = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if status != "OK" or not data or not data[0]:
            return ""
        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            return ""
        msg = email.message_from_bytes(bytes(raw))
        return _decode(msg.get("Message-ID")) or ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_new_order_pdfs(
    config: ImapConfig,
    already_ingested: Callable[[str], bool] | None = None,
) -> list[OrderEmail]:
    """Récupère les mails RÉCENTS portant un PDF, qu'ils soient lus ou non.

    On ne se fie PAS au statut lu/non-lu : la dédup se fait sur le Message-ID
    (``already_ingested``). Un mail déjà ouvert par quelqu'un est donc quand
    même traité une seule fois. On utilise ``BODY.PEEK`` pour ne JAMAIS modifier
    l'état (lu/non-lu) des mails de la boîte.

    Args:
        config: paramètres IMAP.
        already_ingested: callback ``(message_id) -> bool``. Les mails déjà
            ingérés sont sautés sans télécharger leur corps (économise temps +
            appels IA). ``None`` => rien n'est considéré comme déjà vu (dry-run).
    """
    if already_ingested is None:
        def already_ingested(_mid: str) -> bool:
            return False

    orders: list[OrderEmail] = []
    conn = imaplib.IMAP4_SSL(config.host, config.port)
    try:
        conn.login(config.user, config.password)
        conn.select(config.folder)
        status, data = conn.search(None, *config.search.split())
        if status != "OK":
            _log.warning("IMAP search a échoué : %s", status)
            return orders
        nums = data[0].split() if data and data[0] else []
        # On borne le travail aux N mails les plus récents, du + récent au +
        # ancien : évite de télécharger des centaines de mails sur une grosse
        # boîte (en régime établi, ils sont déjà ingérés -> simple peek).
        if config.max_per_cycle > 0:
            nums = nums[-config.max_per_cycle:]
        for num in reversed(nums):
            # 1) Peek léger du Message-ID : si déjà ingéré, on ne télécharge rien.
            mid = _peek_message_id(conn, num)
            if mid and already_ingested(mid):
                continue
            # 2) Mail nouveau : on télécharge tout (PEEK = sans marquer lu).
            status, msg_data = conn.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(bytes(raw))
            attachments = _extract_attachments(msg)
            if not attachments:
                continue  # mail sans pièce jointe -> ignoré
            orders.append(
                OrderEmail(
                    uid=num,
                    message_id=mid or _decode(msg.get("Message-ID")) or num.decode(),
                    sender=_decode(msg.get("From")),
                    subject=_decode(msg.get("Subject")),
                    body=_extract_body(msg),
                    attachments=attachments,
                )
            )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    return orders


# ─── Un tick de poll (bloquant) ──────────────────────────────────────────────

def poll_once(config: ImapConfig, tenant_id: str) -> int:
    """Un cycle : fetch (récents, lus ou non) → ingestion. Renvoie le nb ingéré.

    La dédup se fait sur le Message-ID (``message_already_ingested``) : aucun
    mail n'est marqué lu, on ne modifie jamais la boîte. Un mail déjà traité
    n'est même pas re-téléchargé.
    """
    from common.services.commande_service import ingest_email, message_already_ingested

    orders = fetch_new_order_pdfs(
        config,
        already_ingested=lambda mid: message_already_ingested(tenant_id, mid),
    )
    ingested = 0
    for order in orders:
        try:
            # Un mail = une ingestion : le service lit le corps + toutes les
            # pièces jointes (PDF, txt, csv, Excel...) et décide via l'IA.
            cid = ingest_email(
                tenant_id,
                sender=order.sender,
                subject=order.subject,
                body=order.body,
                message_id=order.message_id,
                attachments=order.attachments,
            )
            if cid:
                ingested += 1
        except Exception:  # noqa: BLE001 - un mail KO ne bloque pas les autres
            _log.exception("Ingestion du mail %s échouée", order.message_id)

    return ingested


# ─── Boucle de fond (branchée dans @app.on_startup) ──────────────────────────

async def commandes_inbound_loop() -> None:
    """Boucle infinie : poll IMAP toutes les ``IMAP_POLL_SECONDS``.

    No-op (log unique + sortie) si IMAP n'est pas configuré dans le ``.env``,
    pour ne rien casser tant que la boîte mail n'est pas branchée.
    """
    config = imap_config_from_env()
    if config is None:
        _log.info(
            "Ingestion commandes IMAP désactivée (IMAP_HOST/USER/PASSWORD absents). "
            "L'upload manuel reste disponible sur /commandes."
        )
        return

    _log.info(
        "Ingestion commandes IMAP active : %s@%s (toutes les %ds)",
        config.user, config.host, config.poll_seconds,
    )
    while True:
        try:
            from common.sync.scheduler import _get_default_tenant_id

            tenant_id = await asyncio.to_thread(_get_default_tenant_id)
            if not tenant_id:
                _log.error("IMAP poll : tenant_id introuvable (ALLOWED_TENANTS ?)")
            else:
                n = await asyncio.to_thread(poll_once, config, tenant_id)
                if n:
                    _log.info("IMAP poll : %d commande(s) ingérée(s)", n)
        except Exception:  # noqa: BLE001 - la boucle ne meurt jamais
            _log.exception("IMAP poll : erreur de cycle")
        await asyncio.sleep(config.poll_seconds)
