"""Import des séries horaires dans les statistiques long terme de Home Assistant.

Les sensors de l'intégration portent l'**état courant** — total du jour, dernière
valeur — et c'est ce que lisent les automatisations. Ils ne peuvent pas porter
d'historique fin : l'état d'un sensor est toujours horodaté « maintenant », le
recorder n'accepte pas d'état antidaté. Envoyer douze points d'un coup à 14 h
écrirait douze états datés 14 h, et l'historique resterait le même escalier.

Les **statistiques long terme** sont le mécanisme prévu pour ça, celui qu'utilisent
les intégrations d'énergie pour importer un relevé de compteur historique : des
lignes horodatées librement, alignées sur l'heure. Réimporter une heure écrase la
ligne existante — l'heure en cours, forcément partielle quand elle part, se
corrige donc d'elle-même au passage suivant.

Contrepartie assumée : ces statistiques ne sont **pas des entités**. Elles
apparaissent dans les graphiques et le panneau « Statistiques », pas dans les
templates. C'est le partage voulu : les sensors pour automatiser, les
statistiques pour analyser après coup.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any, Final

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN
from .metrics import METRIC_DESCRIPTIONS

_LOGGER = logging.getLogger(__name__)

# Une série cumulative (pas, kcal) porte le total de chaque heure ; une série de
# mesure (fréquence cardiaque) porte moyenne, minimum et maximum de l'heure.
KIND_SUM: Final = "sum"
KIND_MEASUREMENT: Final = "measurement"

# Nombre de points acceptés d'un coup, toutes séries confondues. Large de quoi
# rattraper plusieurs jours de retard sans laisser un payload malformé nous faire
# écrire n'importe quoi dans la base.
MAX_POINTS: Final = 5000

# Recul consenti pour retrouver la somme cumulée précédant un lot. Une journée
# suffit au cas courant (on réimporte l'heure en cours, la précédente est juste
# là) ; au-delà on retombe sur la dernière ligne connue, quel que soit le trou.
_LOOKBACK: Final = timedelta(days=1)


def statistic_id(person: str, key: str) -> str:
    """Rend l'identifiant de statistique externe d'une personne et d'une métrique."""
    return f"{DOMAIN}:{slugify(f'{person} {key}')}"


async def async_import_series(
    hass: HomeAssistant, person: str, series: list[Any]
) -> int:
    """Range des séries horaires et rend le nombre de points écrits.

    Lève `ValueError` sur une série malformée : le webhook la traduit en 400,
    l'app saura que le lot est à corriger et non à rejouer.
    """
    if "recorder" not in hass.config.components:
        _LOGGER.warning(
            "Recorder absent : les séries horaires de %s sont ignorées", person
        )
        return 0

    total_points = sum(
        len(serie["points"])
        for serie in series
        if isinstance(serie, dict) and isinstance(serie.get("points"), list)
    )
    if total_points > MAX_POINTS:
        raise ValueError(f"trop de points dans le lot ({total_points} > {MAX_POINTS})")

    imported = 0
    for serie in series:
        imported += await _import_serie(hass, person, serie)
    return imported


async def _import_serie(hass: HomeAssistant, person: str, serie: Any) -> int:
    """Range une série (une métrique) et rend son nombre de points."""
    if not isinstance(serie, dict):
        raise ValueError("chaque série doit être un objet JSON")

    key = serie.get("key")
    if not isinstance(key, str) or not key:
        raise ValueError("« key » manquant dans une série")

    kind = serie.get("kind", KIND_SUM)
    if kind not in (KIND_SUM, KIND_MEASUREMENT):
        raise ValueError(f"« kind » inconnu pour {key} : {kind!r}")

    raw_points = serie.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(f"« points » doit être une liste non vide pour {key}")

    # Tri chronologique : l'app envoie déjà dans l'ordre, mais la somme cumulée
    # en dépend, autant ne pas s'en remettre à sa bonne volonté.
    points = sorted(
        (_parse_point(key, kind, raw) for raw in raw_points), key=lambda p: p[0]
    )

    metadata = _metadata(person, key, serie.get("unit"), has_sum=kind == KIND_SUM)

    if kind == KIND_SUM:
        rows = await _sum_rows(hass, metadata["statistic_id"], points)
    else:
        rows = [StatisticData(start=start, **values) for start, values in points]

    async_add_external_statistics(hass, metadata, rows)
    return len(rows)


def _parse_point(key: str, kind: str, raw: Any) -> tuple[datetime, dict[str, float]]:
    """Vérifie un point et rend `(début de l'heure, valeurs)`."""
    if not isinstance(raw, dict):
        raise ValueError(f"chaque point de {key} doit être un objet JSON")

    start = dt_util.parse_datetime(raw.get("start") or "")
    if start is None:
        raise ValueError(f"« start » manquant ou illisible dans un point de {key}")
    start = dt_util.as_utc(start)
    if (start.minute, start.second, start.microsecond) != (0, 0, 0):
        # Home Assistant range les statistiques externes par heure pleine : un
        # point décalé serait rattaché à la mauvaise heure sans prévenir.
        raise ValueError(f"« start » doit être aligné sur l'heure pour {key} : {start}")

    if kind == KIND_SUM:
        return start, {"state": _number(key, raw, "value")}

    mean = _number(key, raw, "mean")
    return start, {
        "mean": mean,
        "min": _number(key, raw, "min", default=mean),
        "max": _number(key, raw, "max", default=mean),
    }


def _number(
    key: str, raw: dict[str, Any], field: str, *, default: float | None = None
) -> float:
    """Lit un champ numérique d'un point, avec un message d'erreur parlant."""
    value = raw.get(field)
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"« {field} » doit être un nombre dans un point de {key}")
    return float(value)


def _metadata(
    person: str, key: str, unit: Any, *, has_sum: bool
) -> StatisticMetaData:
    """Métadonnées de la statistique, calées sur le catalogue quand il connaît la clé.

    L'unité vient du catalogue en priorité : elle doit rester **stable** d'un
    import à l'autre, sinon Home Assistant considère que la série a changé de
    nature et refuse les nouvelles lignes.
    """
    description = METRIC_DESCRIPTIONS.get(key)
    if description is not None:
        name = description.name
        unit_of_measurement = description.native_unit_of_measurement
    else:
        name = key
        unit_of_measurement = unit if isinstance(unit, str) else None

    return StatisticMetaData(
        has_mean=not has_sum,
        has_sum=has_sum,
        name=f"{person} — {name}",
        source=DOMAIN,
        statistic_id=statistic_id(person, key),
        unit_of_measurement=unit_of_measurement,
    )


async def _sum_rows(
    hass: HomeAssistant,
    stat_id: str,
    points: list[tuple[datetime, dict[str, float]]],
) -> list[StatisticData]:
    """Cumule les totaux horaires en lignes de somme croissante.

    Home Assistant affiche l'écart entre deux `sum` consécutives : la somme doit
    donc croître d'une heure à l'autre, et repartir de ce qui a déjà été importé.
    """
    running = await _previous_sum(hass, stat_id, points[0][0])
    rows: list[StatisticData] = []
    for start, values in points:
        running += values["state"]
        rows.append(StatisticData(start=start, state=values["state"], sum=running))
    return rows


async def _previous_sum(hass: HomeAssistant, stat_id: str, before: datetime) -> float:
    """Somme cumulée de la dernière heure importée strictement avant `before`."""
    recorder = get_instance(hass)

    rows = await recorder.async_add_executor_job(
        partial(
            statistics_during_period,
            hass,
            before - _LOOKBACK,
            before,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
    )
    existing = rows.get(stat_id)
    if existing:
        return float(existing[-1].get("sum") or 0.0)

    # Rien dans la fenêtre : soit la série démarre, soit l'app a été muette
    # longtemps. On reprend alors la dernière ligne connue — sauf si elle est
    # postérieure, cas d'un rattrapage d'historique ancien qui repart de zéro.
    rows = await recorder.async_add_executor_job(
        partial(
            statistics_during_period,
            hass,
            dt_util.utc_from_timestamp(0),
            before,
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
    )
    previous = rows.get(stat_id)
    return float(previous[-1].get("sum") or 0.0) if previous else 0.0
