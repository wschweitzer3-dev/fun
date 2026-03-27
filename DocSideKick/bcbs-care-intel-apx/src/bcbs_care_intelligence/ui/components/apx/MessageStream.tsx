import { Skeleton } from "@/components/ui/skeleton";
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
};

export function MessageStream({ messages }: MessageStreamProps) {
  return (
    <section className="message-stream" aria-live="polite">
      {messages.length === 0 ? (
        <div className="empty-chat-state">
          <h3>Unified AI Copilot for Healthcare Data</h3>
          <p>Ask a cohort, cost, or care-gap question to route across structured and unstructured insights.</p>
        </div>
      ) : null}
      {messages.map((message) => (
        <article key={message.id} className={`chat-message chat-message-${message.role}`}>
          <header className="chat-message-header">{message.role === "user" ? "You" : "BCBS Care Intelligence"}</header>
          <div className="chat-message-content">
            <p>{message.content}</p>
            {message.loading ? (
              <div className="loading-block">
                <p className="analyzing-text">Analyzing data...</p>
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-2/3" />
              </div>
            ) : null}
            {message.response ? <ResponseRenderer response={message.response} /> : null}
          </div>
        </article>
      ))}
    </section>
  );
}

