import { FormEvent, useState } from "react";

type ChatComposerProps = {
  defaultValue?: string;
  isLoading: boolean;
  onSubmit: (message: string) => Promise<void>;
};

export function ChatComposer({ defaultValue = "", isLoading, onSubmit }: ChatComposerProps) {
  const [message, setMessage] = useState(defaultValue);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || isLoading) {
      return;
    }
    await onSubmit(trimmed);
    setMessage("");
  }

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <label htmlFor="chat-message" className="composer-label">
        Ask one question
      </label>
      <textarea
        id="chat-message"
        className="chat-input"
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder="Example: Which Midwest providers are Tier 1 Comprehensive targets this month?"
        rows={4}
        disabled={isLoading}
      />
      <div className="chat-composer-actions">
        <p className="chat-composer-hint">Structured + unstructured + external trends in one run.</p>
        <button className="primary-btn" type="submit" disabled={isLoading || !message.trim()}>
          {isLoading ? "Running analysis..." : "Run analysis"}
        </button>
      </div>
    </form>
  );
}
