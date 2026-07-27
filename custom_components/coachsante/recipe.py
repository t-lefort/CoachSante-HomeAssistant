"""Extraction des données structurées d'un lien de recette."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 3


@dataclass(frozen=True, slots=True)
class RecipeSummary:
    """Recette directement utilisable dans le contexte nutritionnel."""

    name: str
    text: str


async def async_extract_recipe(hass: HomeAssistant, url: str) -> RecipeSummary | None:
    """Télécharge une URL publique HTTPS et extrait son JSON-LD `Recipe`."""
    current = url
    session = async_get_clientsession(hass)

    try:
        for _ in range(MAX_REDIRECTS + 1):
            _validate_url(current)
            async with asyncio.timeout(12):
                response = await session.get(
                    current,
                    allow_redirects=False,
                    headers={"User-Agent": "CoachSante/0.6 (+Home Assistant)"},
                )
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    _LOGGER.warning("Recette %s inaccessible : HTTP %s", current, response.status)
                    return None
                if "text/html" not in response.headers.get("Content-Type", ""):
                    return None
                raw = await response.content.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    _LOGGER.warning("Page de recette trop volumineuse : %s", current)
                    return None
                return extract_recipe(raw.decode(response.charset or "utf-8", errors="replace"))
    except (TimeoutError, ValueError, OSError) as err:
        _LOGGER.warning("Impossible d'analyser la recette %s : %s", url, err)
    return None


def extract_recipe(page: str) -> RecipeSummary | None:
    """Extrait la première recette JSON-LD d'une page HTML."""
    parser = _JsonLdParser()
    parser.feed(page)
    for block in parser.blocks:
        try:
            document = json.loads(unescape(block))
        except (json.JSONDecodeError, TypeError):
            continue
        for candidate in _walk_json(document):
            if _is_recipe(candidate):
                return _summarize(candidate)
    return None


def _summarize(recipe: dict[str, Any]) -> RecipeSummary | None:
    name = recipe.get("name")
    ingredients = recipe.get("recipeIngredient")
    if not isinstance(name, str) or not isinstance(ingredients, list):
        return None

    lines = [f"Recette : {name}"]
    if recipe_yield := _read_text(recipe.get("recipeYield")):
        lines.append(f"Rendement : {recipe_yield}")
    lines.append("Ingrédients : " + "; ".join(str(item) for item in ingredients if item))

    nutrition = recipe.get("nutrition")
    if isinstance(nutrition, dict):
        fields = (
            ("calories", "énergie"),
            ("proteinContent", "protéines"),
            ("carbohydrateContent", "glucides"),
            ("fatContent", "lipides"),
            ("fiberContent", "fibres"),
            ("sugarContent", "sucres"),
        )
        values = [
            f"{label} {_read_text(nutrition.get(key))}"
            for key, label in fields
            if _read_text(nutrition.get(key))
        ]
        if values:
            lines.append("Nutrition par portion : " + ", ".join(values))
    return RecipeSummary(name=name, text="\n".join(lines))


def _read_text(value: Any) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_recipe(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = value.get("@type")
    return kind == "Recipe" or isinstance(kind, list) and "Recipe" in kind


def _walk_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [child for item in value for child in _walk_json(item)]
    if not isinstance(value, dict):
        return [value]
    children = [value]
    for key in ("@graph", "mainEntity", "itemListElement"):
        if key in value:
            children.extend(_walk_json(value[key]))
    return children


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("seules les URL HTTPS publiques sont acceptées")
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("adresse locale refusée")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("adresse privée refusée")


class _JsonLdParser(HTMLParser):
    """Collecte le contenu des balises JSON-LD sans dépendance HTML externe."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._parts is not None:
            self.blocks.append("".join(self._parts))
            self._parts = None
