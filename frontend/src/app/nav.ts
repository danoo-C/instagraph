import type { TranslationKey } from "@/i18n";

export interface NavItem {
  to: string;
  label: TranslationKey;
}

/** Sidebar entries (§5.1). */
export const NAV_ITEMS: NavItem[] = [
  { to: "/overview", label: "nav.overview" },
  { to: "/chats", label: "nav.chats" },
  { to: "/people", label: "nav.people" },
  { to: "/compare", label: "nav.compare" },
  { to: "/wrapped", label: "nav.wrapped" },
  { to: "/search", label: "nav.search" },
  { to: "/ai", label: "nav.ai" },
  { to: "/settings", label: "nav.settings" },
];

export const IMPORT_NAV: NavItem = { to: "/import", label: "nav.import" };
