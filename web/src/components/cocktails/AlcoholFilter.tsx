import type { AlcoholFilter as Alcohol } from "../../api/types";
import { Segmented } from "../../ui/Segmented";
import browse from "../browse/Browse.module.css";
import { ALCOHOL_OPTIONS } from "./filters";

export function AlcoholFilter({
  value,
  onChange,
}: {
  value: Alcohol;
  onChange: (value: Alcohol) => void;
}) {
  return (
    <div className={browse.control}>
      <span className={browse.controlLabel} aria-hidden="true">
        Alcohol
      </span>
      <Segmented label="Alcohol" options={ALCOHOL_OPTIONS} value={value} onChange={onChange} />
    </div>
  );
}
