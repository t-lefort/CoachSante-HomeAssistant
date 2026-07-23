"""État d'une personne suivie par CoachSanté, et sa persistance sur disque."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_PERSON,
    CONF_PHOTO_RETENTION,
    DEFAULT_PHOTO_RETENTION,
    DOMAIN,
    NUTRIENTS,
    STORAGE_VERSION,
)
from .metrics import DAILY_SUM_KEYS

_LOGGER = logging.getLogger(__name__)

# On temporise l'écriture sur disque : l'app peut envoyer plusieurs lots
# d'affilée, inutile de réécrire le fichier à chaque fois.
SAVE_DELAY = 10

# Nombre de repas du jour conservés en mémoire pour les attributs d'entité.
MAX_MEALS_PER_DAY = 20

# Alias posé en assignation simple (et non avec `type`) pour rester lisible par le
# Python 3.11 du poste de dev, alors que Home Assistant tourne en 3.13.
CoachSanteConfigEntry = ConfigEntry["CoachSanteData"]


@dataclass(slots=True)
class MetricValue:
    """Dernière valeur connue d'une métrique santé."""

    value: Any
    unit: str | None = None
    day: str | None = None
    updated_at: str | None = None
    source: str | None = None


class CoachSanteData:
    """Toutes les données d'une personne : métriques, nutrition, dernière photo."""

    def __init__(self, hass: HomeAssistant, entry: CoachSanteConfigEntry) -> None:
        """Initialise l'état à vide ; `async_load` le remplit depuis le disque."""
        self.hass = hass
        self.entry = entry
        self.person: str = entry.data[CONF_PERSON]
        self.slug: str = slugify(self.person)

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )

        self.metrics: dict[str, MetricValue] = {}
        self.nutrition: dict[str, float] = dict.fromkeys(NUTRIENTS, 0.0)
        self.nutrition_day: str = _today()
        self.meals: list[dict[str, Any]] = []

        self.photo_path: str | None = None
        self.photo_updated: datetime | None = None
        self.photo_note: str | None = None
        self.photo_content_type: str = "image/jpeg"

        # Renseigné par la plateforme `sensor` : permet de créer une entité à la
        # volée quand l'app remonte une métrique jamais vue.
        self.async_add_metric_entities: Callable[[list[str]], None] | None = None

        # Renseigné par la plateforme `image`, pour que l'événement de nouvelle
        # photo puisse dire à l'automatisation quelle entité regarder.
        self.image_entity_id: str | None = None

    # --- Persistance -------------------------------------------------------

    async def async_load(self) -> None:
        """Recharge l'état sauvegardé, si présent."""
        stored = await self._store.async_load()
        if not stored:
            return

        self.metrics = {
            key: MetricValue(**value)
            for key, value in stored.get("metrics", {}).items()
            if isinstance(value, dict)
        }

        nutrition = stored.get("nutrition", {})
        self.nutrition_day = nutrition.get("day", _today())
        self.nutrition.update(
            {
                key: float(value)
                for key, value in nutrition.get("totals", {}).items()
                if key in self.nutrition
            }
        )
        self.meals = nutrition.get("meals", [])

        photo = stored.get("photo", {})
        self.photo_path = photo.get("path")
        self.photo_note = photo.get("note")
        self.photo_content_type = photo.get("content_type", "image/jpeg")
        if updated := photo.get("updated_at"):
            self.photo_updated = dt_util.parse_datetime(updated)

        # Si le fichier a été supprimé à la main entre deux démarrages, on évite
        # de présenter une entité image cassée.
        if self.photo_path and not await self.hass.async_add_executor_job(
            Path(self.photo_path).is_file
        ):
            _LOGGER.info("Photo %s introuvable, entité image réinitialisée", self.photo_path)
            self.photo_path = None
            self.photo_updated = None

        self._ensure_today()
        # Redémarrage après minuit : les compteurs du jour d'hier sont périmés.
        self.reset_stale_daily_metrics()

    def async_schedule_save(self) -> None:
        """Programme une sauvegarde différée de l'état."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    async def async_remove_storage(self) -> None:
        """Supprime le fichier d'état (appelé quand l'entrée est retirée)."""
        await self._store.async_remove()

    def _as_dict(self) -> dict[str, Any]:
        return {
            "metrics": {key: asdict(value) for key, value in self.metrics.items()},
            "nutrition": {
                "day": self.nutrition_day,
                "totals": self.nutrition,
                "meals": self.meals,
            },
            "photo": {
                "path": self.photo_path,
                "note": self.photo_note,
                "content_type": self.photo_content_type,
                "updated_at": self.photo_updated.isoformat() if self.photo_updated else None,
            },
        }

    # --- Métriques santé ---------------------------------------------------

    def apply_metrics(self, metrics: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        """Applique un lot de métriques.

        Renvoie `(clés acceptées, clés jamais vues)` — les secondes ont besoin
        qu'une entité soit créée pour elles.
        """
        accepted: list[str] = []
        new_keys: list[str] = []

        for item in metrics:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                continue
            if "value" not in item:
                continue

            if key not in self.metrics:
                new_keys.append(key)

            self.metrics[key] = MetricValue(
                value=item.get("value"),
                unit=item.get("unit"),
                day=item.get("day"),
                updated_at=item.get("updated_at") or dt_util.utcnow().isoformat(),
                source=item.get("source"),
            )
            accepted.append(key)

        return accepted, new_keys

    def reset_stale_daily_metrics(self) -> list[str]:
        """Remet à zéro les compteurs « somme du jour » restés sur un jour passé.

        Sans ça, un compteur comme `steps` afficherait le total d'hier jusqu'au
        premier envoi du matin, trompant une automatisation qui le lirait juste
        après minuit. On ne touche qu'aux métriques dont le `jour` connu est
        antérieur à aujourd'hui : une valeur sans jour ou « dernier » est laissée
        telle quelle. Renvoie les clés remises à zéro.
        """
        today = _today()
        reset: list[str] = []
        for key in DAILY_SUM_KEYS:
            metric = self.metrics.get(key)
            if metric is None or not metric.day or metric.day >= today:
                continue
            metric.value = 0
            metric.day = today
            metric.updated_at = dt_util.utcnow().isoformat()
            reset.append(key)
        return reset

    # --- Nutrition ---------------------------------------------------------

    def _ensure_today(self) -> bool:
        """Remet les compteurs à zéro si on a changé de jour. Renvoie True si reset."""
        today = _today()
        if self.nutrition_day == today:
            return False
        self.nutrition_day = today
        self.nutrition = dict.fromkeys(NUTRIENTS, 0.0)
        self.meals = []
        return True

    def roll_over_day(self) -> bool:
        """Force la vérification du changement de jour (appelé à minuit)."""
        return self._ensure_today()

    def add_nutrition(self, values: dict[str, float], label: str | None) -> None:
        """Ajoute les macros d'un repas aux compteurs du jour."""
        self._ensure_today()

        for key, amount in values.items():
            if key in self.nutrition:
                self.nutrition[key] += amount

        meal: dict[str, Any] = {
            "label": label,
            "at": dt_util.now().isoformat(),
            **values,
        }
        self.meals.append(meal)
        del self.meals[:-MAX_MEALS_PER_DAY]

    def reset_day(self) -> None:
        """Remet à zéro les compteurs nutritionnels du jour."""
        self.nutrition_day = _today()
        self.nutrition = dict.fromkeys(NUTRIENTS, 0.0)
        self.meals = []

    @property
    def last_meal(self) -> dict[str, Any] | None:
        """Dernier repas enregistré aujourd'hui."""
        return self.meals[-1] if self.meals else None

    # --- Photos ------------------------------------------------------------

    @property
    def photo_dir(self) -> Path:
        """Dossier où sont rangées les photos de repas de cette personne."""
        media_root = self.hass.config.media_dirs.get("local") or self.hass.config.path("media")
        return Path(media_root) / DOMAIN / self.slug

    async def async_save_photo(
        self,
        raw: bytes,
        *,
        content_type: str,
        note: str | None,
        taken_at: datetime,
    ) -> Path:
        """Écrit la photo sur disque et met à jour l'état de l'entité image."""
        suffix = ".png" if content_type == "image/png" else ".jpg"
        filename = f"{taken_at.strftime('%Y-%m-%d_%H%M%S')}{suffix}"
        path = self.photo_dir / filename
        retention = self.entry.options.get(CONF_PHOTO_RETENTION, DEFAULT_PHOTO_RETENTION)

        # `_write_photo` renvoie le chemin réellement écrit : deux photos prises
        # dans la même seconde ne s'écrasent pas, la seconde est suffixée.
        written = await self.hass.async_add_executor_job(_write_photo, path, raw, retention)

        self.photo_path = str(written)
        self.photo_updated = taken_at
        self.photo_note = note
        self.photo_content_type = content_type
        return written


def _today() -> str:
    return dt_util.now().date().isoformat()


def _unique_path(path: Path) -> Path:
    """Renvoie `path`, ou une variante suffixée `_2`, `_3`… s'il existe déjà."""
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _write_photo(path: Path, raw: bytes, retention: int) -> Path:
    """Écrit la photo et purge les plus anciennes. Exécuté hors boucle d'événements.

    Renvoie le chemin réellement écrit (suffixé en cas de collision de nom).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _unique_path(path)
    path.write_bytes(raw)

    if retention <= 0:
        return path

    photos = sorted(
        (item for item in path.parent.iterdir() if item.is_file()),
        key=lambda item: item.stat().st_mtime,
    )
    for old in photos[:-retention]:
        try:
            old.unlink()
        except OSError:
            _LOGGER.warning("Impossible de supprimer l'ancienne photo %s", old)

    return path
