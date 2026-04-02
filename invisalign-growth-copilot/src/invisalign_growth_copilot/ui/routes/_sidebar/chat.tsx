import { startTransition, useEffect, useMemo, useState } from "react";
import { ChatComposer } from "@/components/apx/ChatComposer";
import { MessageStream, type ChatMessage } from "@/components/apx/MessageStream";
import { useApiHealth, useChatWithSupervisor, type ChatResponse } from "@/lib/api";

const SUGGESTED_PROMPTS = [
  "Who should we target for Comprehensive expansion right now?",
  "GP Dentist volume is declining in the Midwest. How do we fix it?",
  "What should reps say, and what external trends support this now?"
];

const LOADING_STEPS = [
  "Routing request to multi-agent supervisor...",
  "Running structured and playbook reasoning...",
  "Checking trend enrichment and ranking opportunities..."
];

function makeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

export function ChatPage() {
  const mutation = useChatWithSupervisor();
  const healthQuery = useApiHealth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingStepIndex, setLoadingStepIndex] = useState(0);

  useEffect(() => {
    if (!mutation.isPending) {
      setLoadingStepIndex(0);
      return;
    }

    const interval = window.setInterval(() => {
      setLoadingStepIndex((prev) => (prev + 1) % LOADING_STEPS.length);
    }, 1300);

    return () => window.clearInterval(interval);
  }, [mutation.isPending]);

  const loadingMessage = LOADING_STEPS[loadingStepIndex];

  const latestAssistantResponse: ChatResponse | undefined = useMemo(() => {
    const assistantMessages = messages.filter((message) => message.role === "assistant" && message.response);
    return assistantMessages.length ? assistantMessages[assistantMessages.length - 1].response : undefined;
  }, [messages]);

  async function submitMessage(message: string) {
    const userMessage: ChatMessage = {
      id: makeId("user"),
      role: "user",
      content: message
    };

    const loadingMessageId = makeId("loading");
    const loadingMessageNode: ChatMessage = {
      id: loadingMessageId,
      role: "assistant",
      content: "Running your analysis.",
      loading: true
    };

    startTransition(() => {
      setMessages((prev) => [...prev, userMessage, loadingMessageNode]);
    });

    try {
      const response = await mutation.mutateAsync({ message });
      const assistantMessage: ChatMessage = {
        id: makeId("assistant"),
        role: "assistant",
        content: response.response_text,
        response
      };
      startTransition(() => {
        setMessages((prev) => [...prev.filter((entry) => entry.id !== loadingMessageId), assistantMessage]);
      });
    } catch (error) {
      const err = error instanceof Error ? error.message : "Unknown error";
      const failedMessage: ChatMessage = {
        id: makeId("assistant-error"),
        role: "assistant",
        content: "The MAS request failed.",
        response: {
          response_text: "The request could not complete through live MAS services. Retry after endpoint verification.",
          data_table: [],
          chart_spec: null,
          kpis: [],
          sources: ["system:error"],
          notices: ["No fallback answer injected."],
          debug: {
            supervisor_endpoint: "mas-47342197-endpoint",
            latency_ms: 0,
            approval_hops: 0,
            total_round_trips: 0,
            external_trend_status: "not_used",
            error: err,
            fallback_used: false
          }
        }
      };
      startTransition(() => {
        setMessages((prev) => [...prev.filter((entry) => entry.id !== loadingMessageId), failedMessage]);
      });
    }
  }

  return (
    <div className="chat-page-layout">
      <section className="chat-main-column">
        <header className="hero-block">
          <p className="hero-kicker">Invisalign Provider Growth</p>
          <h1>Ask one question. Get who to target and what to say.</h1>
          <p>
            This copilot combines Genie + KA + external trends through your MAS endpoint with MCP approvals handled
            automatically.
          </p>
          <div className="prompt-chip-row" role="list" aria-label="Suggested prompts">
            {SUGGESTED_PROMPTS.map((prompt) => (
              <button key={prompt} type="button" className="prompt-chip" onClick={() => submitMessage(prompt)}>
                {prompt}
              </button>
            ))}
          </div>
        </header>

        <MessageStream messages={messages} loadingMessage={loadingMessage} />
        <ChatComposer isLoading={mutation.isPending} onSubmit={submitMessage} />
      </section>

      <aside className="chat-context-column">
        <section className="context-card">
          <h3>System Status</h3>
          <p>Endpoint: {healthQuery.data?.supervisor_endpoint ?? "mas-47342197-endpoint"}</p>
          <p>State: {healthQuery.data?.supervisor_state ?? "Checking..."}</p>
          <p>Health: {healthQuery.data?.status ?? "Checking..."}</p>
        </section>

        <section className="context-card">
          <h3>Latest Run</h3>
          <p>Latency: {latestAssistantResponse?.debug.latency_ms ?? "-"} ms</p>
          <p>MCP approvals: {latestAssistantResponse?.debug.approval_hops ?? "-"}</p>
          <p>Trend status: {latestAssistantResponse?.debug.external_trend_status ?? "-"}</p>
        </section>
      </aside>
    </div>
  );
}
