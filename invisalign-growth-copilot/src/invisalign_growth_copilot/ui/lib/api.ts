import { useMutation, useQuery } from "@tanstack/react-query";

export type ChartType = "bar" | "line" | "pie";
export type KpiFormat = "number" | "currency" | "percent";

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

export type KpiTile = {
  label: string;
  value: number;
  format: KpiFormat;
  decimals: number;
  suffix?: string | null;
};

export type DebugInfo = {
  supervisor_endpoint: string;
  latency_ms: number;
  approval_hops: number;
  total_round_trips: number;
  external_trend_status: "not_used" | "ok" | "unavailable" | string;
  error?: string | null;
  fallback_used?: boolean;
};

export type ChatResponse = {
  response_text: string;
  data_table: Array<Record<string, unknown>>;
  chart_spec?: ChartSpec | null;
  kpis: KpiTile[];
  sources: string[];
  notices: string[];
  debug: DebugInfo;
};

export type HealthResponse = {
  status: "ok" | "degraded" | string;
  app_name: string;
  app_env: string;
  supervisor_endpoint: string;
  supervisor_state?: string | null;
  endpoint_ready: boolean;
  error?: string | null;
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
  return useMutation({ mutationFn: chatWithSupervisor });
}

export async function getApiHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>(`${API_BASE}/health`, {
    method: "GET"
  });
}

export function useApiHealth() {
  return useQuery({
    queryKey: ["api-health"],
    queryFn: getApiHealth,
    staleTime: 45_000,
    retry: 1
  });
}
