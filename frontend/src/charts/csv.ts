import { downloadFile } from "@/lib/utils";

export type CsvCell = string | number | boolean | Date | null | undefined;

/** Tabular data behind a chart: used for CSV export and the "view as table" toggle. */
export interface CsvTable {
  headers: string[];
  rows: CsvCell[][];
}

function cellToString(v: CsvCell): string {
  if (v == null) return "";
  if (v instanceof Date) return v.toISOString();
  return String(v);
}

/** RFC 4180 escaping; also neutralizes spreadsheet formula injection (=, +, -, @). */
export function escapeCsvCell(v: CsvCell): string {
  let s = cellToString(v);
  if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build CSV text (CRLF line endings). */
export function toCsv(table: CsvTable): string {
  return [table.headers, ...table.rows].map((r) => r.map(escapeCsvCell).join(",")).join("\r\n");
}

/** Array of records → CsvTable. `columns` picks/orders keys and optionally renames headers. */
export function recordsToTable<T extends Record<string, unknown>>(
  records: readonly T[],
  columns?: ReadonlyArray<(keyof T & string) | { key: keyof T & string; header: string }>,
): CsvTable {
  const cols =
    columns?.map((c) => (typeof c === "string" ? { key: c, header: c } : c)) ??
    Object.keys(records[0] ?? {}).map((k) => ({ key: k, header: k }));
  return {
    headers: cols.map((c) => c.header),
    rows: records.map((r) => cols.map((c) => r[c.key] as CsvCell)),
  };
}

/** Download a CsvTable as a UTF-8 (with BOM, for Excel) .csv file. */
export function downloadCsv(filename: string, table: CsvTable): void {
  const name = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  downloadFile(name, new Blob(["﻿", toCsv(table)], { type: "text/csv;charset=utf-8" }));
}
