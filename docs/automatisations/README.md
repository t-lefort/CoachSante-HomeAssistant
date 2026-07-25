# Automatisations CoachSanté

Exemples d'automatisations Home Assistant qui consomment les événements et
entités exposés par l'intégration `coachsante`. Elles vivent **côté
utilisateur** : l'intégration fournit les entrées (events, entité `image`) et
reçoit les sorties (`coachsante.add_nutrition`), mais ne contient aucune
logique d'analyse.

| Fichier | Ce qu'il fait |
|---|---|
| [`analyse-repas-llm.yaml`](analyse-repas-llm.yaml) | Photo de repas → estimation des macros par Claude → `coachsante.add_nutrition` |

## Analyse LLM des photos de repas

### Ce que ça fait

```
app iOS ──photo──▶ webhook ──▶ event coachsante_meal_photo
                                        │
                                        ▼
                            ai_task.generate_data (Claude + photo)
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

2. **Un modèle qui lit les images.** Dans les options de l'entrée, prends un
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

   `media_root` doit correspondre au dossier configuré dans
   `homeassistant.media_dirs` (`/media` par défaut). L'automatisation s'en
   sert pour convertir le chemin absolu de la photo
   (`/media/coachsante/thomas/2026-07-22_123500.jpg`) en identifiant media
   source (`media-source://media_source/local/coachsante/thomas/…`).

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
  image_entity_id: image.thomas_dernier_repas
  note: test manuel
  taken_at: "2026-07-22T12:35:00+02:00"
```

Les compteurs `sensor.thomas_calories_du_jour` & compagnie doivent bouger dans
la foulée, et une ligne apparaître dans le journal de bord.

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

3. **Le contexte amont.** Si tu peux fournir à l'automatisation une liste de
   plats candidats (recettes de la semaine, courses, planning de repas), la
   glisser dans `instructions` fait passer le modèle de l'estimation à
   l'identification. C'est de loin le gain le plus important, et le reste de
   l'automatisation ne bouge pas — seul le bloc `instructions` s'allonge.

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
