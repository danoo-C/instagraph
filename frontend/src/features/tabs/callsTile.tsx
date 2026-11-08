import { Tile } from "@/components/Tile";
import { apiTileValue } from "@/features/tabs/callsHelpers";
import { toMessageQuery } from "@/lib/drilldown";
import type { ApiTile } from "@/features/tabs/types";

/** Render a backend Tile (unit-aware, clickable when it has a drilldown). */
export function ApiTileView({ tile, timeZone }: { tile: ApiTile; timeZone?: string }) {
  return (
    <Tile
      label={tile.label}
      value={apiTileValue(tile, timeZone)}
      format="text"
      hint={tile.detail ?? tile.person?.name}
      query={tile.drilldown ? toMessageQuery(tile.drilldown) : undefined}
    />
  );
}
