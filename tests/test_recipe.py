"""Extraction des recettes publiées en JSON-LD."""

from custom_components.coachsante.recipe import extract_recipe


def test_extrait_portions_ingredients_et_nutrition() -> None:
    """Une recette schema.org devient un contexte directement exploitable."""
    page = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Recipe",
      "name": "Gratin de chou-fleur",
      "recipeYield": "6 portions",
      "recipeIngredient": ["1000 g de chou-fleur", "250 g de lait de coco"],
      "nutrition": {
        "@type": "NutritionInformation",
        "calories": "366 kcal",
        "proteinContent": "16.5 g"
      }
    }
    </script>
    """

    recipe = extract_recipe(page)

    assert recipe is not None
    assert recipe.name == "Gratin de chou-fleur"
    assert "6 portions" in recipe.text
    assert "1000 g de chou-fleur" in recipe.text
    assert "énergie 366 kcal" in recipe.text
    assert "protéines 16.5 g" in recipe.text


def test_page_sans_recette_est_ignoree() -> None:
    """Une page ordinaire ne fabrique pas de données."""
    assert extract_recipe("<html><title>Pas une recette</title></html>") is None
