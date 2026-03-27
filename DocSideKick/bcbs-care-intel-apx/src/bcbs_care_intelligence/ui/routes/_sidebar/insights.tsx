import { ChartRenderer } from "@/components/apx/ChartRenderer";
import { SortableDataTable } from "@/components/apx/SortableDataTable";
import { useGetInsightsSnapshotSuspense } from "@/lib/api";

type InsightsPageProps = {
  mode: "member-insights" | "population-trends";
};

export function InsightsPage({ mode }: InsightsPageProps) {
  const { data } = useGetInsightsSnapshotSuspense();
  const title = mode === "member-insights" ? "Member Insights" : "Population Trends";

  return (
    <div className="insights-page">
      <header className="insights-header">
        <h2>{title}</h2>
        <p>{data.summary}</p>
      </header>

      <section className="insights-cards">
        {data.cards.map((card) => (
          <article key={card.title} className="insights-card">
            <p className="insights-card-label">{card.title}</p>
            <p className="insights-card-value">{card.value}</p>
            <p className="insights-card-detail">{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="insights-grid">
        {data.chart_specs.map((chart, idx) => (
          <ChartRenderer key={`${chart.type}-${idx}`} chart={chart} />
        ))}
      </section>

      <section className="table-panel">
        <h4>Underlying Data</h4>
        <SortableDataTable rows={data.data_table} />
      </section>
    </div>
  );
}

