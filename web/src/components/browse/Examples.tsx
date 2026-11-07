import { Button } from "../../ui/Button";
import styles from "./Browse.module.css";

/** "Or try an example": pill buttons that fill in and run a search. */
export function Examples({
  examples,
  current,
  onPick,
}: {
  examples: readonly string[];
  current?: string;
  onPick: (example: string) => void;
}) {
  return (
    <div className={styles.examples} role="group" aria-label="Examples">
      <span className={styles.examplesLabel}>Or try an example</span>
      {examples.map((example) => (
        <Button
          key={example}
          variant="pill"
          size="sm"
          pressed={current === example}
          onClick={() => {
            onPick(example);
          }}
        >
          {example}
        </Button>
      ))}
    </div>
  );
}
