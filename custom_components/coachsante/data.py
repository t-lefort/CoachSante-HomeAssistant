"""État d'une personne suivie par CoachSanté, et sa persistance sur disque."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util, slugify

from .const import (
    CONF_CONTEXT_RETENTION_DAYS,
    CONF_PERSON,
    CONF_PHOTO_RETENTION,
    DEFAULT_CONTEXT_RETENTION_DAYS,
    DEFAULT_PHOTO_RETENTION,
    DOMAIN,
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_PROMPT_LENGTH,
    MAX_CONTEXT_TEXT_LENGTH,
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

# Sous-dossier des photos de contexte, à côté des photos de repas.
CONTEXT_DIRNAME = "contexte"

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


@dataclass(slots=True)
class ContextItem:
    """Un élément de contexte destiné au modèle qui estime les repas.

    Trois formes : un texte seul (lien de recette, précision écrite dans l'app),
    une photo seule (emballage, étiquette nutritionnelle) dont l'analyse arrive
    ensuite par le service `add_context`, ou les deux. `analysis` est ce que le
    modèle a lu sur la photo : elle **s'ajoute** au texte, elle ne le remplace pas.
    """

    id: str
    at: str
    label: str | None = None
    text: str | None = None
    analysis: str | None = None
    photo_path: str | None = None

    @property
    def pending_analysis(self) -> bool:
        """Vrai tant qu'une photo attend sa description."""
        return self.photo_path is not None and not self.analysis

    @property
    def is_usable(self) -> bool:
        """Vrai s'il y a quelque chose à donner au modèle."""
        return bool(self.text or self.analysis)

    def as_prompt_line(self) -> str:
        """Ligne telle qu'elle sera injectée dans le prompt d'estimation."""
        parts = [part for part in (self.label, self.text, self.analysis) if part]
        # « 2026-07-25T19:59:00+02:00 » → « 2026-07-25 19:59 »
        stamp = self.at[:16].replace("T", " ")
        return f"- {stamp} : {' — '.join(parts)}"


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
        self.context: list[ContextItem] = []
        self.goal: str = ""

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

        self.context = [
            ContextItem(**item)
            for item in stored.get("context", [])
            if isinstance(item, dict) and "id" in item and "at" in item
        ]
        self.goal = stored.get("goal", "") if isinstance(stored.get("goal", ""), str) else ""

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
        # Home Assistant a pu rester éteint plus longtemps que la rétention.
        await self.async_purge_expired_context()

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
            "context": [asdict(item) for item in self.context],
            "goal": self.goal,
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

    # --- Contexte nutritionnel ---------------------------------------------

    @property
    def context_retention(self) -> timedelta | None:
        """Durée de conservation d'un élément de contexte. `None` = pour toujours."""
        days = self.entry.options.get(CONF_CONTEXT_RETENTION_DAYS, DEFAULT_CONTEXT_RETENTION_DAYS)
        return timedelta(days=days) if days > 0 else None

    @property
    def context_prompt(self) -> str:
        """Bloc de texte prêt à injecter dans le prompt d'estimation d'un repas.

        Les éléments récents priment : on remplit en remontant le temps jusqu'à
        la borne, puis on remet en ordre chronologique. Un contexte tronqué garde
        ainsi ce qui vient d'être envoyé plutôt que les vieilleries.
        """
        lines: list[str] = []
        budget = MAX_CONTEXT_PROMPT_LENGTH
        for item in reversed(self.context):
            if not item.is_usable:
                continue
            line = item.as_prompt_line()
            if len(line) + 1 > budget:
                break
            lines.append(line)
            budget -= len(line) + 1
        lines.reverse()
        return "\n".join(lines)

    @property
    def context_pending_count(self) -> int:
        """Nombre de photos de contexte qui attendent encore leur analyse."""
        return sum(1 for item in self.context if item.pending_analysis)

    def add_context(
        self,
        *,
        text: str | None = None,
        label: str | None = None,
        analysis: str | None = None,
        photo_path: str | None = None,
        at: datetime | None = None,
    ) -> ContextItem:
        """Ajoute un élément de contexte et renvoie l'objet créé."""
        item = ContextItem(
            id=secrets.token_hex(8),
            at=(at or dt_util.now()).isoformat(),
            label=_clip(label),
            text=_clip(text),
            analysis=_clip(analysis),
            photo_path=photo_path,
        )
        self.context.append(item)
        # La file d'attente de l'app peut rejouer dans le désordre : on retrie
        # pour que le prompt se lise du plus ancien au plus récent.
        self.context.sort(key=lambda entry: entry.at)
        return item

    def complete_context(
        self, context_id: str, analysis: str, label: str | None = None
    ) -> ContextItem | None:
        """Attache à un élément l'analyse de sa photo. `None` s'il a expiré entre-temps.

        L'analyse remplace une éventuelle analyse précédente : une automatisation
        qui rejoue son appel après un échec ne duplique rien.
        """
        for item in self.context:
            if item.id == context_id:
                item.analysis = _clip(analysis)
                if label:
                    item.label = _clip(label)
                return item
        return None

    async def async_clear_context(self, context_id: str | None = None) -> int:
        """Retire un élément (ou tout le contexte) et efface ses photos."""
        if context_id is None:
            dropped, self.context = self.context, []
        else:
            dropped = [item for item in self.context if item.id == context_id]
            self.context = [item for item in self.context if item.id != context_id]

        if orphans := [item.photo_path for item in dropped if item.photo_path]:
            await self.hass.async_add_executor_job(_delete_files, orphans)
        return len(dropped)

    def purge_expired_context(self) -> list[str]:
        """Retire ce qui a dépassé la rétention, et le trop-plein.

        Renvoie les photos devenues orphelines, à effacer hors boucle d'événements.
        """
        retention = self.context_retention
        limit = dt_util.now() - retention if retention else None

        kept: list[ContextItem] = []
        dropped: list[ContextItem] = []
        for item in self.context:
            at = dt_util.parse_datetime(item.at)
            if limit is not None and at is not None and dt_util.as_local(at) < limit:
                dropped.append(item)
            else:
                kept.append(item)

        # Garde-fou indépendant de la date : au-delà de la borne, les plus
        # anciens sautent (un attribut d'entité ne doit pas enfler sans limite).
        overflow = max(0, len(kept) - MAX_CONTEXT_ITEMS)
        dropped.extend(kept[:overflow])
        self.context = kept[overflow:]

        return [item.photo_path for item in dropped if item.photo_path]

    async def async_purge_expired_context(self) -> bool:
        """Purge le contexte périmé et efface ses photos. Vrai si la liste a changé."""
        before = len(self.context)
        if orphans := self.purge_expired_context():
            await self.hass.async_add_executor_job(_delete_files, orphans)
        return len(self.context) != before

    # --- Photos ------------------------------------------------------------

    @property
    def photo_dir(self) -> Path:
        """Dossier où sont rangées les photos de repas de cette personne."""
        media_root = self.hass.config.media_dirs.get("local") or self.hass.config.path("media")
        return Path(media_root) / DOMAIN / self.slug

    @property
    def context_dir(self) -> Path:
        """Sous-dossier des photos de contexte (emballages, étiquettes…)."""
        return self.photo_dir / CONTEXT_DIRNAME

    def media_content_id(self, path: str | None) -> str | None:
        """Construit l'identifiant *media source* d'une photo, à passer tel quel à `ai_task`.

        `None` si le dossier média local n'est pas déclaré dans la configuration :
        l'automatisation doit alors se rabattre sur le chemin disque.
        """
        media_root = self.hass.config.media_dirs.get("local")
        if not path or not media_root:
            return None
        try:
            relative = Path(path).relative_to(media_root)
        except ValueError:
            return None
        return f"media-source://media_source/local/{relative.as_posix()}"

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

    async def async_save_context_photo(
        self, raw: bytes, *, content_type: str, captured_at: datetime
    ) -> Path:
        """Écrit une photo de contexte et renvoie son chemin réel.

        Pas de purge par nombre ici : ces photos disparaissent avec l'élément de
        contexte qui les porte, à l'expiration de la rétention.
        """
        suffix = ".png" if content_type == "image/png" else ".jpg"
        path = self.context_dir / f"{captured_at.strftime('%Y-%m-%d_%H%M%S')}{suffix}"
        return await self.hass.async_add_executor_job(_write_context_photo, path, raw)


def _today() -> str:
    return dt_util.now().date().isoformat()


def _clip(text: str | None) -> str | None:
    """Normalise un texte de contexte : espaces rognés, longueur bornée, vide → None."""
    if text is None:
        return None
    text = text.strip()
    return text[:MAX_CONTEXT_TEXT_LENGTH] if text else None


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


def _write_context_photo(path: Path, raw: bytes) -> Path:
    """Écrit une photo de contexte. Exécuté hors boucle d'événements."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _unique_path(path)
    path.write_bytes(raw)
    return path


def _delete_files(paths: list[str]) -> None:
    """Efface les photos d'un contexte périmé. Exécuté hors boucle d'événements."""
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            _LOGGER.warning("Impossible de supprimer la photo de contexte %s", path)
