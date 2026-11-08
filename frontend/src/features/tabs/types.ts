import type { components } from "@/api/schema";

/** Short aliases for generated API schema types used by the chat tabs. */
type S = components["schemas"];

export type PersonRef = S["PersonRef"];
export type Series = S["Series"];
export type TimeSeries = S["TimeSeries"];
export type CategoryChart = S["CategoryChart"];
export type HeatmapData = S["HeatmapData"];
export type ApiTile = S["Tile"];
export type ApiMessageQuery = S["MessageQuery"];
export type MessageRef = S["MessageRef"];
export type Unit = NonNullable<S["Tile"]["unit"]>;
