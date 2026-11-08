import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFns,
  tableFeatures,
  type ColumnDef,
  type RowData,
  type SortingState,
} from "@tanstack/react-table";

/** Feature set used by DataTable. Columns must be typed against it. */
export const dataTableFeatures = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns,
});
export type DataTableFeatures = typeof dataTableFeatures;

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- column value types vary per column
export type DataTableColumn<T extends RowData> = ColumnDef<DataTableFeatures, T, any>;
export type { SortingState };

/**
 * Column helper bound to DataTable's features:
 * `const col = dataTableColumnHelper<Row>(); const columns = col.columns([col.accessor("name", { header: "Name" })])`.
 */
export function dataTableColumnHelper<T extends RowData>() {
  return createColumnHelper<DataTableFeatures, T>();
}
