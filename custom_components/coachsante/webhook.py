"""Réception des données poussées par l'app iOS.

Un webhook par personne. L'URL contient déjà un identifiant secret, mais comme
elle transite par un reverse proxy exposé sur Internet, chaque requête est en
plus signée en HMAC-SHA256 avec un secret partagé.
"""

from __future__ import annotations

import base64
import binascii
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
    EVENT_MEAL_PHOTO,
    EVENT_METRICS,
    HEADER_SIGNATURE,
    MAX_PAYLOAD_BYTES,
    PAYLOAD_TYPE_MEAL_PHOTO,
    PAYLOAD_TYPE_METRICS,
    SIGNATURE_PREFIX,
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

    payload_type = payload.get("type")
    if payload_type == PAYLOAD_TYPE_METRICS:
        return _handle_metrics(hass, entry, data, payload)
    if payload_type == PAYLOAD_TYPE_MEAL_PHOTO:
        return await _handle_meal_photo(hass, entry, data, payload)

    return web.Response(status=400, text=f"type de charge utile inconnu : {payload_type!r}")


def _signature_is_valid(secret: str, body: bytes, header: str | None) -> bool:
    """Vérifie la signature HMAC-SHA256 du corps de la requête."""
    if not header or not header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(SIGNATURE_PREFIX) :], expected)


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
    photo = payload.get("photo")
    if not isinstance(photo, dict) or not isinstance(photo.get("data"), str):
        return web.Response(status=400, text="« photo.data » (base64) est obligatoire")

    content_type = photo.get("content_type", "image/jpeg")
    if content_type not in ALLOWED_CONTENT_TYPES:
        return web.Response(status=400, text=f"type d'image non supporté : {content_type!r}")

    try:
        raw = base64.b64decode(photo["data"], validate=True)
    except (binascii.Error, ValueError):
        return web.Response(status=400, text="base64 invalide")

    if not raw:
        return web.Response(status=400, text="photo vide")

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
            "image_entity_id": data.image_entity_id,
            "note": data.photo_note,
            "taken_at": taken_at.isoformat(),
        },
    )

    return web.json_response({"ok": True, "path": str(path)})
