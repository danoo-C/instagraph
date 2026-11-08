import { useNavigate } from "react-router-dom";
import { useApi } from "@/api/useApi";
import { ErrorCard } from "@/components/ErrorCard";
import { PersonBadge } from "@/components/PersonBadge";
import { Tile, TileGrid } from "@/components/Tile";
import { toMessageQuery, useOpenMessages } from "@/lib/drilldown";
import { formatDetail, tileDisplay } from "@/features/overview/tiles";
import { formatDate } from "@/lib/format";

const CHAT_TILES = new Set(["chats", "chats_one_to_one", "chats_group", "chats_request"]);

export function HeadlineTiles() {
  const q = useApi("/api/overview");
  const open = useOpenMessages();
  const navigate = useNavigate();

  if (q.error) return <ErrorCard error={q.error} onRetry={() => void q.refetch()} />;

  if (q.isLoading || !q.data)
    return (
      <TileGrid>
        {Array.from({ length: 12 }, (_, i) => (
          <Tile key={i} label="…" value={null} loading />
        ))}
      </TileGrid>
    );

  const data = q.data;
  return (
    <div className="flex flex-col gap-2">
      {data.excluded_requests && (
        <p className="text-sm text-muted" role="note">
          {data.excluded_requests.note}{" "}
          <span className="text-xs">(turn on “Include message requests” in the filter bar)</span>
        </p>
      )}
      <TileGrid>
        {data.tiles.map((t) => {
          const { value, format } = tileDisplay(t);
          let onClick: (() => void) | undefined;
          if (t.drilldown) onClick = () => open(toMessageQuery(t.drilldown));
          else if (CHAT_TILES.has(t.key)) onClick = () => navigate("/chats");
          else if (t.key === "people") onClick = () => navigate("/people");
          else if (t.unit === "count" || t.unit === "per_day") onClick = () => open({});
          const detail = formatDetail(t.detail);
          return (
            <Tile
              key={t.key}
              label={t.label}
              value={value}
              format={format}
              onClick={onClick}
              hint={
                t.person ? (
                  <span className="flex items-center gap-1">
                    <PersonBadge
                      id={t.person.person_id}
                      name={t.person.name}
                      color_index={t.person.color_index}
                      size="xs"
                      showName={false}
                    />
                    {detail ?? t.person.name}
                  </span>
                ) : (
                  detail
                )
              }
            />
          );
        })}
        {data.current_streak.length_days > 0 && (
          <Tile
            label="Current streak"
            value={data.current_streak.length_days}
            hint={`days · since ${formatDate(data.current_streak.start)}`}
            onClick={() => open(toMessageQuery(data.current_streak.drilldown))}
          />
        )}
      </TileGrid>
    </div>
  );
}
