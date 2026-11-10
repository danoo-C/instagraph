import type { ComponentType } from "react";
import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { Layout } from "@/app/Layout";
import { RootRedirect } from "@/app/RootRedirect";

/** Lazy route: each page is its own chunk (ECharts loads only with chart pages). */
const page = (load: () => Promise<{ default: ComponentType }>): Pick<RouteObject, "lazy"> => ({
  lazy: async () => ({ Component: (await load()).default }),
});

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <RootRedirect /> },
      { path: "import", ...page(() => import("@/pages/Import")) },
      { path: "import/report", ...page(() => import("@/pages/ImportReport")) },
      { path: "overview", ...page(() => import("@/pages/Overview")) },
      { path: "chats", ...page(() => import("@/pages/Chats")) },
      { path: "chats/:id/:tab?", ...page(() => import("@/pages/ChatDetail")) },
      { path: "people", ...page(() => import("@/pages/People")) },
      { path: "people/:id", ...page(() => import("@/pages/Person")) },
      { path: "compare", ...page(() => import("@/pages/Compare")) },
      { path: "wrapped", ...page(() => import("@/pages/Wrapped")) },
      { path: "search", ...page(() => import("@/pages/Search")) },
      { path: "ai/:id?", ...page(() => import("@/pages/Ai")) },
      { path: "settings", ...page(() => import("@/pages/Settings")) },
      { path: "*", ...page(() => import("@/pages/NotFound")) },
    ],
  },
];

export const createAppRouter = () => createBrowserRouter(routes);
