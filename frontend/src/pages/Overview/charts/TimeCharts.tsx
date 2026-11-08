import { timeSeriesTable } from "@/charts/tables";
import { useMemo, useState } from "react";
import { useApi } from "@/api/useApi";
import { ChartCard } from "@/charts/ChartCard";
import { EChart } from "@/charts/EChart";
import { Tile } from "@/components/Tile";
import { bucketRange } from "@/features/overview/query";
import { toMessageQuery, useOpenMessages } from "@/lib/drilldown";
import { Segmented } from "@/components/Segmented";
import { isEmptySeries } from "@/features/overview/tables";
import {
  lineOption,
  replyTimesOption,
  sentReceivedOption,
  timelineOption,
  typesOption,
} from "@/features/overview/transforms";
import type { Bucket } from "@/features/overview/types";

const BUCKET_OPTIONS = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
] as const;

type ClickParams = { componentType?: string; seriesId?: string; dataIndex?: number };

function BucketToggle({ value, onChange }: { value: Bucket; onChange: (b: Bucket) => void }) {
  return (
    <Segmented
      label="Time bucket"
      value={value as "day" | "week" | "month"}
      options={BUCKET_OPTIONS}
      onChange={onChange}
    />
  );
}

/** 1. Messages over time, stacked by the top 10 chats + others. */
export function TimelineCard() {
  const [bucket, setBucket] = useState<Bucket>("week");
  const q = useApi("/api/overview/timeline", { query: { bucket } });
  const open = useOpenMessages();
  const ts = q.data?.timeline;
  const option = useMemo(() => (ts ? timelineOption(ts) : {}), [ts]);
  return (
    <ChartCard
      title="Messages over time"
      info="Messages per day/week/month, stacked by your 10 busiest chats plus everything else (gray). Scroll with Shift+wheel or drag the slider to zoom; click an area to open those messages. Long ranges may be shown in coarser buckets."
      query={q}
      empty={q.isSuccess && isEmptySeries(ts)}
      data={timeSeriesTable(ts)}
      actions={<BucketToggle value={bucket} onChange={setBucket} />}
      className="xl:col-span-2"
    >
      <EChart
        option={option}
        height={360}
        onEvents={{
          click: (p: ClickParams) => {
            if (!ts || p.dataIndex == null || !p.seriesId) return;
            const x = ts.x[p.dataIndex];
            if (!x) return;
            open(toMessageQuery(q.data?.drilldowns[p.seriesId], bucketRange(x, ts.bucket)));
          },
        }}
      />
    </ChartCard>
  );
}

/** 2. Sent vs received, mirrored bars. */
export function SentReceivedCard() {
  const [bucket, setBucket] = useState<Bucket>("month");
  const q = useApi("/api/overview/sent-received", { query: { bucket } });
  const open = useOpenMessages();
  const ts = q.data?.timeline;
  const option = useMemo(() => (ts ? sentReceivedOption(ts) : {}), [ts]);
  return (
    <ChartCard
      title="Sent vs received"
      info="Messages you sent (up) and messages others sent (down) per bucket. Click a bar to open those messages."
      query={q}
      empty={q.isSuccess && isEmptySeries(ts)}
      data={timeSeriesTable(ts)}
      actions={<BucketToggle value={bucket} onChange={setBucket} />}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: ClickParams) => {
            if (!ts || p.dataIndex == null || !p.seriesId) return;
            const x = ts.x[p.dataIndex];
            if (x) open(toMessageQuery(q.data?.drilldowns[p.seriesId], bucketRange(x, ts.bucket)));
          },
        }}
      />
    </ChartCard>
  );
}

/** Generic time-series card for charts where a click opens that bucket's messages. */
function SimpleSeriesCard({
  title,
  info,
  path,
  bar,
  drill = true,
}: {
  title: string;
  info: string;
  path:
    "/api/overview/cumulative" | "/api/overview/new-conversations" | "/api/overview/active-chats";
  bar?: boolean;
  drill?: boolean;
}) {
  const q = useApi(path);
  const open = useOpenMessages();
  const ts = q.data?.timeline;
  const option = useMemo(() => (ts ? lineOption(ts, { bar, area: !bar }) : {}), [ts, bar]);
  return (
    <ChartCard
      title={title}
      info={info}
      query={q}
      empty={q.isSuccess && isEmptySeries(ts)}
      data={timeSeriesTable(ts)}
    >
      <EChart
        option={option}
        onEvents={
          drill
            ? {
                click: (p: ClickParams) => {
                  if (!ts || p.dataIndex == null) return;
                  const x = ts.x[p.dataIndex];
                  if (x) open(bucketRange(x, ts.bucket));
                },
              }
            : undefined
        }
      />
    </ChartCard>
  );
}

/** 9. Cumulative messages. */
export function CumulativeCard() {
  return (
    <SimpleSeriesCard
      title="Cumulative messages"
      info="Running total of all messages in the selected range."
      path="/api/overview/cumulative"
      drill={false}
    />
  );
}

/** 10. New conversations per month. */
export function NewConversationsCard() {
  return (
    <SimpleSeriesCard
      title="New people per month"
      info="People you exchanged a first message with that month (in any chat). Click a bar to open that month's messages."
      path="/api/overview/new-conversations"
      bar
    />
  );
}

/** 11. Active chats per month. */
export function ActiveChatsCard() {
  return (
    <SimpleSeriesCard
      title="Active chats per month"
      info="Number of chats with at least one message in that month. Click a point to open that month's messages."
      path="/api/overview/active-chats"
    />
  );
}

/** 12. Message type mix over time (100% stacked area). */
export function TypesCard() {
  const q = useApi("/api/overview/types");
  const open = useOpenMessages();
  const ts = q.data?.timeline;
  const option = useMemo(() => (ts ? typesOption(ts) : {}), [ts]);
  return (
    <ChartCard
      title="Message types over time"
      info="Share of each message type (text, photo, video, voice note, share, call, …) per month. The CSV holds the raw counts. Click an area to open those messages."
      query={q}
      empty={q.isSuccess && isEmptySeries(ts)}
      data={timeSeriesTable(ts)}
    >
      <EChart
        option={option}
        onEvents={{
          click: (p: ClickParams) => {
            if (!ts || p.dataIndex == null || !p.seriesId) return;
            const x = ts.x[p.dataIndex];
            if (x) open({ types: p.seriesId, ...bucketRange(x, ts.bucket) });
          },
        }}
      />
    </ChartCard>
  );
}

/** 15. My median reply time over time. */
export function ReplyTimesCard() {
  const q = useApi("/api/overview/reply-times", { query: { bucket: "month" } });
  const ts = q.data?.over_time;
  const option = useMemo(() => (ts ? replyTimesOption(ts) : {}), [ts]);
  return (
    <ChartCard
      title="Reply times over time"
      info="Median time until you reply (me) and until others reply to you (others), per month. Only replies within the reply window count (see Settings)."
      query={q}
      empty={q.isSuccess && isEmptySeries(ts)}
      data={timeSeriesTable(ts)}
    >
      <div className="mb-2 grid grid-cols-2 gap-2">
        {q.data?.tiles.map((t) => (
          <Tile
            key={t.key}
            label={t.label}
            value={t.value == null ? null : Number(t.value) / 1000}
            format="duration"
            hint={t.detail}
            query={t.drilldown ? toMessageQuery(t.drilldown) : undefined}
          />
        ))}
      </div>
      <EChart option={option} height={280} />
    </ChartCard>
  );
}
