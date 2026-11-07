import { Check } from "lucide-react";

import { productImageUrl } from "../../api/client";
import { coverSources, drinkPhoto } from "../../domains/format";
import type { ApprovalOption } from "../../state/approval";
import { Photo } from "../../ui/Photo";
import styles from "./ApprovalDrawer.module.css";

function Thumb({ option }: { option: ApprovalOption }) {
  const item = option.item;
  if (!item) return null;
  let photo;
  if (item.kind === "product") {
    photo = (
      <Photo
        sources={[productImageUrl(item.product.product_id)]}
        alt=""
        ratio="2 / 3"
        paper
        placeholder=""
      />
    );
  } else if (item.kind === "cocktail") {
    photo = (
      <Photo sources={[drinkPhoto(item.cocktail)]} alt="" ratio="1" fit="cover" placeholder="" />
    );
  } else {
    photo = (
      <Photo
        sources={coverSources(item.book).map((s) => s.url)}
        alt=""
        ratio="2 / 3"
        fit="cover"
        placeholder=""
      />
    );
  }
  return <span className={styles.thumb}>{photo}</span>;
}

/** One checkbox per value the proposal touches; unticking drops it from the approval. */
export function ApprovalOptions({
  options,
  unticked,
  onToggle,
  disabled,
  suffix,
}: {
  options: ApprovalOption[];
  unticked: ReadonlySet<string>;
  onToggle: (key: string, kept: boolean) => void;
  disabled?: boolean;
  suffix?: (option: ApprovalOption) => string | undefined;
}) {
  if (!options.length) return null;
  return (
    <ul className={styles.options}>
      {options.map((option) => {
        const kept = !unticked.has(option.key);
        const extra = suffix?.(option);
        return (
          <li key={option.key}>
            <label className={styles.option} data-kept={kept ? "true" : "false"}>
              <input
                type="checkbox"
                className={styles.checkbox}
                checked={kept}
                disabled={disabled}
                onChange={(event) => {
                  onToggle(option.key, event.target.checked);
                }}
              />
              <span className={styles.box} aria-hidden="true">
                <Check size={14} strokeWidth={3} />
              </span>
              <Thumb option={option} />
              <span className={styles.optionLabel}>
                {option.label}
                {extra ? <span className={styles.optionExtra}> → {extra}</span> : null}
              </span>
            </label>
          </li>
        );
      })}
    </ul>
  );
}
