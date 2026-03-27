import { useMemo, useState } from "react";
import type { ChatResponse } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
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

export function ResponseRenderer({ response }: ResponseRendererProps) {
  const [showAllSources, setShowAllSources] = useState(false);
  const paragraphs = useMemo(() => toParagraphs(response.response_text), [response.response_text]);
  const sources = response.sources ?? [];
  const visibleSources = showAllSources ? sources : sources.slice(0, 4);

  return (
    <div className="response-body">
      <div className="response-main">
        <section className="response-text">
          {paragraphs.map((paragraph, idx) => (
            <p key={`paragraph-${idx}`}>{paragraph}</p>
          ))}
          {response.debug?.fallback_used ? <Badge>Demo fallback</Badge> : null}
        </section>

        {response.data_table?.length ? (
          <section className="table-panel">
            <h4>Structured Output</h4>
            <SortableDataTable rows={response.data_table} />
          </section>
        ) : null}

        {response.chart_spec ? <ChartRenderer chart={response.chart_spec} /> : null}
      </div>

      <aside className="response-side-panel">
        <h4>Sources</h4>
        {!visibleSources.length ? <p>No explicit citations returned.</p> : null}
        <ul className="sources-list">
          {visibleSources.map((source) => (
            <li key={source}>{source}</li>
          ))}
        </ul>
        {sources.length > 4 ? (
          <button className="text-btn" type="button" onClick={() => setShowAllSources((prev) => !prev)}>
            {showAllSources ? "Show fewer" : `Show ${sources.length - 4} more`}
          </button>
        ) : null}
      </aside>
    </div>
  );
}

