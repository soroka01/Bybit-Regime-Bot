// Вкладка «Статистика»: анализатор РЕАЛЬНОЙ торговой истории бота (GET /api/stats?range=…). Только чтение.
// Состав: statsRange/Buckets/TradeLogic/Money/Markers/Kpi/Warnings/Page.ts (чистая логика), statsView.ts (плитки/карточки/состояния), statsCharts.ts (графики), statsTrades.ts (таблица сделок), здесь — опрос, состояния, шапка.
import { api, asApiError, type ApiError } from "../api";
import { t } from "../i18n";
import { persisted } from "../state";
import type { Meta, StatsRange, StatsResponse } from "../types";
import { h } from "../ui/dom";
import { icon } from "../ui/icons";
import { setLoading, skeletonChart, skeletonTile } from "../ui/kit";
import { createStatsCharts, isEqUnit, type EqUnit } from "./statsCharts";
import { DEFAULT_STATS_RANGE, isStatsRange, STATS_RANGES } from "./statsRange";
import { isStTradeFilter, type StTradeFilter } from "./statsTradeLogic";
import { selectState } from "./statsPage";
import { agoMs } from "../ui/fmt";
import { clockUtc } from "../ui/fmtTime";
import {
  allMetrics, costsCard, heroGrid, pageNotices, positionCard, qualityBlock, reasonCard, reconCard, sideCard, unavailableCard, windowLine,
} from "./statsView";
import { createTradesCard } from "./statsTrades";
import { rememberStats } from "./statsFeed";
import type { DataCtx, Tab } from "./tab";

const REFRESH_MS = 60_000;
/** Ответ моложе этого при возврате на вкладку / в окно не перезапрашивается (сервер всё равно кэширует его на минуту). */
const FRESH_MS = 30_000;
const SUB = t("stats.sub");

interface SState { range: StatsRange; unit: EqUnit; filter: StTradeFilter }

export function createStats(_meta: Meta): Tab {
  const P = persisted<SState>("stats", { range: DEFAULT_STATS_RANGE, unit: "usdt", filter: "all" }, { range: isStatsRange, unit: isEqUnit, filter: isStTradeFilter });
  const S = P.state;
  const root = h("div", { class: "page stats" });

  let res: StatsResponse | null = null;
  let err: ApiError | null = null;
  let lastOk = 0;
  let lastSig = "";
  let selectedN: number | null = null;
  let timer = 0, tickTimer = 0;
  let ctl: AbortController | null = null;
  let visible = false;
  let seq = 0;

  // ---------- шапка (строится один раз)
  const updated = h("span", { class: "muted small st-updated", "aria-live": "off" });
  const refreshBtn = h("button", { class: "btn ghost icon-btn sm", type: "button", "aria-label": t("stats.refresh_now"), "data-tip": t("stats.refresh_now"), "data-tip-pos": "left", onClick: () => void load() }, icon("refresh", 15));
  const chips = new Map<StatsRange, HTMLButtonElement>();
  const rangeBar = h("div", { class: "st-ranges", role: "group", "aria-label": t("stats.period_label") });
  for (const r of STATS_RANGES) {
    const b = h("button", { type: "button", class: "chip-btn", "aria-pressed": String(r.key === S.range), title: r.title, "aria-label": r.title, text: r.label, onClick: () => setRange(r.key) });
    chips.set(r.key, b);
    rangeBar.append(b);
  }
  const qualitySlot = h("div", { class: "st-quality-slot" });
  const windowSlot = h("div", { class: "st-window-slot" });
  const head = h("div", { class: "st-head" },
    h("div", { class: "page-head" },
      h("div", { class: "page-head-text" }, h("h2", { class: "page-title", text: t("stats.title") }), h("p", { class: "page-sub", text: SUB })),
      h("div", { class: "page-actions" }, updated, refreshBtn)),
    h("div", { class: "st-toolbar" }, rangeBar),
    windowSlot, qualitySlot);

  const content = h("div", { class: "st-content" });
  root.append(head, content);

  // ---------- готовое представление (создаётся один раз, обновляется на месте)
  const noticesSlot = h("div", { class: "st-notices" });
  const heroSlot = h("div", { class: "st-hero-slot" });
  const allSlot = h("div", { class: "st-all-slot" });
  const cards = h("div", { class: "st-cards" });
  const staleSlot = h("div", { class: "st-stale" });
  const charts = createStatsCharts({
    getUnit: () => S.unit, setUnit: (u) => { S.unit = u; P.save(); },
    onClearSelection: () => select(null),
  });
  const trades = createTradesCard({
    getFilter: () => S.filter, setFilter: (f) => { S.filter = f; P.save(); },
    onSelect: (n) => { selectedN = n; if (res?.available) charts.setSelected(res, n); },
    onGoto: () => charts.scrollToEquity(),
    fileHint: () => [res?.range ?? S.range],
  });
  const ready = h("div", { class: "st-ready" },
    staleSlot, noticesSlot, heroSlot, allSlot, charts.equity, charts.drawdown,
    h("div", { class: "grid-2 st-pair" }, charts.pnl, charts.hist),
    charts.calendar, cards, trades.el);

  function select(n: number | null) {
    selectedN = n;
    trades.setSelected(n);
    if (res?.available) charts.setSelected(res, n);
  }

  // ---------- состояния
  const skeleton = () => h("div", { class: "st-skel", "aria-hidden": "true" },
    h("div", { class: "st-hero" }, ...Array.from({ length: 8 }, skeletonTile)), skeletonChart(300), skeletonChart(170),
    h("div", { class: "grid-2" }, skeletonChart(250), skeletonChart(250)));

  function paintHead() {
    for (const [k, b] of chips) b.setAttribute("aria-pressed", String(k === S.range));
    const ok = !!res?.available && res.range === S.range;
    windowSlot.replaceChildren(ok ? windowLine(res!) ?? "" : "");
    qualitySlot.replaceChildren(ok ? qualityBlock(res!) : "");
    paintUpdated();
  }
  function paintUpdated() {
    updated.textContent = lastOk ? t("stats.updated", { ago: agoMs(lastOk) }) : "";
  }

  function layoutCards() {
    const vis = [...cards.children].filter((c) => !(c as HTMLElement).hidden) as HTMLElement[];
    vis.forEach((c, i) => c.classList.toggle("span-all", vis.length % 2 === 1 && i === vis.length - 1));
  }

  function render() {
    const state = selectState({ res, err });
    const stale = err && res?.available && state.kind === "error" ? err : null;
    paintHead();
    switch (state.kind) {
      case "loading": content.replaceChildren(skeleton()); return;
      case "error":
        if (stale) break;
        content.replaceChildren(h("div", { class: "banner error st-err", role: "alert", "data-code": state.code },
          h("strong", { text: state.code === "network" ? t("stats.err.network") : t("stats.err.load") }),
          h("span", { text: state.message }),
          h("div", { class: "banner-actions" }, h("button", { class: "btn", type: "button", onClick: () => void load(), text: t("stats.retry") }))));
        return;
      case "unavailable": content.replaceChildren(unavailableCard(state.reason, state.message, () => void load())); return;
      default: break;
    }
    const r = res!;
    staleSlot.replaceChildren(stale ? h("div", { class: "notice warn", role: "status" }, h("strong", { text: t("stats.stale.title") }), h("span", { text: t("stats.stale.shown", { message: stale.message, time: clockUtc(lastOk) }) }),
      h("button", { class: "btn sm", type: "button", onClick: () => void load(), text: t("stats.retry") })) : "");
    noticesSlot.replaceChildren(...pageNotices(r));
    heroSlot.replaceChildren(heroGrid(r));
    allSlot.replaceChildren(allMetrics(r));
    const parts = [positionCard(r), sideCard(r), reasonCard(r), costsCard(r), reconCard(r), charts.daily];
    cards.replaceChildren(...parts.filter((x): x is HTMLElement => !!x));
    charts.update(r, selectedN);
    trades.update(r);
    layoutCards();
    if (content.firstChild !== ready) content.replaceChildren(ready);
  }

  // ---------- сеть
  async function load(): Promise<void> {
    ctl?.abort();
    const c = (ctl = new AbortController());
    const my = ++seq;
    const range = S.range;
    // нет данных или на экране данные ДРУГОГО диапазона: показываем скелетон, а не старые цифры под новой кнопкой периода
    if (!res || res.range !== range) content.replaceChildren(skeleton());
    setLoading(refreshBtn, true);
    root.setAttribute("aria-busy", "true");
    root.classList.add("busy");
    try {
      const r = await api.stats(range, c.signal);
      if (c.signal.aborted || my !== seq) return;
      err = null;
      lastOk = Date.now();
      rememberStats(r, lastOk);
      const sig = JSON.stringify({ ...r, computed_at: 0 });
      if (sig === lastSig && res?.range === r.range && content.firstChild === ready) { paintUpdated(); return; }
      lastSig = sig;
      res = r;
      render();
    } catch (e) {
      if (c.signal.aborted || my !== seq) return;
      const a = asApiError(e);
      if (a.silent) return;
      err = a;
      if (res && res.range !== S.range) res = null;
      lastSig = "";
      render();
    } finally {
      if (my === seq) { setLoading(refreshBtn, false); root.removeAttribute("aria-busy"); root.classList.remove("busy"); }
    }
  }

  function setRange(r: StatsRange) {
    if (r === S.range) return;
    S.range = r;
    P.save();
    selectedN = null;
    trades.setSelected(null);
    paintHead();
    void load().then(() => loop());
  }

  // ---------- таймеры (в скрытой вкладке/окне не работают)
  function loop(delay = REFRESH_MS) {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    timer = window.setTimeout(async () => { if (!visible || document.hidden) return; await load(); loop(); }, delay);
  }
  /** Вкладка снова на виду: свежие данные (< FRESH_MS) показываем как есть и ждём остаток минуты, старые — перезапрашиваем. */
  function resume() {
    const age = Date.now() - lastOk;
    if (lastOk && res && res.range === S.range && !err && age >= 0 && age < FRESH_MS) { paintUpdated(); loop(Math.max(1000, REFRESH_MS - age)); return; }
    void load().then(() => loop());
  }
  const tick = () => { if (visible && !document.hidden) paintUpdated(); };
  document.addEventListener("visibilitychange", () => {
    if (!visible) return;
    if (document.hidden) { clearTimeout(timer); ctl?.abort(); } else resume();
  });

  content.replaceChildren(skeleton());
  paintHead();

  return {
    el: root,
    show() { visible = true; clearInterval(tickTimer); tickTimer = window.setInterval(tick, 1000); resume(); },
    hide() { visible = false; clearTimeout(timer); clearInterval(tickTimer); ctl?.abort(); setLoading(refreshBtn, false); root.classList.remove("busy"); },
    // полоса свежести: после «Подгрузить новые свечи» ответ нужно пересчитать (сервер увидит новые свечи/фандинг)
    dataContext: (): DataCtx => ({ symbol: res?.config?.symbol ?? "BTCUSDT", tfs: ["1h"] }),
    refresh() { void load(); },
  };
}
