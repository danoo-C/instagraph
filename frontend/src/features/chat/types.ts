import type { components } from "@/api/schema";

/** Generated API schema shortcuts used by the chat pages. */
type S = components["schemas"];

export type PersonRef = S["PersonRef"];
export type ApiMessageQuery = S["MessageQuery"];
export type ApiSeries = S["Series"];
export type ApiTimeSeries = S["TimeSeries"];
export type ApiCategoryChart = S["CategoryChart"];
export type ApiHeatmap = S["HeatmapData"];
export type ApiTile = S["Tile"];
export type MessageRef = S["MessageRef"];
export type ChatHeader = S["ChatHeaderResponse"];
export type ChatListItem = S["ChatListItem"];
export type ChatListResponse = S["ChatListResponse"];
export type ChatSortKey = ChatListResponse["sort"];
export type ChatSummary = S["ChatSummaryResponse"];
export type ChatPersonRow = S["ChatPersonRow"];
export type ChatTimeline = S["ChatTimelineResponse"];
export type TimelineEvent = S["TimelineEvent"];
export type ChatWhen = S["ChatWhenResponse"];
export type ChatDynamics = S["DynamicsResponse"];
export type PersonReplyStats = S["PersonReplyStats"];
export type PersonEffort = S["PersonEffort"];
export type Bucket = ApiTimeSeries["bucket"];
