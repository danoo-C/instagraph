import type { components } from "@/api/schema";

/** Shorthand for generated API schemas. */
export type Schemas = components["schemas"];
export type ApiMessageQuery = Schemas["MessageQuery"];
export type ApiPersonRef = Schemas["PersonRef"];
export type ApiUnit = NonNullable<Schemas["Tile"]["unit"]>;
export type ApiScalar = Schemas["Tile"]["value"];
export type ApiTimeSeries = Schemas["TimeSeries"];
export type ApiCategoryChart = Schemas["CategoryChart"];
