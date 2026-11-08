import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import {
  parseBrowserParams,
  writeBrowserParams,
  type BrowserQuery,
} from "@/features/messages/query";

/** Message Browser query stored in the URL. `update(patch)` merges and writes it back. */
export function useBrowserQuery(): [
  BrowserQuery,
  (patch: Partial<BrowserQuery>, opts?: { replace?: boolean }) => void,
] {
  const [params, setParams] = useSearchParams();
  const key = params.toString();
  const query = useMemo(() => parseBrowserParams(new URLSearchParams(key)), [key]);
  const update = useCallback(
    (patch: Partial<BrowserQuery>, opts?: { replace?: boolean }) =>
      setParams((prev) => writeBrowserParams({ ...parseBrowserParams(prev), ...patch }, prev), {
        replace: opts?.replace,
      }),
    [setParams],
  );
  return [query, update];
}
