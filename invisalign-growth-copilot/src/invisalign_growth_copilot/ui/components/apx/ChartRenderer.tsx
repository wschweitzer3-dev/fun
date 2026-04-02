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

const CHART_COLORS = ["#0b5f8e", "#28a3b8", "#3fc1c9", "#7fdfd4", "#155f88", "#084b74"];

export function ChartRenderer({ chart }: ChartRendererProps) {
  return (
    <section className="chart-panel" aria-label="Dynamic visualization">
      <header className="chart-panel-header">
        <h4>{chart.title ?? "Dynamic Visual"}</h4>
      </header>
      <div className="chart-canvas">
        <ResponsiveContainer width="100%" height={300}>
          {chart.type === "line" ? (
            <LineChart data={chart.data}>
              <CartesianGrid strokeDasharray="4 4" stroke="#cfe6f4" />
              <XAxis dataKey={chart.x} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey={chart.y} stroke="#0b5f8e" strokeWidth={3} dot={{ r: 3 }} />
            </LineChart>
          ) : null}

          {chart.type === "bar" ? (
            <BarChart data={chart.data}>
              <CartesianGrid strokeDasharray="4 4" stroke="#cfe6f4" />
              <XAxis dataKey={chart.x} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey={chart.y} fill="#28a3b8" radius={[8, 8, 0, 0]} />
            </BarChart>
          ) : null}

          {chart.type === "pie" ? (
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie data={chart.data} dataKey={chart.y} nameKey={chart.x} outerRadius={104} label>
                {chart.data.map((_, index) => (
                  <Cell key={`pie-cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          ) : null}
        </ResponsiveContainer>
      </div>
    </section>
  );
}
