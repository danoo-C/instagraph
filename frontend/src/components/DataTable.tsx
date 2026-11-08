import { useEffect, useRef, type ReactNode } from "react";
import { useTable, type OnChangeFn, type Row, type RowData } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  dataTableFeatures,
  type DataTableColumn,
  type DataTableFeatures,
  type SortingState,
} from "@/components/dataTableColumns";
import { cn } from "@/lib/utils";

export interface DataTableProps<T extends RowData> {
  data: readonly T[];
  columns: DataTableColumn<T>[];
  getRowId?: (row: T, index: number) => string;
  initialSorting?: SortingState;
  /** Controlled sorting (e.g. server-side sort for the chats list). */
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  /** Data is already sorted by the backend. */
  manualSorting?: boolean;
  /** Virtualize rows inside a scroll container of this height (px). */
  virtualize?: { height: number; rowHeight?: number };
  onRowClick?: (row: T) => void;
  /** Called when the last rows become visible (infinite loading, virtualized only). */
  onEndReached?: () => void;
  caption?: string;
  empty?: ReactNode;
  className?: string;
}

/** Sortable table (TanStack Table v9) with optional row virtualization (TanStack Virtual). */
export function DataTable<T extends RowData>({
  data,
  columns,
  getRowId,
  initialSorting,
  sorting,
  onSortingChange,
  manualSorting,
  virtualize,
  onRowClick,
  onEndReached,
  caption,
  empty,
  className,
}: DataTableProps<T>) {
  const table = useTable({
    features: dataTableFeatures,
    columns,
    data: data as T[],
    getRowId,
    manualSorting,
    onSortingChange,
    ...(sorting ? { state: { sorting } } : {}),
    initialState: { sorting: initialSorting ?? [] },
  });
  const rows = table.getRowModel().rows;
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowHeight = virtualize?.rowHeight ?? 36;
  // eslint-disable-next-line react-hooks/incompatible-library -- virtualizer is used only in this render
  const virtualizer = useVirtualizer({
    count: virtualize ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 10,
  });

  const items = virtualize ? virtualizer.getVirtualItems() : [];
  const lastIndex = items.length ? items[items.length - 1]!.index : -1;
  const nearEnd = Boolean(virtualize) && rows.length > 0 && lastIndex >= rows.length - 5;
  useEffect(() => {
    if (nearEnd) onEndReached?.();
  }, [nearEnd, rows.length, onEndReached]);
  const padTop = virtualize && items.length ? items[0]!.start : 0;
  const padBottom =
    virtualize && items.length ? virtualizer.getTotalSize() - items[items.length - 1]!.end : 0;
  const visible: Row<DataTableFeatures, T>[] = virtualize ? items.map((v) => rows[v.index]!) : rows;
  const colCount = table.getAllLeafColumns().length;

  return (
    <div
      ref={scrollRef}
      className={cn("overflow-auto rounded-lg border border-border", className)}
      style={virtualize ? { height: virtualize.height } : undefined}
    >
      <table className="w-full border-collapse text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="sticky top-0 z-10 bg-surface-2">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    className="whitespace-nowrap px-3 py-2 font-medium"
                    aria-sort={
                      sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                    }
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 hover:text-accent"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        <table.FlexRender header={header} />
                        <span aria-hidden="true" className="text-xs text-muted">
                          {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : "↕"}
                        </span>
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {padTop > 0 && (
            <tr aria-hidden="true">
              <td colSpan={colCount} style={{ height: padTop, padding: 0 }} />
            </tr>
          )}
          {visible.map((row) => (
            <tr
              key={row.id}
              className={cn(
                "border-t border-border",
                onRowClick && "cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2",
              )}
              style={virtualize ? { height: rowHeight } : undefined}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              onKeyDown={
                onRowClick
                  ? (e) => {
                      if (e.key === "Enter") onRowClick(row.original);
                    }
                  : undefined
              }
              tabIndex={onRowClick ? 0 : undefined}
            >
              {row.getAllCells().map((cell) => (
                <td key={cell.id} className="px-3 py-1.5 tabular-nums">
                  <table.FlexRender cell={cell} />
                </td>
              ))}
            </tr>
          ))}
          {padBottom > 0 && (
            <tr aria-hidden="true">
              <td colSpan={colCount} style={{ height: padBottom, padding: 0 }} />
            </tr>
          )}
          {rows.length === 0 && (
            <tr>
              <td colSpan={colCount} className="px-3 py-6 text-center text-muted">
                {empty ?? "—"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
