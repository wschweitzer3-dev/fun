import { useEffect, useMemo, useState } from "react";
import type { ChatResponse, KpiTile } from "@/lib/api";
import { ChartRenderer } from "@/components/apx/ChartRenderer";
import { SortableDataTable } from "@/components/apx/SortableDataTable";

type ResponseRendererProps = {
  response: ChatResponse;
};

function toParagraphs(text: string): string[] {
  return text
    .split("\n")
    .map((part) => part.trim())
    .filter(Boolean);
}

function formatKpiValue(tile: KpiTile, value: number): string {
  const options: Intl.NumberFormatOptions = {
    minimumFractionDigits: tile.decimals,
    maximumFractionDigits: tile.decimals
  };
  if (tile.format === "currency") {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", ...options }).format(value);
  }
  if (tile.format === "percent") {
    return `${new Intl.NumberFormat("en-US", options).format(value)}%`;
  }
  return new Intl.NumberFormat("en-US", options).format(value);
}

function AnimatedKpi({ tile }: { tile: KpiTile }) {
  const [animatedValue, setAnimatedValue] = useState(0);

  useEffect(() => {
    const durationMs = 900;
    const start = performance.now();

    function step(now: number) {
      const progress = Math.min((now - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAnimatedValue(tile.value * eased);
      if (progress < 1) {
        requestAnimationFrame(step);
      }
    }

    requestAnimationFrame(step);
  }, [tile.value]);

  return (
    <article className="kpi-card">
      <p className="kpi-label">{tile.label}</p>
      <p className="kpi-value">
        {formatKpiValue(tile, animatedValue)}
        {tile.suffix ?? ""}
      </p>
    </article>
  );
}

export function ResponseRenderer({ response }: ResponseRendererProps) {
  const paragraphs = useMemo(() => toParagraphs(response.response_text), [response.response_text]);

  return (
    <div className="response-layout">
      <section className="zone recommendation-zone">
        <h4>Recommendation</h4>
        {paragraphs.map((paragraph, idx) => (
          <p key={`paragraph-${idx}`}>{paragraph}</p>
        ))}
      </section>

      <section className="zone visual-zone">
        <h4>Dynamic Visuals</h4>
        {response.kpis.length ? (
          <div className="kpi-grid">
            {response.kpis.map((tile) => (
              <AnimatedKpi key={tile.label} tile={tile} />
            ))}
          </div>
        ) : (
          <p className="muted-copy">No KPI summary returned for this answer.</p>
        )}

        {response.chart_spec ? <ChartRenderer chart={response.chart_spec} /> : null}

        {response.data_table.length ? (
          <div className="table-panel">
            <h5>Supporting Data</h5>
            <SortableDataTable rows={response.data_table} />
          </div>
        ) : null}
      </section>

      <aside className="zone context-zone">
        <h4>Sources & Context</h4>
        {response.sources.length ? (
          <ul className="sources-list">
            {response.sources.map((source) => (
              <li key={source}>{source}</li>
            ))}
          </ul>
        ) : (
          <p className="muted-copy">No explicit source identifiers returned.</p>
        )}

        {response.notices.length ? (
          <div className="notice-stack">
            {response.notices.map((notice) => (
              <p key={notice} className="notice-copy">
                {notice}
              </p>
            ))}
          </div>
        ) : null}

        <div className="debug-meta">
          <p>Endpoint: {response.debug.supervisor_endpoint}</p>
          <p>Latency: {response.debug.latency_ms} ms</p>
          <p>MCP approvals: {response.debug.approval_hops}</p>
          <p>Trend status: {response.debug.external_trend_status}</p>
        </div>
      </aside>
    </div>
  );
}
