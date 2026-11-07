import {
  Brain,
  Compass,
  Flame,
  Gem,
  Heart,
  Moon,
  PartyPopper,
  Sparkles,
  Sun,
  type LucideProps,
} from "lucide-react";

/** The API names each theme's icon with a Material icon name; these are their equivalents. */
const ICONS: Record<string, React.ComponentType<LucideProps>> = {
  fireplace: Flame,
  favorite: Heart,
  diamond: Gem,
  sunny: Sun,
  dark_mode: Moon,
  psychology: Brain,
  explore: Compass,
  celebration: PartyPopper,
};

export function ThemeIcon({ name, ...props }: { name: string } & LucideProps) {
  const Icon = ICONS[name] ?? Sparkles;
  return <Icon aria-hidden="true" {...props} />;
}
