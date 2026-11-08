import type { components } from "@/api/schema";

type S = components["schemas"];

export type ApiMessageQuery = S["MessageQuery"];
export type ApiTile = S["Tile"];
export type ApiSeries = S["Series"];
export type ApiTimeSeries = S["TimeSeries"];
export type ApiCategoryChart = S["CategoryChart"];
export type ApiHeatmap = S["HeatmapData"];
export type ApiCalendar = S["CalendarData"];
export type ApiChatRef = S["ChatRef"];
export type ApiPersonRef = S["PersonRef"];
export type Bucket = ApiTimeSeries["bucket"];
export type ContactMetric = S["ContactsResponse"]["metric"];
export type ApiContactItem = S["ContactItem"];
export type ApiEmojiCount = S["OverviewEmojisResponse"]["top"][number];
export type ApiSizeBin = S["SizeBinOut"];
export type ApiGraph = S["SocialGraphResponse"];
export type ApiYearRow = S["YearRow"];
export type ApiAward = S["Award"];
