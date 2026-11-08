import { useEffect } from "react";
import { NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { useApi } from "@/api/useApi";
import { IMPORT_NAV, NAV_ITEMS, type NavItem } from "@/app/nav";
import { FilterBar } from "@/components/filters/FilterBar";
import { CloseIcon, MenuIcon, MoonIcon, SunIcon } from "@/components/icons";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { FILTER_KEYS } from "@/state/filters";
import { useResolvedTheme, useThemeStore } from "@/state/theme";
import { useUiStore } from "@/state/ui";

/** Year of an ISO timestamp in the browser's time zone (undefined when missing/invalid). */
function yearOf(ts: string | null | undefined): number | undefined {
  if (!ts) return undefined;
  const y = new Date(ts).getFullYear();
  return Number.isNaN(y) ? undefined : y;
}

/** FilterBar fed with the dataset's year span (from `GET /api/status`). */
function DatasetFilterBar() {
  const status = useApi("/api/status", undefined, { filters: false, staleTime: 60_000 });
  return (
    <FilterBar firstYear={yearOf(status.data?.first_ts)} lastYear={yearOf(status.data?.last_ts)} />
  );
}

/** Keep global filter params when navigating between sections. */
function useFilterSearch(): string {
  const [params] = useSearchParams();
  const out = new URLSearchParams();
  for (const k of FILTER_KEYS) {
    const v = params.get(k);
    if (v != null) out.set(k, v);
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

function SideLink({ item, search }: { item: NavItem; search: string }) {
  const t = useT();
  return (
    <NavLink
      to={{ pathname: item.to, search }}
      className={({ isActive }) =>
        cn(
          "rounded-md px-3 py-2 text-sm font-medium",
          isActive ? "bg-accent/10 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
        )
      }
    >
      {t(item.label)}
    </NavLink>
  );
}

function Sidebar() {
  const t = useT();
  const search = useFilterSearch();
  const open = useUiStore((s) => s.sidebarOpen);
  const setOpen = useUiStore((s) => s.setSidebarOpen);
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          aria-hidden="true"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-56 shrink-0 flex-col gap-1 border-r border-border bg-surface p-3 transition-transform md:static md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Main navigation"
      >
        <div className="mb-3 flex items-center justify-between px-2">
          <span className="text-lg font-bold tracking-tight">{t("app.name")}</span>
          <button
            type="button"
            className="icon-btn md:hidden"
            aria-label={t("nav.close")}
            onClick={() => setOpen(false)}
          >
            <CloseIcon />
          </button>
        </div>
        <nav className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <SideLink key={item.to} item={item} search={search} />
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-2 border-t border-border pt-3">
          <SideLink item={IMPORT_NAV} search="" />
          <p className="px-3 text-xs text-muted">{t("privacy.local")}</p>
        </div>
      </aside>
    </>
  );
}

function ThemeToggle() {
  const t = useT();
  const theme = useResolvedTheme();
  const setTheme = useThemeStore((s) => s.setTheme);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={`${t("theme.toggle")} (${t(next === "dark" ? "theme.dark" : "theme.light")})`}
      title={t("theme.toggle")}
      onClick={() => setTheme(next)}
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function TopBar() {
  const t = useT();
  const toggle = useUiStore((s) => s.toggleSidebar);
  return (
    <header
      data-topbar
      className="sticky top-0 z-20 flex items-center gap-2 border-b border-border bg-surface/95 px-3 py-2 backdrop-blur sm:px-4"
    >
      <button
        type="button"
        className="icon-btn md:hidden"
        aria-label={t("nav.menu")}
        onClick={toggle}
      >
        <MenuIcon />
      </button>
      <div className="min-w-0 flex-1">
        <DatasetFilterBar />
      </div>
      <ThemeToggle />
    </header>
  );
}

/** App shell: left sidebar + top filter bar + routed page. */
export function Layout() {
  const { pathname } = useLocation();
  const setOpen = useUiStore((s) => s.setSidebarOpen);
  useEffect(() => setOpen(false), [pathname, setOpen]);
  return (
    <div className="flex h-full">
      <Sidebar />
      {/* `relative`: anchors absolutely positioned descendants (e.g. `sr-only` labels) to
          this scroller; otherwise they are placed relative to <body> and make the whole
          page scroll. */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        <TopBar />
        <main id="main" className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
