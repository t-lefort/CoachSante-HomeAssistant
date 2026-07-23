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

## Type `meal_photo` — photo de repas

```json
{
  "type": "meal_photo",
  "taken_at": "2026-07-22T12:35:00+02:00",
  "note": "midi, au boulot",
  "photo": {
    "content_type": "image/jpeg",
    "data": "<base64>"
  }
}
```

`content_type` accepte `image/jpeg` et `image/png`. L'app doit **redimensionner
avant l'envoi** (côté long ~1600 px, JPEG qualité ~0,7) : une photo brute
d'iPhone dépasse allègrement la limite une fois encodée en base64.

Effets côté Home Assistant :

1. La photo est écrite dans `<media>/coachsante/<prénom>/AAAA-MM-JJ_HHMMSS.jpg`.
2. L'entité `image.<prénom>_dernier_repas` est rafraîchie.
3. L'événement `coachsante_meal_photo` est émis :

```json
{
  "entry_id": "01J…",
  "person": "Thomas",
  "path": "/media/coachsante/thomas/2026-07-22_123500.jpg",
  "image_entity_id": "image.thomas_dernier_repas",
  "note": "midi, au boulot",
  "taken_at": "2026-07-22T12:35:00+02:00"
}
```

C'est cet événement qui déclenche l'automatisation d'analyse. Elle renvoie ses
résultats par le service `coachsante.add_nutrition`, en passant `entry_id` tel
quel.

## Autres événements

| Événement | Émis quand | Données |
|---|---|---|
| `coachsante_metrics` | Un lot de métriques est accepté | `entry_id`, `person`, `keys` |
| `coachsante_nutrition` | `add_nutrition` a été appelé | `entry_id`, `person`, `label`, `added`, `totals` |

## Tester sans l'app

`scripts/test_webhook.py` envoie une charge utile signée. De quoi valider
l'intégration avant que la moindre ligne de Swift existe :

```bash
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> metrics
python scripts/test_webhook.py https://exemple.fr/api/webhook/<id> <secret> photo chemin/vers/photo.jpg
```
