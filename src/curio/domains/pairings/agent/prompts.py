"""How a drink pairing reads to the book agent's model (the PairCocktail tool result)."""

from curio.domains.cocktails.agent.prompts import alcohol_label
from curio.domains.pairings.models import BookPairing


def render_pairing(pairing: BookPairing) -> str:
    """A PairCocktail result for the model: the profile, then each drink with its recipe,
    alcohol status and the pairer's reasons, numbered "drink #N"."""
    profile = pairing.profile
    lines = [f"Pairing: {profile.summary}."]
    for theme in profile.themes:
        lines.append(f"- {theme.label} ({round(theme.weight * 100)}%): {theme.evidence}")
    if profile.alcohol_free:
        lines.append(f"Alcohol-free recipes only, because {profile.alcohol_free_reason}.")
    if not pairing.pairings:
        lines.append("No recipe fits; suggest pairing another book or allowing alcohol.")
    for position, item in enumerate(pairing.pairings, start=1):
        c = item.cocktail
        recipe = ", ".join(
            f"{i.name} ({i.measure})" if i.measure else i.name for i in c.ingredients
        )
        lines.append(
            f"{position}. drink #{c.drink_id} {c.name} | {alcohol_label(c)} | {c.category} | "
            f"{c.glass} | ingredients: {recipe} | why: " + "; ".join(item.reasons)
        )
    lines.append(f"Note: {pairing.note}")
    return "\n".join(lines)
