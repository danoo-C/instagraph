import type { ReactNode } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "@/api/queryClient";
import { useBackendPreferences } from "@/state/settingsSync";
import { useApplyTheme } from "@/state/theme";

function ThemeSync() {
  useApplyTheme();
  useBackendPreferences();
  return null;
}

/** App-wide providers (the router wraps these, see router.tsx). */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <Tooltip.Provider delayDuration={200}>
        <ThemeSync />
        {children}
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}
