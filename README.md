# CoachSanté — intégration Home Assistant

Intégration custom Home Assistant qui reçoit les données de santé (Apple Santé /
HealthKit) et les photos de repas envoyées par l'app iOS **CoachSanté**, et les
expose en entités exploitables par des automatisations (analyse nutritionnelle par
LLM, conseils de perte de poids).

Ce dépôt ne contient **que l'intégration Home Assistant**. L'app iOS qui alimente
le webhook est développée dans un dépôt séparé.

## Ce que fournit l'intégration

- **Webhook signé** (un par personne) : reçoit métriques santé, photos de repas et
  contexte nutritionnel, vérifie une signature HMAC-SHA256.
- **Capteurs santé** (`sensor.*`) : pas, dépense énergétique, fréquence cardiaque,
  sommeil, métriques de démarche… créés à la volée selon ce que l'app envoie.
- **Statistiques long terme** : le détail heure par heure des métriques
  quantitatives, importé avec ses vraies dates. Les capteurs ci-dessus portent
  l'état courant et font tourner les automatisations ; ces statistiques servent à
  analyser l'historique fin (panneau « Statistiques », carte graphique). Ce ne
  sont pas des entités : elles ne sont pas lisibles depuis un template.
- **Compteurs nutritionnels** cumulatifs sur la journée (kcal, protéines, glucides,
  lipides, fibres, sucres), remis à zéro à minuit heure locale
  (`state_class: total` + `last_reset`).
- **Entité image** (`image.*`) : dernière photo de repas, plus le fichier sur disque
  dans `/media/coachsante/<personne>/`. Le commentaire joint à la photo est repris
  en attribut et par le capteur `sensor.<personne>_note_du_dernier_repas`.
- **Contexte nutritionnel** : liens de recettes, textes et photos d'emballages
  envoyés depuis l'app, conservés 14 jours et assemblés en un bloc prêt à injecter
  dans le prompt qui estime les repas (`sensor.<personne>_contexte_nutrition`,
  attribut `prompt`). Les photos d'emballages sont décrites à réception par une
  automatisation, et la description rejoint le contexte.
- **Services** : `coachsante.add_nutrition` (macros calculées par une automatisation),
  `coachsante.reset_day`, `coachsante.add_context`, `coachsante.clear_context`.
- **Événements** : `coachsante_meal_photo`, `coachsante_metrics`,
  `coachsante_nutrition`, `coachsante_context`, `coachsante_context_photo` — points
  d'accroche des automatisations.

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
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> contexte "https://cookidoo.fr/…"
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> contexte-photo emballage.jpg
```

## Exemples d'automatisations

L'intégration fournit les entrées (events, entité `image`) et reçoit les sorties
(`coachsante.add_nutrition`, `coachsante.add_context`), mais ne contient aucune
logique d'analyse : celle-ci vit dans tes automatisations. Des exemples prêts à
adapter sont dans [docs/automatisations/](docs/automatisations/) — l'analyse d'une
photo de repas par un LLM, qui remplit les compteurs nutritionnels du jour, et la
description des photos de contexte, qui alimente ce que le modèle sait avant
d'estimer.

## Protocole

Le format des charges utiles du webhook (métriques, séries horaires, photo,
contexte, signature) est
décrit dans [docs/protocole-webhook.md](docs/protocole-webhook.md). C'est le contrat
que l'app iOS doit respecter.

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

### Publier une version

**HACS suit les releases GitHub, pas le `manifest.json` de la branche.** Monter
la version dans le manifeste ne suffit donc pas : tant qu'aucune release n'est
créée, HACS continue de proposer la précédente. Rien n'automatise ça.

```bash
git tag -a v0.4.0 -m "Résumé en une ligne"
git push origin v0.4.0
gh release create v0.4.0 --title "v0.4.0 — …" --verify-tag --latest --notes "…"
```

Le tag doit porter sur un commit dont le `manifest.json` annonce la même version.

## Licence

MIT — voir [LICENSE](LICENSE).
