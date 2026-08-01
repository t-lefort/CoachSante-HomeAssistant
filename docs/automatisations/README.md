# Automatisations CoachSanté

Exemples d'automatisations Home Assistant qui consomment les événements et
entités exposés par l'intégration `coachsante`. Elles vivent **côté
utilisateur** : l'intégration fournit les entrées (events, entité `image`) et
reçoit les sorties (`coachsante.add_nutrition`), mais ne contient aucune
logique d'analyse.

| Fichier | Ce qu'il fait |
|---|---|
| [`analyse-repas-llm.yaml`](analyse-repas-llm.yaml) | Repas décrit et/ou photographié → estimation des macros par Claude → `coachsante.add_nutrition` |
| [`analyse-contexte-photo.yaml`](analyse-contexte-photo.yaml) | Photo d'emballage → relevé de l'étiquette → `coachsante.add_context` |
| [`rappel-repas-manquant.yaml`](rappel-repas-manquant.yaml) | Notification si un repas attendu n'est pas encore enregistré |

Les deux se complètent : la seconde remplit le contexte, que la première reçoit
tout assemblé dans l'événement `coachsante_meal_photo` et injecte dans son
prompt. Installer la première seule fonctionne ; installer la seconde sans la
première laisserait des photos de contexte en attente d'analyse pour rien.

## Rappel de repas manquant

L'exemple `rappel-repas-manquant.yaml` contrôle après le petit-déjeuner, le
déjeuner et le dîner que le capteur « Dernier repas » contient respectivement
un, deux ou trois repas pour la journée. Il envoie une notification seulement
si le compte attendu n'est pas atteint.

Avant de le recharger dans Home Assistant, remplace dans `variables` :

- `meal_sensor` par l'identifiant du capteur
  `sensor.<personne>_dernier_repas` ;
- `notify_action` par l'action mobile visible dans *Outils de développement →
  Actions*, par exemple `notify.mobile_app_iphone_de_thomas`.

Les trois horaires se règlent dans `triggers`. Le compteur est alimenté après
l'analyse nutritionnelle : prévois donc quelques minutes entre l'heure habituelle
du repas et celle du rappel.

## Analyse LLM des repas

### Ce que ça fait

```
app iOS ──texte/photo──▶ webhook ──▶ event coachsante_meal_photo
                                        │
                                        ▼
                            ai_task.generate_data (Claude + texte/photo)
                                        │
                                   JSON structuré
                                        │
                                        ▼
                            coachsante.add_nutrition (entry_id)
                                        │
                                        ▼
                     sensor.<personne>_calories_du_jour, …_protéines_du_jour, …
```

Une seule automatisation couvre toutes les personnes configurées :
l'`entry_id` est repris tel quel depuis l'événement, donc les macros
atterrissent toujours sur les compteurs de la bonne personne.

### Prérequis

1. **Une entité AI Task**, quelle qu'en soit l'intégration. L'automatisation
   passe par le service générique `ai_task.generate_data` : elle ne dépend
   d'aucun fournisseur en particulier. Anthropic, OpenAI et Google Generative
   AI exposent tous une entité AI Task et conviennent, du moment que le modèle
   choisi lit les images.

   Ajoute l'entrée depuis *Paramètres → Appareils et services → Ajouter*, puis
   *Ajouter un service* → *AI Task* sur la page de l'intégration (une entrée de
   conversation seule ne suffit pas : il faut bien une entrée **AI Task**).
   Récupère l'`entity_id` obtenu dans *Outils de développement → États* en
   filtrant sur `ai_task.`.

2. **Un modèle qui lit les images** si les repas peuvent inclure une photo. Dans les options de l'entrée, prends un
   modèle multimodal récent (`claude-opus-5` ou `claude-sonnet-5` côté
   Anthropic). Les modèles de la classe « mini » / Haiku sont à éviter : ils
   sous-estiment nettement les quantités.

3. **Home Assistant ≥ 2025.9** environ, pour que `ai_task.generate_data`
   accepte `attachments` **et** `structured_output`. Vérifié fonctionnel sur
   **2026.3.4**. Contrôle dans *Outils de développement → Actions* →
   `ai_task.generate_data` que les champs « Pièces jointes » et « Sortie
   structurée » apparaissent. Sans `attachments`, l'automatisation enverrait le
   prompt sans la photo — et le modèle répondrait quand même, en inventant.

### Installation

1. Copier `analyse-repas-llm.yaml` dans `packages/` (ou coller son contenu
   dans une nouvelle automatisation en mode YAML).

2. Ajuster les deux variables en tête de fichier :

   ```yaml
   variables:
     ai_task_entity: ai_task.claude   # ← l'entity_id relevé ci-dessus
     media_root: /media               # ← racine média de HA
   ```

   `media_root` n'est plus qu'un filet de sécurité : depuis la 0.3.0,
   l'intégration met l'identifiant media source directement dans l'événement
   (`media_content_id`), et l'automatisation le prend tel quel. La conversion à
   la main du chemin absolu (`/media/coachsante/thomas/2026-07-22_123500.jpg`)
   ne sert que si ce champ arrive vide, c'est-à-dire si aucun dossier média
   local n'est déclaré dans `homeassistant.media_dirs` :

   ```yaml
   # configuration.yaml
   homeassistant:
     media_dirs:
       local: /media
   ```

3. Recharger les automatisations et vérifier que
   `automation.coachsante_analyse_llm_d_une_photo_de_repas` apparaît.

### Tester sans envoyer de photo depuis l'iPhone

Prends une photo déjà présente dans `/media/coachsante/<prénom>/` et déclenche
l'événement à la main (*Outils de développement → Événements → Déclencher un
événement*) :

```yaml
event_type: coachsante_meal_photo
event_data:
  entry_id: 01JXXXXXXXXXXXXXXXXXXXXXXX   # ← Paramètres → Appareils et services → l'entrée CoachSanté (dans l'URL)
  person: Thomas
  path: /media/coachsante/thomas/2026-07-22_123500.jpg
  media_content_id: media-source://media_source/local/coachsante/thomas/2026-07-22_123500.jpg
  image_entity_id: image.thomas_dernier_repas
  note: test manuel
  taken_at: "2026-07-22T12:35:00+02:00"
  context: "- 2026-07-22 11:00 : Raviolis Rana — 232 kcal / 100 g, paquet de 250 g"
```

Les compteurs `sensor.thomas_calories_du_jour` & compagnie doivent bouger dans
la foulée, et une ligne apparaître dans le journal de bord.

## Description des photos de contexte

### Ce que ça fait

```
app iOS ──photo d'emballage──▶ webhook ──▶ event coachsante_context_photo
                                                    │  (élément « en attente »)
                                                    ▼
                                        ai_task.generate_data (Claude + photo)
                                                    │
                                              relevé de l'étiquette
                                                    ▼
                                    coachsante.add_context (context_id)
                                                    │
                                                    ▼
                              sensor.<personne>_contexte_nutrition (attribut « prompt »)
                                                    │
                                                    ▼
                          injecté dans le prompt du prochain repas photographié
```

Tant que l'automatisation n'a pas répondu, l'élément reste marqué « en attente »
(attribut `en_attente_analyse` du capteur) et **n'entre pas** dans le prompt : un
relevé à moitié fait ne pollue jamais une estimation.

L'app peut aussi envoyer du contexte purement textuel (un lien de recette collé
dans l'onglet Contexte). Celui-là n'a rien à analyser : il émet
`coachsante_context` et rejoint directement le prompt.

### Prérequis

Les mêmes que ci-dessus — une entité AI Task, un modèle qui lit les images. Les
étiquettes nutritionnelles sont écrites petit : l'app les envoie moins
compressées que les photos de repas (2000 px, JPEG 0,85), mais un modèle
« mini » restera en peine.

### Rétention

Les éléments de contexte vivent **14 jours**, puis disparaissent avec leur photo.
Réglable dans les options de l'intégration (*Paramètres → Appareils et services →
CoachSanté → Configurer*) ; 0 conserve tout. Bornes : 30 éléments, 2 000
caractères par texte, 6 000 pour le bloc assemblé — au-delà, ce sont les plus
anciens qui sautent.

Pour faire le ménage à la main :

```yaml
actions:
  - action: coachsante.clear_context
    data:
      entry_id: 01JXXXXXXXXXXXXXXXXXXXXXXX
      # sans context_id, c'est tout le contexte de la personne qui part
```

Et pour ajouter du contexte sans passer par l'app (liste de courses, recette de
la semaine dans un `input_text`, élément d'une liste `todo`) :

```yaml
actions:
  - action: coachsante.add_context
    data:
      entry_id: 01JXXXXXXXXXXXXXXXXXXXXXXX
      label: Menu de la semaine
      text: "{{ states('input_text.recette_du_soir') }}"
```

### Tester sans photographier d'emballage

```yaml
event_type: coachsante_context_photo
event_data:
  entry_id: 01JXXXXXXXXXXXXXXXXXXXXXXX
  person: Thomas
  context_id: 3f9c1a7b2e4d6058          # ← doit exister : envoie d'abord une photo de contexte
  label: Paquet de raviolis
  text: null
  captured_at: "2026-07-25T19:59:00+02:00"
  path: /media/coachsante/thomas/contexte/2026-07-25_195900.jpg
  media_content_id: media-source://media_source/local/coachsante/thomas/contexte/2026-07-25_195900.jpg
```

Le `context_id` doit correspondre à un élément réel, sinon `add_context` lève
`context_not_found` — c'est voulu : une analyse orpheline ne doit pas créer
silencieusement un élément fantôme. Le plus simple est d'envoyer une vraie photo
de contexte (depuis l'app, ou avec
`scripts/test_webhook.py … contexte-photo emballage.jpg`) et de relire le
`context_id` dans l'attribut `elements` du capteur.

### Réglages courants

| Symptôme | Piste |
|---|---|
| `Entity ai_task.… not found` | Mauvais `ai_task_entity`, ou aucune entrée AI Task créée dans l'intégration Anthropic |
| Le modèle décrit une image générique / dit ne rien voir | La photo n'est pas jointe : `media_root` ne correspond pas à la config média, ou la version de HA ignore `attachments` |
| `extra keys not allowed @ data['structured_output']` | La clé s'appelle **`structure`**. Le formulaire HA affiche « Sortie structurée », mais le paramètre du service est `structure` — deux noms différents pour la même chose |
| Erreur de validation sur `structure` | Le format attendu par HA est `nom_du_champ: {description, selector, required}`, **pas** du JSON Schema (`type: object` / `properties` / liste `required`). C'est un piège classique : la doc de la plupart des API LLM montre du JSON Schema, HA non |
| `Error talking to API: Error code: 402 - This request requires more credits, or fewer max_tokens` (OpenRouter) | **Le piège le plus courant.** OpenRouter pré-autorise la **totalité** du `max_tokens` demandé, pas la consommation réelle. L'intégration HA demande le plafond de sortie du modèle (65 536 tokens pour Sonnet 5), donc il faut ce montant en crédit *disponible* même si la réponse fait 100 tokens. Créditer le compte ; la sous-entrée AI Task n'expose que le modèle, pas de réglage `max_tokens` |
| `Error talking to API: Error code: 401 - User not found` (OpenRouter) | Clé API révoquée ou inconnue. À distinguer d'un `401 Missing Authentication header`, qui signale un espace parasite dans la valeur collée |
| ⚠️ Lire les erreurs OpenRouter dans **Journaux bruts** | La vue résumée de *Paramètres → Journaux* regroupe les messages par source et affiche le texte de la **première** occurrence avec un compteur alimenté par les suivantes. Un vieux 401 peut donc s'afficher « apparu 4 fois » alors que les occurrences récentes sont des 402. Toujours ouvrir ⋮ → *Afficher les journaux bruts* avant de conclure |
| `Unsupported parameter: reasoning_effort` en boucle dans les journaux | Bruit connu de la bibliothèque `python-open-router` ; sans effet sur le résultat |
| Macros systématiquement sous-estimées | Voir « Calibrer » ci-dessous |
| `no_nutrient_provided` | Le modèle a renvoyé un JSON vide ; regarder la trace de l'automatisation pour voir la réponse brute |

### Calibrer

Les estimations à partir d'une photo seule ont une marge d'erreur réelle
(±20–30 % sur les kcal est normal). Deux leviers, dans l'ordre :

1. **La note dans l'app.** Le prompt donne explicitement priorité à la note sur
   la lecture visuelle. « 150 g de riz » ou « pizza surgelée Buitoni » corrige
   immédiatement l'estimation, pour un coût d'usage nul.
2. **Le prompt.** Ajouter des repères propres à ta vaisselle (« les assiettes
   blanches font 26 cm de diamètre », « le verre bleu fait 25 cl ») dans le
   bloc `instructions` améliore nettement l'estimation des quantités.

3. **Le contexte amont.** Fournir au modèle une liste de plats candidats
   (recettes de la semaine, étiquettes des produits du placard, portions
   habituelles) le fait passer de l'estimation à l'identification. C'est de loin
   le gain le plus important — et depuis la 0.3.0 il n'y a plus rien à bricoler :
   l'onglet **Contexte** de l'app envoie ces éléments, l'intégration les garde
   deux semaines et les met assemblés dans l'événement, l'automatisation les
   injecte. Photographier l'emballage d'un plat préparé avant de le cuisiner vaut
   mieux que n'importe quel réglage de prompt.

### Coût

Une photo redimensionnée par l'app (1600 px, JPEG 0,7) coûte quelques milliers
de tokens d'entrée. À trois repas par jour et deux personnes, l'ordre de
grandeur est de quelques euros par mois sur `claude-opus-5`, moins sur
`claude-sonnet-5`. Le `mode: queued` de l'automatisation évite les appels
concurrents si plusieurs photos arrivent d'un coup après une coupure réseau.

## Personnaliser sans polluer le dépôt

Les fichiers de ce dossier sont des **exemples génériques**. Ta version réglée
— l'`entity_id` de ton entité AI Task, les repères de ta vaisselle, ton
contexte de recettes — est de la configuration Home Assistant : elle vit dans
ton `configuration.yaml` / tes automatisations, pas ici. Ne renvoie dans ce
dépôt que ce qui profite à tout le monde (une correction de prompt, une prise
en charge d'une nouvelle version de HA).
