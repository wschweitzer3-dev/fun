import { useMutation, useSuspenseQuery } from "@tanstack/react-query";

export type ChartType = "bar" | "line" | "pie";

export type ChatRequest = {
  message: string;
};

export type ChartSpec = {
  type: ChartType;
  x: string;
  y: string;
  data: Array<Record<string, unknown>>;
  title?: string | null;
};

export type DebugInfo = {
  supervisor_endpoint?: string | null;
  genie_space_id?: string | null;
  sql_text?: string | null;
  error?: string | null;
  fallback_used?: boolean;
};

export type ChatResponse = {
  response_text: string;
  data_table?: Array<Record<string, unknown>> | null;
  chart_spec?: ChartSpec | null;
  sources?: string[] | null;
  debug?: DebugInfo | null;
};

export type InsightCard = {
  title: string;
  value: string;
  detail: string;
};

export type InsightsSnapshot = {
  title: string;
  summary: string;
  cards: InsightCard[];
  chart_specs: ChartSpec[];
  data_table: Array<Record<string, unknown>>;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Request failed (${response.status}): ${text}`);
  }

  return (await response.json()) as T;
}

export async function chatWithSupervisor(body: ChatRequest): Promise<ChatResponse> {
  return fetchJson<ChatResponse>(`${API_BASE}/chat`, {
    method: "POST",
    body: JSON.stringify(body)
  });
}

export function useChatWithSupervisor() {
  return useMutation({
    mutationFn: chatWithSupervisor
  });
}

export async function getInsightsSnapshot(): Promise<InsightsSnapshot> {
  return fetchJson<InsightsSnapshot>(`${API_BASE}/insights/snapshot`, {
    method: "GET"
  });
}

export function useGetInsightsSnapshotSuspense() {
  return useSuspenseQuery({
    queryKey: ["insights-snapshot"],
    queryFn: getInsightsSnapshot
  });
}

