import { ArrowUp } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "../../ui/Button";
import styles from "./Composer.module.css";

/** The message box: Enter sends, Shift+Enter adds a line. */
export function Composer({
  placeholder,
  busy,
  onSend,
}: {
  placeholder: string;
  busy: boolean;
  onSend: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const submit = (event?: { preventDefault: () => void }) => {
    event?.preventDefault();
    if (!text.trim() || busy) return;
    onSend(text);
    setText("");
    area.current?.focus();
  };
  return (
    <form className={styles.composer} onSubmit={submit}>
      <label htmlFor={id} className="visually-hidden">
        Message Curio
      </label>
      <textarea
        ref={area}
        id={id}
        className={styles.input}
        value={text}
        rows={1}
        maxLength={2000}
        placeholder={placeholder}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <Button
        type="submit"
        variant="primary"
        className={styles.send}
        icon={<ArrowUp />}
        aria-label="Send"
        disabled={busy || !text.trim()}
      />
    </form>
  );
}
