# CoachSanté — intégration Home Assistant

Intégration custom Home Assistant qui reçoit les données de santé (Apple Santé /
HealthKit) et les photos de repas envoyées par l'app iOS **CoachSanté**, et les
expose en entités exploitables par des automatisations (analyse nutritionnelle par
LLM, conseils de perte de poids).

Ce dépôt ne contient **que l'intégration Home Assistant**. L'app iOS qui alimente
le webhook est développée dans un dépôt séparé.

## Ce que fournit l'intégration

- **Webhook signé** (un par personne) : reçoit métriques santé et photos de repas,
  vérifie une signature HMAC-SHA256.
- **Capteurs santé** (`sensor.*`) : pas, dépense énergétique, fréquence cardiaque,
  sommeil, métriques de démarche… créés à la volée selon ce que l'app envoie.
- **Compteurs nutritionnels** cumulatifs sur la journée (kcal, protéines, glucides,
  lipides, fibres, sucres), remis à zéro à minuit heure locale
  (`state_class: total` + `last_reset`).
- **Entité image** (`image.*`) : dernière photo de repas, plus le fichier sur disque
  dans `/media/coachsante/<personne>/`.
- **Services** : `coachsante.add_nutrition` (macros calculées par une automatisation)
  et `coachsante.reset_day`.
- **Événements** : `coachsante_meal_photo`, `coachsante_metrics`,
  `coachsante_nutrition` — points d'accroche des automatisations.

Plusieurs personnes peuvent coexister : une config entry = une personne = un device
= un webhook, sans partage de données.

## Installation via HACS (recommandé)

1. HACS → menu ⋮ → **Dépôts personnalisés**.
2. Ajouter `https://github.com/t-lefort/CoachSante-HomeAssistant`, catégorie
   **Intégration**.
3. Installer **CoachSanté**, puis redémarrer Home Assistant.
4. **Paramètres → Appareils et services → Ajouter une intégration → CoachSanté**.

Les mises à jour suivantes se font directement depuis HACS.

## Installation manuelle

Copier `custom_components/coachsante/` dans le dossier `custom_components/` de votre
configuration Home Assistant, puis redémarrer.

## Configuration

Le config flow demande un nom de personne, génère un `webhook_id` et un secret HMAC,
et les affiche en fin de flow pour être recopiés dans l'app iOS —
**le secret n'est montré qu'une seule fois**.

Pour vérifier que le webhook répond avant même d'avoir l'app :

```bash
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> metrics
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> photo repas.jpg
```

## Protocole

Le format des charges utiles du webhook (métriques, photo, signature) est décrit dans
[docs/protocole-webhook.md](docs/protocole-webhook.md). C'est le contrat que l'app iOS
doit respecter.

## Icône

L'icône de l'intégration est embarquée dans `custom_components/coachsante/brand/`
(`icon.png` 256², `icon@2x.png` 512², fond transparent). Depuis Home Assistant
2026.3, ces images locales sont servies en priorité (config flow, page de
l'intégration, appareils) — aucune soumission au dépôt `home-assistant/brands`
n'est nécessaire. Pour les régénérer (nécessite un Mac avec Swift) :

```bash
swift scripts/make_ha_icon.swift 512 custom_components/coachsante/brand/icon@2x.png
swift scripts/make_ha_icon.swift 256 custom_components/coachsante/brand/icon.png
```

## Développement

```bash
ruff check custom_components

python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

La suite pytest (`tests/`) s'appuie sur `pytest-homeassistant-custom-component` :
config flow, webhook (signature, tailles, types), nutrition et persistance.
Home Assistant tourne en Python 3.13 ; le code cible cette version.

## Licence

MIT — voir [LICENSE](LICENSE).
