import type { PairingRequest } from "../../api/types";
import type { Session } from "../../state/session";

/** Remembered tastes that apply to pairings: avoided genres and the cocktail tastes. */
export function pairingTastes(
  session: Pick<Session, "bookPreferences" | "cocktailPreferences">,
): Partial<PairingRequest> {
  const body: Partial<PairingRequest> = {};
  const avoided = session.bookPreferences.avoided_genres;
  if (avoided.length) body.avoided_genres = avoided;
  const tastes = Object.fromEntries(
    Object.entries(session.cocktailPreferences).filter(([, v]) =>
      Array.isArray(v) ? v.length : v,
    ),
  );
  if (Object.keys(tastes).length) {
    body.cocktail_preferences = tastes as PairingRequest["cocktail_preferences"];
  }
  return body;
}
