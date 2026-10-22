import "./styles.css";
import { api, asApiError } from "./api";
import { createLeaderboard } from "./tabs/leaderboard";
import { initMeta } from "./meta";
import { createFreshness } from "./ui/freshness";
import { createLab } from "./tabs/lab";
import { createLive } from "./tabs/live";
import { createStats } from "./tabs/stats";
import { createPortfolio } from "./tabs/portfolio";
import type { TabFactory } from "./tabs/tab";
import { createTabHost } from "./tabHost";
import { createWalkForward } from "./tabs/walkforward";
import { currentRoute, ROUTES, setRoute, type Route } from "./state";
import type { Meta } from "./types";
import { errorBanner } from "./ui/banner";
import { h } from "./ui/dom";
import { jobsIndicator } from "./ui/jobs";
import { themeSwitch } from "./ui/theme";
import { githubLink } from "./ui/githubLink";
import { langSwitch } from "./ui/langSwitch";
import { applyLangToDocument, t } from "./i18n";
import { botStatusPill } from "./ui/botStatus";
import { brandMark } from "./ui/icons";
import { kbd, skeletonChart } from "./ui/kit";
import { installPalette, setPaletteContext } from "./ui/palette";
import { installShortcuts } from "./ui/shortcuts";

const TITLES: Record<Route, string> = {
  lab: t("tab.lab"),
  portfolio: t("tab.portfolio"),
  wf: t("tab.wf"),
  best: t("tab.best"),
  live: t("tab.live"),
  stats: t("tab.stats"),
};
const FACTORIES: Record<Route, TabFactory> = {
  lab: createLab,
  portfolio: createPortfolio,
  wf: createWalkForward,
  best: createLeaderboard,
  live: createLive,
  stats: createStats,
};

const app = document.getElementById("app")!;

function shell() {
  const links = new Map<Route, HTMLAnchorElement>();
  const nav = h("nav", { class: "tabs", role: "tablist", "aria-label": t("tab.nav") });
  const ind = h("span", { class: "tab-ind", "aria-hidden": "true" });
  nav.append(ind);
  ROUTES.forEach((r, i) => {
    const a = h("a", {
      href: `#/${r}`, role: "tab", id: `tab-${r}`, "aria-controls": "content", "aria-keyshortcuts": String(i + 1),
      title: t("tab.key_title", { title: TITLES[r], n: i + 1 }),
    }, h("span", { text: TITLES[r] }), kbd(String(i + 1), true));
    a.addEventListener("click", (e) => { e.preventDefault(); setRoute(r); });
    links.set(r, a);
    nav.append(a);
  });
  // WAI-ARIA Tabs: стрелки/Home/End переключают вкладки (roving tabindex — в show()).
  nav.addEventListener("keydown", (e) => {
    const i = ROUTES.indexOf(currentRoute());
    let j = -1;
    if (e.key === "ArrowRight") j = (i + 1) % ROUTES.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + ROUTES.length) % ROUTES.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = ROUTES.length - 1;
    if (j < 0) return;
    e.preventDefault();
    const r = ROUTES[j]!;
    setRoute(r);
    links.get(r)?.focus();
  });
  const wrap = h("div", { class: "tabs-wrap" }, nav);
  /** Индикатор активной вкладки + затухание краёв, когда вкладки прокручиваются (телефон). */
  const layoutTabs = (scrollToActive = false) => {
    const a = links.get(currentRoute());
    if (a && a.offsetWidth) {
      ind.style.width = a.offsetWidth + "px";
      ind.style.transform = `translateX(${a.offsetLeft}px)`;
      if (scrollToActive) nav.scrollTo({ left: a.offsetLeft - (nav.clientWidth - a.offsetWidth) / 2, behavior: "smooth" });
    }
    wrap.classList.toggle("fade-l", nav.scrollLeft > 2);
    wrap.classList.toggle("fade-r", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
  };
  nav.addEventListener("scroll", () => layoutTabs(), { passive: true });
  new ResizeObserver(() => layoutTabs()).observe(nav);

  const fresh = h("div", { class: "freshness", hidden: true });
  const content = h("main", { class: "content", id: "content" });
  const sc = installShortcuts(TITLES);
  const pal = installPalette();
  setPaletteContext(null, TITLES);
  const header = h("header", { class: "topbar" },
    h("div", { class: "brand" }, h("span", { class: "logo", "aria-hidden": "true" }, brandMark()), h("span", { class: "brand-name", text: "Tradebot" })),
    wrap, h("div", { class: "spacer" }),
    h("div", { class: "topbar-end" }, pal.button, botStatusPill(), jobsIndicator(), sc.button, langSwitch(), themeSwitch(), githubLink()));
  app.replaceChildren(h("a", { class: "skip", href: "#content", text: t("app.skip") }), header, fresh, content);
  return { links, content, fresh, layoutTabs };
}

async function boot() {
  applyLangToDocument();
  const { links, content, fresh, layoutTabs } = shell();
  content.append(h("div", { class: "inline-busy" }, h("div", { class: "spinner" }), h("span", { text: t("app.loading") })), skeletonChart(220));
  let meta: Meta;
  try {
    meta = await api.meta();
  } catch (e) {
    const err = asApiError(e);
    content.replaceChildren(errorBanner(err, () => location.reload()));
    return;
  }
  content.replaceChildren();
  initMeta(meta);
  setPaletteContext(meta, TITLES);

  // создание/показ вкладок под «предохранителем»: упавшая вкладка заменяется баннером со сбросом её настроек, остальные работают (tabHost.ts)
  const host = createTabHost({ meta, factories: FACTORIES, titles: TITLES, content, links, layoutTabs });
  const bar = createFreshness(fresh, () => host.dataContext(), () => host.refresh());   // метаданные уже обновлены хуком api.afterDataChange
  window.addEventListener("hashchange", () => { host.show(); bar.refresh(); });
  host.show();
  bar.refresh();
}

void boot();
