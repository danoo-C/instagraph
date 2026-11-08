/**
 * Single place where ECharts is imported and extended. Import `echarts` from here,
 * never directly, so the word-cloud extension and themes are registered exactly once.
 */
import * as echarts from "echarts";
import "echarts-wordcloud";
import { buildEchartsTheme, chartThemeName } from "@/charts/theme";

echarts.registerTheme(chartThemeName("light"), buildEchartsTheme("light"));
echarts.registerTheme(chartThemeName("dark"), buildEchartsTheme("dark"));

export { echarts };
export type { EChartsOption, EChartsType } from "echarts";
