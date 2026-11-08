import { createContext, useContext } from "react";
import type { EChartsType } from "echarts";

export interface ChartSlot {
  /** EChart registers its instance here so the card can export PNG. */
  register: (chart: EChartsType | null) => void;
  /** Accessible label (the card title). */
  label: string;
  /** True inside the fullscreen dialog (EChart grows to fill it). */
  fullscreen: boolean;
}

export const ChartSlotContext = createContext<ChartSlot | null>(null);

export function useChartSlot(): ChartSlot | null {
  return useContext(ChartSlotContext);
}
