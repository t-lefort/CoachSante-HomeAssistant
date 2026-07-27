# Protocole webhook CoachSanté

Contrat entre l'app iOS et l'intégration Home Assistant. Toute évolution ici doit
être répercutée des deux côtés.

## Endpoint

```
POST https://<votre-domaine>/api/webhook/<webhook_id>
Content-Type: application/json
X-CoachSante-Signature: sha256=<hmac_sha256(secret, corps_brut)>
```

L'`webhook_id` et le `secret` sont générés par le config flow au moment d'ajouter
la personne, et affichés une seule fois. Une personne = un webhook = un secret.

La signature est calculée sur les **octets bruts du corps**, avant tout
réencodage. En Swift :

```swift
let signature = HMAC<SHA256>.authenticationCode(for: bodyData, using: key)
request.setValue("sha256=" + signature.map { String(format: "%02x", $0) }.joined(),
                 forHTTPHeaderField: "X-CoachSante-Signature")
```

Corps limité à 12 Mo. Seul `POST` est accepté.

## Réponses

| Code | Sens |
|---|---|
| 200 | Accepté — `{"ok": true, …}` |
| 400 | JSON invalide, type inconnu, base64 cassé |
| 401 | Signature absente ou fausse |
| 413 | Corps trop volumineux |

Seul un **200** autorise l'app à retirer le lot de sa file d'attente. Tout le
reste doit être rejoué (avec backoff), sauf 400 et 401 qui sont des erreurs
définitives : rejouer ne changera rien, il faut les journaliser et jeter le lot.

## Anti-rejeu (`sent_at`)

Chaque payload porte un champ `sent_at` (ISO 8601). L'intégration refuse par un
**400** tout payload dont le `sent_at` est plus vieux que **5 minutes** : un
attaquant qui rejouerait une requête capturée sur le réseau tombe hors de la
fenêtre, et sa copie exacte des octets ne peut pas être re-signée.

Conséquence côté app : **`sent_at` est l'instant de *cette tentative d'envoi*, pas
celui de la mise en file.** À chaque retry, l'app re-date `sent_at` **et re-signe**
le corps. Un lot resté longtemps dans la file (réseau coupé) part donc avec un
`sent_at` frais et n'est jamais rejeté à tort.

`sent_at` absent ou illisible n'est pas bloquant (rétro-compatibilité) ; un
`sent_at` légèrement dans le futur (dérive d'horloge) est accepté.

## Type `metrics` — données santé

```json
{
  "type": "metrics",
  "sent_at": "2026-07-22T10:15:00Z",
  "metrics": [
    {
      "key": "steps",
      "value": 8421,
      "unit": "pas",
      "day": "2026-07-22",
      "updated_at": "2026-07-22T10:14:00Z",
      "source": "iPhone"
    }
  ]
}
```

Seuls `key` et `value` sont obligatoires ; les autres champs finissent en
attributs d'entité. Une clé absente du catalogue crée quand même une entité
générique — rien n'est perdu.

Le champ `day` (jour concerné, `AAAA-MM-JJ`) sert au **changement de jour**. À
minuit — et à tout redémarrage de HA franchissant minuit — les métriques de type
« somme » (`state_class: total_increasing` : `steps`, `active_energy`…) dont le
`day` est antérieur à aujourd'hui **retombent à zéro**. Sans ça, `steps`
afficherait le total d'hier jusqu'au premier envoi du matin, ce qui trompe une
automatisation lue juste après minuit. Les métriques « dernier » (poids,
fréquence cardiaque, VO2 max…) gardent au contraire leur dernière valeur.
Renseignez donc `day` sur les métriques cumulatives.

**L'app envoie des valeurs déjà agrégées.** C'est délibéré : HealthKit
dédoublonne les échantillons qui arrivent en double de l'iPhone et de l'Apple
Watch, mais uniquement au travers de `HKStatisticsQuery`. Envoyer les
échantillons bruts compterait les pas deux fois.

### Catalogue des clés

`somme` = total du jour (`state_class: total_increasing`), `dernier` = valeur la
plus récente.

| Clé | Type HealthKit | Unité attendue | Agrégation |
|---|---|---|---|
| `steps` | `StepCount` | pas | somme |
| `distance_walking_running` | `DistanceWalkingRunning` | km | somme |
| `distance_cycling` | `DistanceCycling` | km | somme |
| `distance_swimming` | `DistanceSwimming` | m | somme |
| `flights_climbed` | `FlightsClimbed` | étages | somme |
| `active_energy` | `ActiveEnergyBurned` | kcal | somme |
| `basal_energy` | `BasalEnergyBurned` | kcal | somme |
| `exercise_minutes` | `AppleExerciseTime` | min | somme |
| `stand_hours` | `AppleStandHour` | h | somme |
| `workout_count` | `HKWorkoutType` | séances | somme |
| `workout_minutes` | `HKWorkoutType` | min | somme |
| `heart_rate` | `HeartRate` | bpm | dernier |
| `resting_heart_rate` | `RestingHeartRate` | bpm | dernier |
| `walking_heart_rate` | `WalkingHeartRateAverage` | bpm | dernier |
| `heart_rate_variability` | `HeartRateVariabilitySDNN` | ms | dernier |
| `vo2_max` | `VO2Max` | mL/kg/min | dernier |
| `blood_oxygen` | `OxygenSaturation` | % | dernier |
| `respiratory_rate` | `RespiratoryRate` | resp/min | dernier |
| `body_mass` | `BodyMass` | kg | dernier |
| `body_fat` | `BodyFatPercentage` | % | dernier |
| `lean_body_mass` | `LeanBodyMass` | kg | dernier |
| `bmi` | `BodyMassIndex` | — | dernier |
| `height` | `Height` | cm | dernier |
| `waist_circumference` | `WaistCircumference` | cm | dernier |
| `sleep_duration` | `SleepAnalysis` (tous états endormis) | h | nuit |
| `sleep_deep` | `SleepAnalysis` `.asleepDeep` | h | nuit |
| `sleep_rem` | `SleepAnalysis` `.asleepREM` | h | nuit |
| `sleep_core` | `SleepAnalysis` `.asleepCore` | h | nuit |
| `sleep_awake` | `SleepAnalysis` `.awake` | h | nuit |
| `time_in_bed` | `SleepAnalysis` `.inBed` | h | nuit |
| `walking_speed` | `WalkingSpeed` | km/h | dernier |
| `walking_step_length` | `WalkingStepLength` | cm | dernier |
| `walking_asymmetry` | `WalkingAsymmetryPercentage` | % | dernier |
| `walking_double_support` | `WalkingDoubleSupportPercentage` | % | dernier |
| `stair_ascent_speed` | `StairAscentSpeed` | m/s | dernier |
| `stair_descent_speed` | `StairDescentSpeed` | m/s | dernier |
| `six_minute_walk_distance` | `SixMinuteWalkTestDistance` | m | dernier |
| `mindful_minutes` | `MindfulSession` | min | somme |
| `water` | `DietaryWater` | L | somme |
| `dietary_energy` | `DietaryEnergyConsumed` | kcal | somme |
| `daylight_minutes` | `TimeInDaylight` | min | somme |
| `audio_exposure_environment` | `EnvironmentalAudioExposure` | dB | dernier |
| `audio_exposure_headphones` | `HeadphoneAudioExposure` | dB | dernier |

⚠️ **Pièges de conversion.** HealthKit renvoie `OxygenSaturation`,
`BodyFatPercentage`, `WalkingAsymmetryPercentage` et
`WalkingDoubleSupportPercentage` sous forme de **fraction entre 0 et 1** : il faut
multiplier par 100 côté app. De même `WalkingSpeed` est en m/s alors que
l'intégration attend des km/h. Les unités du tableau font foi — l'intégration ne
convertit rien.

Le poids reste porté par l'**intégration Withings officielle**. `body_mass` n'est
là que pour d'éventuelles saisies manuelles dans Apple Santé.

## Type `goal` — objectif personnel

```json
{
  "type": "goal",
  "sent_at": "2026-07-27T18:15:00Z",
  "goal": "Perdre 5 kg progressivement tout en gardant ma force"
}
```

`goal` est une chaîne de 255 caractères maximum. Une chaîne vide efface
l’objectif. Home Assistant l’expose dans le capteur « Objectif » de la personne,
afin que les automatisations de conseil puissent l’utiliser.

## Type `series` — détail horaire

```json
{
  "type": "series",
  "sent_at": "2026-07-22T10:15:00Z",
  "series": [
    {
      "key": "steps",
      "unit": "pas",
      "kind": "sum",
      "points": [
        {"start": "2026-07-22T08:00:00Z", "value": 812},
        {"start": "2026-07-22T09:00:00Z", "value": 1340}
      ]
    },
    {
      "key": "heart_rate",
      "unit": "bpm",
      "kind": "measurement",
      "points": [
        {"start": "2026-07-22T08:00:00Z", "mean": 68.2, "min": 54, "max": 112}
      ]
    }
  ]
}
```

**Ce type ne touche pas aux sensors.** Il alimente les *statistiques long terme*
de Home Assistant, et rien d'autre : ni entité, ni événement, ni automatisation.
C'est le partage voulu — les sensors du type `metrics` portent l'état courant et
font tourner les automatisations, les séries servent à analyser après coup.

La raison est structurelle : l'état d'un sensor est toujours horodaté
**« maintenant »**, le recorder n'accepte pas d'état antidaté. Envoyer douze
points d'un coup à 14 h écrirait douze états datés 14 h. Les statistiques long
terme, elles, acceptent des points librement datés — c'est le mécanisme qu'utilisent
les intégrations d'énergie pour importer un relevé de compteur historique.

| Champ | Rôle |
|---|---|
| `key` | Clé du catalogue ci-dessus. Une clé inconnue est acceptée, avec l'`unit` fournie. |
| `unit` | Reprise seulement si la clé est hors catalogue ; sinon l'unité du catalogue prime. |
| `kind` | `sum` (total de l'heure : `value`) ou `measurement` (`mean`, et facultativement `min`/`max`, qui retombent sur `mean`). Défaut : `sum`. |
| `points[].start` | Début de l'heure, **aligné sur l'heure pleine UTC**. Un point décalé fait rejeter le lot en 400. |

Autres règles :

- **Réimporter une heure l'écrase.** L'heure en cours part forcément partielle ;
  la renvoyer au passage suivant la complète, sans jamais rien additionner en
  double. L'app reprend donc toujours à la dernière heure déjà envoyée, incluse.
- **Une série n'est jamais coupée entre deux envois.** Côté HA, la somme cumulée
  d'un lot se calcule à partir de ce qui est déjà en base ; une série écrite en
  deux fois dont la première moitié n'est pas encore commitée donnerait un cumul
  faux. Les lots se remplissent par séries entières, 5 000 points maximum.
- **Une heure sans échantillon est omise**, pas envoyée à zéro : rien ne distingue
  « zéro pas » de « iPhone éteint ».
- Seuls les types **quantitatifs** produisent des séries. Sommeil, séances,
  heures debout et minutes de méditation restent des valeurs du jour.
- Sans `recorder` sur l'instance, le lot est **accepté et ignoré** (`imported: 0`) :
  le rejouer ne servirait à rien.

La réponse est `{"ok": true, "imported": <points écrits>}`.

## Type `meal_photo` — photo de repas

```json
{
  "type": "meal_photo",
  "sent_at": "2026-07-22T12:35:10Z",
  "taken_at": "2026-07-22T12:35:00+02:00",
  "note": "midi, au boulot",
  "photo": {
    "content_type": "image/jpeg",
    "data": "<base64>"
  }
}
```

`sent_at` sert l'anti-rejeu (voir plus haut) ; `taken_at` est l'instant de la
prise de vue, qui peut être bien plus ancien et sert à nommer le fichier.

`content_type` accepte `image/jpeg` et `image/png`. L'app doit **redimensionner
avant l'envoi** (côté long ~1600 px, JPEG qualité ~0,7) : une photo brute
d'iPhone dépasse allègrement la limite une fois encodée en base64.

`note` est le commentaire saisi dans l'app. Il ne sert pas qu'à décorer : il part
dans l'événement, il devient l'état du capteur `sensor.<prénom>_note_du_dernier_repas`
et un attribut de l'entité image, et il a vocation à être repris dans le prompt
d'analyse.

Effets côté Home Assistant :

1. La photo est écrite dans `<media>/coachsante/<prénom>/AAAA-MM-JJ_HHMMSS.jpg`.
2. L'entité `image.<prénom>_dernier_repas` est rafraîchie (attributs `note`,
   `chemin`, `media_content_id`).
3. Le capteur `sensor.<prénom>_note_du_dernier_repas` prend la valeur de `note`.
4. L'événement `coachsante_meal_photo` est émis :

```json
{
  "entry_id": "01J…",
  "person": "Thomas",
  "path": "/media/coachsante/thomas/2026-07-22_123500.jpg",
  "media_content_id": "media-source://media_source/local/coachsante/thomas/2026-07-22_123500.jpg",
  "image_entity_id": "image.thomas_dernier_repas",
  "note": "midi, au boulot",
  "taken_at": "2026-07-22T12:35:00+02:00",
  "context": "- 2026-07-24 18:30 : Recette du soir — https://cookidoo.fr/…\n- 2026-07-25 19:59 : Paquet de raviolis — 232 kcal / 100 g…"
}
```

C'est cet événement qui déclenche l'automatisation d'analyse. Elle renvoie ses
résultats par le service `coachsante.add_nutrition`, en passant `entry_id` tel
quel.

`context` est le contexte des deux dernières semaines, déjà assemblé (voir plus
bas) : l'automatisation n'a rien à aller chercher, elle le colle dans son prompt.
`media_content_id` est l'identifiant *media source* de la photo, à passer tel quel
en pièce jointe à `ai_task.generate_data` ; il vaut `null` si aucun dossier média
local n'est déclaré dans la configuration Home Assistant.

## Type `context` — contexte pour l'estimation

Ce qu'on veut que le modèle sache **avant** d'estimer un repas : un lien de
recette, le poids d'une portion, l'étiquette nutritionnelle d'un produit.

```json
{
  "type": "context",
  "sent_at": "2026-07-25T18:00:00Z",
  "captured_at": "2026-07-25T19:59:00+02:00",
  "label": "Paquet de raviolis",
  "text": "https://cookidoo.fr/recipes/recipe/fr-FR/r123456",
  "photo": {
    "content_type": "image/jpeg",
    "data": "<base64>"
  }
}
```

Au moins un de `text` et `photo` doit être présent — sinon **400**. `label` et
`captured_at` sont facultatifs (`captured_at` vaut par défaut l'instant de
réception). Les contraintes sur `photo` sont celles de `meal_photo` ; pour une
étiquette, l'app compresse moins (côté long ~2000 px, qualité ~0,85) : les petits
caractères doivent rester lisibles.

Réponse : `{"ok": true, "context_id": "3f9c1a7b2e4d6058", "path": "…"}` — `path`
vaut `null` en l'absence de photo.

Effets côté Home Assistant :

1. La photo éventuelle est écrite dans `<media>/coachsante/<prénom>/contexte/`.
2. L'élément entre dans le contexte de la personne, conservé **14 jours** (réglable
   dans les options de l'intégration ; 0 = pour toujours).
3. Le capteur `sensor.<prénom>_contexte_nutrition` est rafraîchi.
4. Un événement est émis : `coachsante_context_photo` s'il y a une photo à faire
   décrire, `coachsante_context` sinon.

```json
{
  "entry_id": "01J…",
  "person": "Thomas",
  "context_id": "3f9c1a7b2e4d6058",
  "label": "Paquet de raviolis",
  "text": null,
  "captured_at": "2026-07-25T19:59:00+02:00",
  "path": "/media/coachsante/thomas/contexte/2026-07-25_195900.jpg",
  "media_content_id": "media-source://media_source/local/coachsante/thomas/contexte/2026-07-25_195900.jpg"
}
```

`path` et `media_content_id` ne figurent que dans `coachsante_context_photo`.

### Analyse des photos de contexte

Une photo de contexte est **analysée à réception**, par une automatisation côté
utilisateur — l'intégration ne parle à aucun LLM. L'enchaînement :

1. `coachsante_context_photo` est émis, l'élément est marqué « en attente » et
   n'entre **pas** dans le prompt.
2. L'automatisation fait décrire la photo (valeurs nutritionnelles, marque,
   ingrédients) et rappelle `coachsante.add_context` avec le `context_id` reçu.
3. La description devient l'analyse de l'élément, qui rejoint le prompt.

Rejouer l'appel remplace l'analyse : une automatisation qui retente après un échec
ne crée pas de doublon. Automatisation prête à adapter dans
[`docs/automatisations/`](https://github.com/t-lefort/CoachSante-HomeAssistant/tree/main/docs/automatisations)
du dépôt de l'intégration.

### Ce que le modèle reçoit

Le capteur `sensor.<prénom>_contexte_nutrition` a pour état le nombre d'éléments
conservés, et porte en attribut `prompt` le bloc assemblé, du plus ancien au plus
récent :

```
- 2026-07-24 18:30 : Recette du soir — https://cookidoo.fr/recipes/recipe/fr-FR/r123456
- 2026-07-25 19:59 : Paquet de raviolis — Raviolis ricotta-épinards, 232 kcal / 100 g
```

Le même bloc part dans le champ `context` de `coachsante_meal_photo`.

Bornes : 30 éléments, 2 000 caractères par texte, 6 000 caractères pour le bloc
assemblé — au-delà, ce sont les plus anciens qui sautent. Un attribut d'entité
plus gros que ça encombre le *recorder*.

## Services

| Service | À quoi il sert | Champs |
|---|---|---|
| `coachsante.add_nutrition` | Créditer les macros d'un repas analysé | `entry_id`, `label`, `energy_kcal`, `protein_g`, `carbs_g`, `fat_g`, `fiber_g`, `sugar_g` |
| `coachsante.reset_day` | Remettre les compteurs du jour à zéro | `entry_id` |
| `coachsante.add_context` | Ajouter un texte au contexte, ou décrire une photo de contexte | `entry_id`, `text`, `label`, `context_id` |
| `coachsante.clear_context` | Oublier un élément de contexte, ou tous | `entry_id`, `context_id` |

## Autres événements

| Événement | Émis quand | Données |
|---|---|---|
| `coachsante_metrics` | Un lot de métriques est accepté | `entry_id`, `person`, `keys` |
| `coachsante_nutrition` | `add_nutrition` a été appelé | `entry_id`, `person`, `label`, `added`, `totals` |
| `coachsante_context` | Un texte de contexte arrive (webhook ou `add_context`) | `entry_id`, `person`, `context_id`, `label`, `text`, `analysis`, `captured_at` |
| `coachsante_context_photo` | Une photo de contexte attend son analyse | ci-dessus + `path`, `media_content_id` |

## Tester sans l'app

`scripts/test_webhook.py` envoie une charge utile signée. De quoi valider
l'intégration avant que la moindre ligne de Swift existe :

```bash
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> metrics
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> photo chemin/vers/photo.jpg
```
