"""Entité image exposant la dernière photo de repas, comme un snapshot de caméra."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, signal_photo_updated
from .data import CoachSanteConfigEntry, CoachSanteData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoachSanteConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crée l'entité image de la personne."""
    async_add_entities([CoachSanteMealImage(hass, entry.runtime_data)])


class CoachSanteMealImage(ImageEntity):
    """Dernière photo de repas envoyée depuis l'iPhone."""

    _attr_has_entity_name = True
    _attr_name = "Dernier repas"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, data: CoachSanteData) -> None:
        """Prépare l'entité à partir de la photo éventuellement déjà sur disque."""
        super().__init__(hass)
        self._data = data
        self._cache: tuple[str, bytes] | None = None

        self._attr_unique_id = f"{data.entry.entry_id}_meal_photo"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.entry.entry_id)},
            name=data.person,
            manufacturer="CoachSanté",
            model="Apple Santé",
        )
        self._attr_image_last_updated = data.photo_updated
        self._attr_content_type = data.photo_content_type

    async def async_added_to_hass(self) -> None:
        """Publie son entity_id pour les événements, et suit les nouvelles photos."""
        self._data.image_entity_id = self.entity_id
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_photo_updated(self._data.entry.entry_id),
                self._handle_new_photo,
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Oublie l'entity_id publié."""
        self._data.image_entity_id = None

    @callback
    def _handle_new_photo(self) -> None:
        """Invalide le cache et signale à Home Assistant que l'image a changé."""
        self._attr_image_last_updated = self._data.photo_updated
        self._attr_content_type = self._data.photo_content_type
        self._cache = None
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Renvoie les octets de la photo, relus depuis le disque si nécessaire."""
        path = self._data.photo_path
        if not path:
            return None
        if self._cache is not None and self._cache[0] == path:
            return self._cache[1]

        raw = await self.hass.async_add_executor_job(_read_photo, path)
        if raw is not None:
            self._cache = (path, raw)
        return raw


def _read_photo(path: str) -> bytes | None:
    """Lit la photo sur disque. Exécuté hors de la boucle d'événements."""
    try:
        return Path(path).read_bytes()
    except OSError as err:
        _LOGGER.error("Lecture impossible de la photo %s : %s", path, err)
        return None
