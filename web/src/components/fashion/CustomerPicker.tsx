import { useUsers } from "../../api/queries";
import type { DemoCustomer } from "../../api/types";
import { usePageState } from "../../state/pageState";
import { Combobox } from "../../ui/Combobox";
import { Field, Select } from "../../ui/Field";
import { Toggle } from "../../ui/Toggle";
import styles from "./Fashion.module.css";
import type { Shopper } from "./useCustomer";

/**
 * Pick a shopping history: a few explained examples, or (on request) every customer id.
 */
export function CustomerPicker({
  customer,
  examples,
  onChange,
}: {
  customer: Shopper | null;
  examples: DemoCustomer[];
  onChange: (id: number | null) => void;
}) {
  const isExample = !!customer && examples.some((e) => e.user_id === customer.user_id);
  const [browseAll, setBrowseAll] = usePageState<boolean>("fashion.browse-all", !examples.length);
  const all = browseAll || (!!customer && !isExample) || !examples.length;
  const users = useUsers(all);
  return (
    <div className={styles.picker}>
      {all ? (
        <Combobox<number>
          label="Shopping history"
          multiple={false}
          options={users.data ?? []}
          value={customer ? [customer.user_id] : []}
          optionLabel={(id) => `Customer #${String(id)}`}
          placeholder={users.isPending ? "Loading customers…" : "Type a customer number"}
          onChange={(ids) => {
            onChange(ids[0] ?? null);
          }}
        />
      ) : (
        <Field label="Shopping history">
          {({ id }) => (
            <Select
              id={id}
              value={customer?.user_id ?? ""}
              onChange={(event) => {
                onChange(Number(event.target.value));
              }}
            >
              {examples.map((example) => (
                <option key={example.user_id} value={example.user_id}>
                  {example.label} (#{example.user_id})
                </option>
              ))}
            </Select>
          )}
        </Field>
      )}
      <Toggle
        checked={all}
        disabled={!examples.length}
        onChange={(value) => {
          setBrowseAll(value);
          if (!value && !isExample) onChange(examples[0]?.user_id ?? null);
        }}
      >
        Browse all customers
      </Toggle>
      {customer?.description ? <p className={styles.caption}>{customer.description}</p> : null}
    </div>
  );
}
