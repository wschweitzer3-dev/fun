import { startTransition, useMemo, useState } from "react";
import { MessageStream, type ChatMessage } from "@/components/apx/MessageStream";
import { ChatComposer } from "@/components/apx/ChatComposer";
import { useChatWithSupervisor, type ChatResponse } from "@/lib/api";

function makeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

export function ChatPage() {
  const mutation = useChatWithSupervisor();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
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
    const loadingMessage: ChatMessage = {
      id: loadingMessageId,
      role: "assistant",
      content: "Running multi-agent analysis across payer data and care notes.",
      loading: true
    };

    startTransition(() => {
      setMessages((prev) => [...prev, userMessage, loadingMessage]);
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
        content: "The live agent call failed. Review details below.",
        response: {
          response_text: "The request could not be completed through live services. Please retry.",
          sources: ["system:error"],
          debug: { error: err, fallback_used: false }
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
        <MessageStream messages={messages} />
        <ChatComposer
          defaultValue="Which diabetic members are missing A1C tests and why?"
          isLoading={mutation.isPending}
          onSubmit={submitMessage}
        />
      </section>
      <aside className="chat-context-column">
        <div className="context-card">
          <h3>Context</h3>
          <p>Supervisor: HLS_Payer_Supervisor</p>
          <p>Structured source: HLS Payer Structured Genie</p>
          <p>Unstructured source: Knowledge Assistant notes corpus</p>
        </div>
        <div className="context-card">
          <h3>Latest Query Metadata</h3>
          <p>
            Endpoint:{" "}
            {latestAssistantResponse?.debug?.supervisor_endpoint
              ? latestAssistantResponse.debug.supervisor_endpoint
              : "Not yet resolved"}
          </p>
          <p>
            Genie Space:{" "}
            {latestAssistantResponse?.debug?.genie_space_id
              ? latestAssistantResponse.debug.genie_space_id
              : "Not queried"}
          </p>
          <p>Fallback Used: {latestAssistantResponse?.debug?.fallback_used ? "Yes" : "No"}</p>
        </div>
      </aside>
    </div>
  );
}

