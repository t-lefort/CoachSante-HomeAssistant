"""Couche de données : nutrition, changement de jour, persistance, photos."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.coachsante.data import CoachSanteData


def _today() -> str:
    return dt_util.now().date().isoformat()


def _yesterday() -> str:
    return (dt_util.now().date() - timedelta(days=1)).isoformat()


async def _fresh_data(hass: HomeAssistant, mock_entry: MockConfigEntry) -> CoachSanteData:
    mock_entry.add_to_hass(hass)
    data = CoachSanteData(hass, mock_entry)
    await data.async_load()
    return data


# --- Nutrition -------------------------------------------------------------


async def test_add_nutrition_cumule(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Deux repas s'additionnent dans les compteurs du jour."""
    data = await _fresh_data(hass, mock_entry)
    data.add_nutrition({"energy_kcal": 200.0, "protein_g": 10.0}, "petit-déj")
    data.add_nutrition({"energy_kcal": 300.0}, "déjeuner")

    assert data.nutrition["energy_kcal"] == 500.0
    assert data.nutrition["protein_g"] == 10.0
    assert data.last_meal["label"] == "déjeuner"
    assert len(data.meals) == 2


async def test_reset_day(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    """`reset_day` remet les compteurs et la liste des repas à zéro."""
    data = await _fresh_data(hass, mock_entry)
    data.add_nutrition({"energy_kcal": 800.0}, "gros repas")

    data.reset_day()

    assert data.nutrition["energy_kcal"] == 0.0
    assert data.meals == []
    assert data.nutrition_day == _today()


async def test_roll_over_day(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    """Au passage de minuit, les compteurs d'hier sont remis à zéro."""
    data = await _fresh_data(hass, mock_entry)
    data.add_nutrition({"energy_kcal": 800.0}, "hier soir")
    # On fait comme si les compteurs dataient d'hier.
    data.nutrition_day = _yesterday()

    assert data.roll_over_day() is True
    assert data.nutrition["energy_kcal"] == 0.0
    assert data.meals == []
    assert data.nutrition_day == _today()

    # Rappelé le même jour, plus rien à faire.
    assert data.roll_over_day() is False


async def test_nutrition_last_reset(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """`last_reset` du capteur nutrition pointe sur le minuit local du jour courant."""
    from custom_components.coachsante.metrics import NUTRITION_DESCRIPTIONS
    from custom_components.coachsante.sensor import CoachSanteNutritionSensor

    data = await _fresh_data(hass, mock_entry)
    sensor = CoachSanteNutritionSensor(data, NUTRITION_DESCRIPTIONS["energy_kcal"])

    expected = dt_util.start_of_local_day(date.fromisoformat(data.nutrition_day))
    assert sensor.last_reset == expected


# --- Métriques « somme du jour » au changement de jour ---------------------


async def test_reset_stale_daily_metrics(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Seuls les compteurs « somme du jour » d'hier retombent à zéro."""
    data = await _fresh_data(hass, mock_entry)
    data.apply_metrics(
        [
            {"key": "steps", "value": 12000, "day": _yesterday()},
            {"key": "heart_rate", "value": 60, "day": _yesterday()},
            {"key": "steps_sans_jour", "value": 1},
        ]
    )

    reset = data.reset_stale_daily_metrics()

    assert "steps" in reset
    assert data.metrics["steps"].value == 0
    assert data.metrics["steps"].day == _today()

    # « dernier » : la fréquence cardiaque garde sa valeur.
    assert "heart_rate" not in reset
    assert data.metrics["heart_rate"].value == 60


async def test_daily_metrics_du_jour_non_touchees(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Un compteur déjà daté d'aujourd'hui n'est pas remis à zéro."""
    data = await _fresh_data(hass, mock_entry)
    data.apply_metrics([{"key": "steps", "value": 5000, "day": _today()}])

    assert data.reset_stale_daily_metrics() == []
    assert data.metrics["steps"].value == 5000


# --- Persistance -----------------------------------------------------------


async def test_persistance_rechargement(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Après sauvegarde et redémarrage, l'état est rechargé depuis le store."""
    data = await _fresh_data(hass, mock_entry)
    data.apply_metrics([{"key": "steps", "value": 8421, "day": _today()}])
    data.add_nutrition({"energy_kcal": 500.0, "protein_g": 30.0}, "déjeuner")
    await data._store.async_save(data._as_dict())

    # Nouveau cycle de vie (redémarrage HA) sur la même entrée.
    reloaded = CoachSanteData(hass, mock_entry)
    await reloaded.async_load()

    assert reloaded.metrics["steps"].value == 8421
    assert reloaded.nutrition["energy_kcal"] == 500.0
    assert reloaded.nutrition["protein_g"] == 30.0
    assert reloaded.last_meal["label"] == "déjeuner"


async def test_photo_supprimee_a_la_main(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Une photo effacée du disque entre deux démarrages n'est plus référencée."""
    data = await _fresh_data(hass, mock_entry)
    path = await data.async_save_photo(
        b"jpeg", content_type="image/jpeg", note=None, taken_at=dt_util.now()
    )
    assert Path(path).is_file()
    await data._store.async_save(data._as_dict())

    Path(path).unlink()  # suppression manuelle hors HA

    reloaded = CoachSanteData(hass, mock_entry)
    await reloaded.async_load()
    assert reloaded.photo_path is None
    assert reloaded.photo_updated is None


# --- Collision de nom de photo ---------------------------------------------


async def test_photo_collision_meme_seconde(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Deux photos prises la même seconde ne s'écrasent pas."""
    data = await _fresh_data(hass, mock_entry)
    taken = dt_util.now()

    p1 = await data.async_save_photo(
        b"premiere", content_type="image/jpeg", note=None, taken_at=taken
    )
    p2 = await data.async_save_photo(
        b"seconde", content_type="image/jpeg", note=None, taken_at=taken
    )

    assert p1 != p2
    assert Path(p1).read_bytes() == b"premiere"
    assert Path(p2).read_bytes() == b"seconde"
    assert data.photo_path == str(p2)
