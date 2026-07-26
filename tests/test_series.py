"""Séries horaires : import dans les statistiques long terme, cumul, validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.coachsante.series import statistic_id

from .conftest import WEBHOOK_ID, encode, sign

URL = f"/api/webhook/{WEBHOOK_ID}"

# Base de temps fixe et alignée sur l'heure : les statistiques externes sont
# rangées par heure pleine, un décalage fausserait toutes les comparaisons.
BASE = datetime(2026, 7, 20, 6, 0, tzinfo=UTC)

STAT_STEPS = statistic_id("Thomas", "steps")
STAT_HEART = statistic_id("Thomas", "heart_rate")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Any, enable_custom_integrations: Any
) -> None:
    """Fait tourner tout le module avec le recorder : sans lui, pas de statistiques.

    Surcharge la fixture homonyme de `conftest.py` pour glisser `recorder_mock`
    **devant** elle : le harness refuse de préparer la base du recorder si Home
    Assistant a déjà démarré, et la version du conftest amorce `hass` la première.
    """
    return


@pytest.fixture
async def client(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth: ClientSessionGenerator,
) -> Any:
    """Client HTTP avec l'entrée « Thomas » configurée et le webhook en place."""
    return await hass_client_no_auth()


def _hours(*values: float, start: datetime = BASE) -> list[dict[str, Any]]:
    """Points horaires consécutifs à partir de `start`."""
    return [
        {"start": (start + timedelta(hours=index)).isoformat(), "value": value}
        for index, value in enumerate(values)
    ]


async def _post(client: Any, series: list[dict[str, Any]]) -> Any:
    """Envoie un lot de séries signé."""
    payload = {
        "type": "series",
        "sent_at": dt_util.utcnow().isoformat(),
        "series": series,
    }
    body = encode(payload)
    return await client.post(URL, data=body, headers=sign(body))


async def _read(
    hass: HomeAssistant, stat: str, types: set[str] | None = None
) -> list[dict[str, Any]]:
    """Relit les statistiques importées pour un identifiant."""
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        BASE - timedelta(days=1),
        BASE + timedelta(days=2),
        {stat},
        "hour",
        None,
        types or {"state", "sum"},
    )
    return rows.get(stat, [])


# --- Import nominal --------------------------------------------------------


async def test_serie_cumulative_importee_avec_somme_croissante(
    hass: HomeAssistant, client: Any
) -> None:
    """Les totaux horaires deviennent une somme cumulée croissante."""
    response = await _post(
        client,
        [{"key": "steps", "unit": "pas", "kind": "sum", "points": _hours(100, 250, 40)}],
    )
    assert response.status == 200
    assert (await response.json())["imported"] == 3

    rows = await _read(hass, STAT_STEPS)
    assert [row["state"] for row in rows] == [100.0, 250.0, 40.0]
    # La somme cumulée, elle, ne redescend jamais : c'est son écart d'une heure
    # à l'autre que Home Assistant affiche.
    assert [row["sum"] for row in rows] == [100.0, 350.0, 390.0]


async def test_serie_de_mesure_importee_en_moyenne_min_max(
    hass: HomeAssistant, client: Any
) -> None:
    """Une série « measurement » remplit moyenne, minimum et maximum."""
    points = [
        {"start": BASE.isoformat(), "mean": 68.5, "min": 54, "max": 112},
        {"start": (BASE + timedelta(hours=1)).isoformat(), "mean": 71.0},
    ]
    response = await _post(
        client, [{"key": "heart_rate", "kind": "measurement", "points": points}]
    )
    assert response.status == 200

    rows = await _read(hass, STAT_HEART, types={"mean", "min", "max"})
    assert [row["mean"] for row in rows] == [68.5, 71.0]
    # Minimum et maximum absents retombent sur la moyenne plutôt que d'échouer.
    assert [row["min"] for row in rows] == [54.0, 71.0]
    assert [row["max"] for row in rows] == [112.0, 71.0]


async def test_plusieurs_series_dans_un_lot(hass: HomeAssistant, client: Any) -> None:
    """Un seul envoi peut porter plusieurs métriques."""
    response = await _post(
        client,
        [
            {"key": "steps", "kind": "sum", "points": _hours(10, 20)},
            {
                "key": "heart_rate",
                "kind": "measurement",
                "points": [{"start": BASE.isoformat(), "mean": 60}],
            },
        ],
    )
    assert response.status == 200
    assert (await response.json())["imported"] == 3
    assert len(await _read(hass, STAT_STEPS)) == 2
    assert len(await _read(hass, STAT_HEART, types={"mean"})) == 1


# --- Reprise et réimport ---------------------------------------------------


async def test_lot_suivant_reprend_la_somme_precedente(
    hass: HomeAssistant, client: Any
) -> None:
    """Un envoi ultérieur continue le cumul au lieu de repartir de zéro."""
    await _post(client, [{"key": "steps", "kind": "sum", "points": _hours(100, 200)}])
    await async_wait_recording_done(hass)

    await _post(
        client,
        [
            {
                "key": "steps",
                "kind": "sum",
                "points": _hours(50, start=BASE + timedelta(hours=2)),
            }
        ],
    )

    rows = await _read(hass, STAT_STEPS)
    assert [row["sum"] for row in rows] == [100.0, 300.0, 350.0]


async def test_reimport_de_la_derniere_heure_corrige_sans_doubler(
    hass: HomeAssistant, client: Any
) -> None:
    """Réenvoyer l'heure en cours l'écrase — c'est ce qui la complète au fil du jour."""
    await _post(client, [{"key": "steps", "kind": "sum", "points": _hours(100, 30)}])
    await async_wait_recording_done(hass)

    # Même heure, valeur complétée : 30 pas à la volée, 180 une fois l'heure finie.
    await _post(
        client,
        [
            {
                "key": "steps",
                "kind": "sum",
                "points": _hours(180, start=BASE + timedelta(hours=1)),
            }
        ],
    )

    rows = await _read(hass, STAT_STEPS)
    assert len(rows) == 2
    assert [row["state"] for row in rows] == [100.0, 180.0]
    # Le cumul repart de l'heure précédente, il n'additionne pas les deux envois.
    assert [row["sum"] for row in rows] == [100.0, 280.0]


async def test_trou_long_ne_fait_pas_redescendre_la_somme(
    hass: HomeAssistant, client: Any
) -> None:
    """Après plusieurs jours sans envoi, le cumul reprend là où il s'était arrêté."""
    await _post(client, [{"key": "steps", "kind": "sum", "points": _hours(500)}])
    await async_wait_recording_done(hass)

    await _post(
        client,
        [
            {
                "key": "steps",
                "kind": "sum",
                "points": _hours(70, start=BASE + timedelta(days=1, hours=5)),
            }
        ],
    )

    rows = await _read(hass, STAT_STEPS)
    assert [row["sum"] for row in rows] == [500.0, 570.0]


# --- Validation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("series", "motif"),
    [
        ("pas une liste", "liste"),
        ([{"points": _hours(1)}], "key"),
        ([{"key": "steps", "kind": "bidule", "points": _hours(1)}], "kind"),
        ([{"key": "steps", "points": []}], "points"),
        ([{"key": "steps", "points": [{"value": 1}]}], "start"),
        ([{"key": "steps", "points": [{"start": "2026-07-20T06:30:00Z", "value": 1}]}], "aligné"),
        ([{"key": "steps", "points": [{"start": BASE.isoformat()}]}], "value"),
    ],
)
async def test_serie_malformee_refusee(
    client: Any, series: Any, motif: str
) -> None:
    """Un lot malformé part en 400 : le rejouer n'y changerait rien."""
    response = await _post(client, series)
    assert response.status == 400
    assert motif in await response.text()


async def test_lot_trop_gros_refuse(client: Any) -> None:
    """Le nombre de points d'un lot est borné."""
    points = [
        {"start": (BASE + timedelta(hours=index)).isoformat(), "value": 1}
        for index in range(5001)
    ]
    response = await _post(client, [{"key": "steps", "kind": "sum", "points": points}])
    assert response.status == 400
    assert "trop de points" in await response.text()


async def test_cle_hors_catalogue_acceptee(hass: HomeAssistant, client: Any) -> None:
    """Une clé inconnue passe quand même, avec l'unité fournie par l'app."""
    response = await _post(
        client,
        [{"key": "grip_strength", "unit": "kg", "kind": "sum", "points": _hours(12)}],
    )
    assert response.status == 200
    assert len(await _read(hass, statistic_id("Thomas", "grip_strength"))) == 1
