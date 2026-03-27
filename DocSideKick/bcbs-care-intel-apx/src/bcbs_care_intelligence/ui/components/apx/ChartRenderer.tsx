import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import type { ChartSpec } from "@/lib/api";

type ChartRendererProps = {
  chart: ChartSpec;
};

const CHART_COLORS = ["#003A8F", "#005EB8", "#3A87D1", "#79ACE0", "#A7C8EB", "#C7DCF2"];

export function ChartRenderer({ chart }: ChartRendererProps) {
  return (
    <section className="chart-panel" aria-label="Structured visualization">
      <div className="chart-panel-header">
        <h4>{chart.title ?? "Structured Visualization"}</h4>
      </div>
      <div className="chart-canvas">
        <ResponsiveContainer width="100%" height={280}>
          {chart.type === "line" ? (
            <LineChart data={chart.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#dbe5f3" />
              <XAxis dataKey={chart.x} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey={chart.y} stroke="#003A8F" strokeWidth={3} dot={{ r: 3 }} />
            </LineChart>
          ) : null}

          {chart.type === "bar" ? (
            <BarChart data={chart.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#dbe5f3" />
              <XAxis dataKey={chart.x} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey={chart.y} fill="#005EB8" radius={[6, 6, 0, 0]} />
            </BarChart>
          ) : null}

          {chart.type === "pie" ? (
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie data={chart.data} dataKey={chart.y} nameKey={chart.x} cx="50%" cy="50%" outerRadius={100} label>
                {chart.data.map((_, index) => (
                  <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          ) : null}
        </ResponsiveContainer>
      </div>
    </section>
  );
}

