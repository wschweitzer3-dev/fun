import { ResponseRenderer } from "@/components/apx/ResponseRenderer";
import type { ChatResponse } from "@/lib/api";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;
  loading?: boolean;
};

type MessageStreamProps = {
  messages: ChatMessage[];
  loadingMessage: string;
};

export function MessageStream({ messages, loadingMessage }: MessageStreamProps) {
  return (
    <section className="message-stream" aria-live="polite">
      {messages.length === 0 ? (
        <div className="empty-chat-state">
          <p className="empty-chip">Provider Growth Assistant</p>
          <h2>One question in. Action plan out.</h2>
          <p>
            Ask for targets, decline diagnosis, or revenue lift. The app runs MAS, resolves MCP approvals, and returns
            execution-ready recommendations.
          </p>
        </div>
      ) : null}

      {messages.map((message) => (
        <article key={message.id} className={`chat-message chat-message-${message.role}`}>
          <header className="chat-message-header">
            {message.role === "user" ? "You" : "Invisalign Growth Copilot"}
          </header>
          <div className="chat-message-content">
            {message.role === "user" || !message.response ? <p>{message.content}</p> : null}
            {message.loading ? <p className="analyzing-text">{loadingMessage}</p> : null}
            {message.response ? <ResponseRenderer response={message.response} /> : null}
          </div>
        </article>
      ))}
    </section>
  );
}
