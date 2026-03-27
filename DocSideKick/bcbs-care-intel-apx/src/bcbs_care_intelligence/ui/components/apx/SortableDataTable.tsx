import { useMemo, useState } from "react";
import { ArrowDownAZ, ArrowUpAZ } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type SortDirection = "asc" | "desc";

type SortableDataTableProps = {
  rows: Array<Record<string, unknown>>;
};

function compareValues(a: unknown, b: unknown, direction: SortDirection): number {
  const dir = direction === "asc" ? 1 : -1;

  if (typeof a === "number" && typeof b === "number") {
    return (a - b) * dir;
  }

  const aText = String(a ?? "");
  const bText = String(b ?? "");
  return aText.localeCompare(bText) * dir;
}

export function SortableDataTable({ rows }: SortableDataTableProps) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [direction, setDirection] = useState<SortDirection>("asc");

  const columns = useMemo(() => {
    if (!rows.length) {
      return [];
    }
    return Object.keys(rows[0]);
  }, [rows]);

  const sortedRows = useMemo(() => {
    if (!sortKey) {
      return rows;
    }
    return [...rows].sort((left, right) => compareValues(left[sortKey], right[sortKey], direction));
  }, [rows, sortKey, direction]);

  function toggleSort(column: string) {
    if (sortKey === column) {
      setDirection((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(column);
    setDirection("asc");
  }

  if (!rows.length) {
    return <div className="empty-state">No tabular rows were returned for this response.</div>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead key={column}>
              <button className="table-sort-btn" onClick={() => toggleSort(column)} type="button">
                {column}
                {sortKey === column ? (
                  direction === "asc" ? (
                    <ArrowDownAZ size={14} />
                  ) : (
                    <ArrowUpAZ size={14} />
                  )
                ) : null}
              </button>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sortedRows.map((row, index) => (
          <TableRow key={`row-${index}`}>
            {columns.map((column) => (
              <TableCell key={`${index}-${column}`}>{String(row[column] ?? "")}</TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

