import { FormEvent, useState } from "react";

type ChatComposerProps = {
  defaultValue?: string;
  isLoading: boolean;
  onSubmit: (value: string) => Promise<void> | void;
};

export function ChatComposer({ defaultValue, isLoading, onSubmit }: ChatComposerProps) {
  const [value, setValue] = useState(defaultValue ?? "");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || isLoading) {
      return;
    }

    await onSubmit(trimmed);
    setValue("");
  }

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <textarea
        className="chat-input"
        placeholder="Which diabetic members are missing A1C tests and why?"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        rows={3}
        disabled={isLoading}
        aria-label="Ask BCBS Care Intelligence"
      />
      <div className="chat-composer-actions">
        <p className="chat-composer-hint">Press Enter in the button or click Send for analysis.</p>
        <button type="submit" className="primary-btn" disabled={isLoading || value.trim().length === 0}>
          {isLoading ? "Analyzing..." : "Send"}
        </button>
      </div>
    </form>
  );
}

