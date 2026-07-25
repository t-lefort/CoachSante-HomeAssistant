"""Réception des données poussées par l'app iOS.

Un webhook par personne. L'URL contient déjà un identifiant secret, mais comme
elle transite par un reverse proxy exposé sur Internet, chaque requête est en
plus signée en HMAC-SHA256 avec un secret partagé.
"""

from __future__ import annotations

import base64
import binascii
from datetime import timedelta
import hashlib
import hmac
import logging
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from homeassistant.util.json import json_loads

from .const import (
    CONF_SECRET,
    EVENT_CONTEXT,
    EVENT_CONTEXT_PHOTO,
    EVENT_MEAL_PHOTO,
    EVENT_METRICS,
    HEADER_SIGNATURE,
    MAX_PAYLOAD_BYTES,
    PAYLOAD_TYPE_CONTEXT,
    PAYLOAD_TYPE_MEAL_PHOTO,
    PAYLOAD_TYPE_METRICS,
    REPLAY_MAX_AGE_SECONDS,
    SIGNATURE_PREFIX,
    signal_context_updated,
    signal_metrics_updated,
    signal_photo_updated,
)
from .data import CoachSanteConfigEntry, CoachSanteData

_LOGGER = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


async def async_handle_webhook(
    entry: CoachSanteConfigEntry,
    hass: HomeAssistant,
    webhook_id: str,
    request: web.Request,
) -> web.Response:
    """Point d'entrée unique du webhook d'une personne."""
    data = entry.runtime_data

    if request.content_length is not None and request.content_length > MAX_PAYLOAD_BYTES:
        return web.Response(status=413, text="charge utile trop volumineuse")

    body = await request.read()
    if len(body) > MAX_PAYLOAD_BYTES:
        return web.Response(status=413, text="charge utile trop volumineuse")

    signature = request.headers.get(HEADER_SIGNATURE)
    if not _signature_is_valid(entry.data[CONF_SECRET], body, signature):
        _LOGGER.warning("Signature invalide sur le webhook de %s", data.person)
        return web.Response(status=401, text="signature invalide")

    try:
        payload = json_loads(body)
    except ValueError:
        return web.Response(status=400, text="corps JSON invalide")

    if not isinstance(payload, dict):
        return web.Response(status=400, text="le corps doit être un objet JSON")

    if _payload_is_stale(payload):
        _LOGGER.warning("Payload rejeté (anti-rejeu) sur le webhook de %s", data.person)
        return web.Response(status=400, text="horodatage « sent_at » trop ancien")

    payload_type = payload.get("type")
    if payload_type == PAYLOAD_TYPE_METRICS:
        return _handle_metrics(hass, entry, data, payload)
    if payload_type == PAYLOAD_TYPE_MEAL_PHOTO:
        return await _handle_meal_photo(hass, entry, data, payload)
    if payload_type == PAYLOAD_TYPE_CONTEXT:
        return await _handle_context(hass, entry, data, payload)

    return web.Response(status=400, text=f"type de charge utile inconnu : {payload_type!r}")


def _signature_is_valid(secret: str, body: bytes, header: str | None) -> bool:
    """Vérifie la signature HMAC-SHA256 du corps de la requête."""
    if not header or not header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(SIGNATURE_PREFIX) :], expected)


def _payload_is_stale(payload: dict[str, Any]) -> bool:
    """Vrai si `sent_at` est plus vieux que la fenêtre anti-rejeu.

    Absent ou illisible, on laisse passer : c'est le secret HMAC qui protège
    l'intégrité, `sent_at` ne sert qu'à écarter le rejeu d'une requête capturée.
    Un `sent_at` dans le futur (dérive d'horloge) est également accepté.
    """
    sent_at = dt_util.parse_datetime(payload.get("sent_at") or "")
    if sent_at is None:
        return False
    age = dt_util.utcnow() - dt_util.as_utc(sent_at)
    return age > timedelta(seconds=REPLAY_MAX_AGE_SECONDS)


def _handle_metrics(
    hass: HomeAssistant,
    entry: CoachSanteConfigEntry,
    data: CoachSanteData,
    payload: dict[str, Any],
) -> web.Response:
    """Enregistre un lot de métriques santé déjà agrégées par l'app."""
    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        return web.Response(status=400, text="« metrics » doit être une liste")

    accepted, new_keys = data.apply_metrics(metrics)

    if new_keys and data.async_add_metric_entities is not None:
        data.async_add_metric_entities(new_keys)

    if accepted:
        data.async_schedule_save()
        async_dispatcher_send(hass, signal_metrics_updated(entry.entry_id))
        hass.bus.async_fire(
            EVENT_METRICS,
            {
                "entry_id": entry.entry_id,
                "person": data.person,
                "keys": accepted,
            },
        )

    return web.json_response({"ok": True, "accepted": len(accepted)})


async def _handle_meal_photo(
    hass: HomeAssistant,
    entry: CoachSanteConfigEntry,
    data: CoachSanteData,
    payload: dict[str, Any],
) -> web.Response:
    """Range une photo de repas et prévient les automatisations."""
    try:
        raw, content_type = await _decode_photo(hass, payload.get("photo"))
    except ValueError as err:
        return web.Response(status=400, text=str(err))

    taken_at = dt_util.parse_datetime(payload.get("taken_at") or "") or dt_util.now()
    taken_at = dt_util.as_local(taken_at)
    note = payload.get("note")

    path = await data.async_save_photo(
        raw,
        content_type=content_type,
        note=note if isinstance(note, str) else None,
        taken_at=taken_at,
    )

    data.async_schedule_save()
    async_dispatcher_send(hass, signal_photo_updated(entry.entry_id))
    hass.bus.async_fire(
        EVENT_MEAL_PHOTO,
        {
            "entry_id": entry.entry_id,
            "person": data.person,
            "path": str(path),
            "media_content_id": data.media_content_id(str(path)),
            "image_entity_id": data.image_entity_id,
            "note": data.photo_note,
            "taken_at": taken_at.isoformat(),
            # Ce que l'app a envoyé comme contexte ces dernières semaines : à
            # coller tel quel dans le prompt d'estimation.
            "context": data.context_prompt,
        },
    )

    return web.json_response({"ok": True, "path": str(path)})


async def _handle_context(
    hass: HomeAssistant,
    entry: CoachSanteConfigEntry,
    data: CoachSanteData,
    payload: dict[str, Any],
) -> web.Response:
    """Range un élément de contexte : texte, photo à faire analyser, ou les deux."""
    text = payload.get("text")
    text = text if isinstance(text, str) else None
    label = payload.get("label")
    label = label if isinstance(label, str) else None
    photo = payload.get("photo")

    if not (text and text.strip()) and photo is None:
        return web.Response(status=400, text="« text » ou « photo » est obligatoire")

    captured_at = dt_util.parse_datetime(payload.get("captured_at") or "") or dt_util.now()
    captured_at = dt_util.as_local(captured_at)

    written = None
    if photo is not None:
        try:
            raw, content_type = await _decode_photo(hass, photo)
        except ValueError as err:
            return web.Response(status=400, text=str(err))
        written = await data.async_save_context_photo(
            raw, content_type=content_type, captured_at=captured_at
        )

    item = data.add_context(
        text=text,
        label=label,
        photo_path=str(written) if written else None,
        at=captured_at,
    )
    await data.async_purge_expired_context()
    data.async_schedule_save()
    async_dispatcher_send(hass, signal_context_updated(entry.entry_id))

    event = {
        "entry_id": entry.entry_id,
        "person": data.person,
        "context_id": item.id,
        "label": item.label,
        "text": item.text,
        "captured_at": item.at,
    }
    if written is not None:
        # C'est cet événement qui demande l'analyse de la photo : l'automatisation
        # répond par `coachsante.add_context` en repassant le `context_id`.
        event |= {"path": str(written), "media_content_id": data.media_content_id(str(written))}
        hass.bus.async_fire(EVENT_CONTEXT_PHOTO, event)
    else:
        hass.bus.async_fire(EVENT_CONTEXT, event)

    return web.json_response(
        {"ok": True, "context_id": item.id, "path": str(written) if written else None}
    )


async def _decode_photo(hass: HomeAssistant, photo: Any) -> tuple[bytes, str]:
    """Décode la photo d'un payload, après validation.

    Lève `ValueError` — dont le message part tel quel en 400 — si elle est
    inexploitable. Renvoie `(octets, type MIME)`.
    """
    if not isinstance(photo, dict) or not isinstance(photo.get("data"), str):
        raise ValueError("« photo.data » (base64) est obligatoire")

    content_type = photo.get("content_type", "image/jpeg")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(f"type d'image non supporté : {content_type!r}")

    try:
        raw = await hass.async_add_executor_job(_decode_base64, photo["data"])
    except (binascii.Error, ValueError) as err:
        raise ValueError("base64 invalide") from err

    if not raw:
        raise ValueError("photo vide")

    return raw, content_type


def _decode_base64(data: str) -> bytes:
    """Décode le base64 de la photo. Lourd (~10 Mo) → hors boucle d'événements."""
    return base64.b64decode(data, validate=True)
