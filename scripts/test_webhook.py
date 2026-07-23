#!/usr/bin/env python3
"""Envoie une charge utile signée au webhook CoachSanté.

Sert à valider l'intégration Home Assistant avant que l'app iOS existe.

    python scripts/test_webhook.py <url> <secret> metrics
    python scripts/test_webhook.py <url> <secret> photo repas.jpg
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

EXEMPLE_METRIQUES = [
    {"key": "steps", "value": 8421, "unit": "pas", "source": "test_webhook.py"},
    {"key": "active_energy", "value": 512, "unit": "kcal", "source": "test_webhook.py"},
    {"key": "resting_heart_rate", "value": 54, "unit": "bpm", "source": "test_webhook.py"},
    {"key": "sleep_duration", "value": 7.25, "unit": "h", "source": "test_webhook.py"},
    {"key": "walking_asymmetry", "value": 1.8, "unit": "%", "source": "test_webhook.py"},
]


def build_payload(mode: str, argument: str | None) -> dict:
    """Construit la charge utile correspondant au mode demandé."""
    if mode == "metrics":
        return {"type": "metrics", "metrics": EXEMPLE_METRIQUES}

    if mode == "photo":
        if argument is None:
            raise SystemExit("Le mode « photo » attend un chemin de fichier.")
        path = Path(argument)
        content_type = MIME_BY_SUFFIX.get(path.suffix.lower())
        if content_type is None:
            raise SystemExit(f"Extension non supportée : {path.suffix!r} (attendu .jpg ou .png)")
        return {
            "type": "meal_photo",
            "note": "envoi de test",
            "photo": {
                "content_type": content_type,
                "data": base64.b64encode(path.read_bytes()).decode(),
            },
        }

    raise SystemExit(f"Mode inconnu : {mode!r} (attendu « metrics » ou « photo »)")


def main() -> int:
    """Signe et envoie la requête, puis affiche la réponse."""
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)

    url, secret, mode = sys.argv[1:4]
    argument = sys.argv[4] if len(sys.argv) > 4 else None

    body = json.dumps(build_payload(mode, argument)).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    request = Request(  # noqa: S310 — URL fournie par l'utilisateur, usage local
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CoachSante-Signature": f"sha256={signature}",
        },
    )

    print(f"POST {url} ({len(body)} octets)")
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            print(f"→ {response.status} {response.read().decode()}")
    except HTTPError as err:
        print(f"→ {err.code} {err.read().decode()}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
