import { Sparkles } from "lucide-react";

import type { ReadingMemory } from "../../state/session";
import browse from "../browse/Browse.module.css";

/** Which remembered reading tastes shape the results (liked genres rank higher; avoided
 * genres and authors are left out). */
export function ReadingTastesNote({
  memory,
  boost = true,
}: {
  memory: ReadingMemory;
  boost?: boolean;
}) {
  const parts: string[] = [];
  if (boost && memory.liked_genres.length) parts.push(`more ${memory.liked_genres.join(", ")}`);
  const avoided = [...memory.avoided_genres, ...memory.avoided_authors];
  if (avoided.length) parts.push(`no ${avoided.join(", ")}`);
  if (!parts.length) return null;
  return (
    <p className={browse.note} data-testid="tastes-note">
      <Sparkles size={14} aria-hidden="true" />
      Your remembered tastes apply: {parts.join(" · ")}.
    </p>
  );
}
