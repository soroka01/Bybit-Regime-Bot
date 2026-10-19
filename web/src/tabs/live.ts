// Вкладка «Бот (live)»: панель управления торговлей «с одного взгляда». Только чтение снимка /api/live; управление — исключительно в Telegram.
// Состав: liveCalc.ts (чистая логика: P/L за сутки, обратный отсчёт, стоп/ликвидация, пороги риска), liveView.ts (секции), здесь — опрос, график, тикер.
import { api, asApiError } from "../api";
import { alignSeries, stride } from "../charts/align";
import { lineChart, type LineChart } from "../charts/line";
import { persisted } from "../state";
import type { LiveSnapshot, Meta } from "../types";
import { errorBanner } from "../ui/banner";
import { segmented } from "../ui/controls";
import { h } from "../ui/dom";
import { icon } from "../ui/icons";
import { emptyState, pageHead, skeletonChart, skeletonTile } from "../ui/kit";
import { publishLive } from "../ui/botStatus";
import { num, usd } from "../ui/fmt";
import {
  closedKey, heartbeatState, isEventFilter, isRange, newClosedKeys, parseSeries, RANGES, tradeMarkers, type EventFilter, type HbTone, type RangeKey, filterEvents,
} from "./liveCalc";
import {
  closedTradesCard, closedTradesLoading, eventsList, fallbackHint, heroCard, killedBanner, noSnapshot, positionCard, riskCard, signalsCard, staleBanner, tickAll, tradesCard, type ClosedState,
} from "./liveView";
import { resolveWindow, statsNeeded, type Feeds, type FullRange } from "./liveWindow";
import { loadStats } from "./statsFeed";
import type { Tab } from "./tab";

const REFRESH_MS = 10_000;
const SUB = "Только просмотр: управление ботом — через Telegram (/start). Данные обновляются автоматически.";
const TRADES_STEP = 20;
const CLOSED_REFRESH_MS = 60_000;     // закрытые сделки (round trips из /api/stats?range=30d): раз в минуту, пока вкладка видна
const CLOSED_FRESH_MS = 30_000;       // ответ, который вкладка «Статистика» получила недавно, берём из общего кэша
const NEW_TRADE_MS = 10_000;          // подсветка новой закрытой сделки
const FULL_REFRESH_MS = 60_000;       // полная история для «Всё» / «7 д» (/api/stats?range=all|7d): раз в минуту, пока вкладка видна
const FULL_FRESH_MS = 30_000;         // ответ, полученный вкладкой «Статистика» недавно, берётся из общего кэша

interface LState { range: RangeKey; ev: EventFilter }

export function createLive(meta: Meta): Tab {
  const P = persisted<LState>("live", { range: "24h", ev: "all" }, { range: isRange, ev: isEventFilter });
  const S = P.state;
  const root = h("div", { class: "page live" });
  const chartHost = h("div", { class: "chart" });
  let chart: LineChart | null = null;
  let timer = 0;
  let tickTimer = 0;
  let ctl: AbortController | null = null;
  let visible = false;
  let lastJson = "";            // последний отрисованный снимок: если не изменился — DOM не трогаем
  let lastSnap: LiveSnapshot | null = null;
  let renderedHb: HbTone | null = null;
  let heroEl: HTMLElement | null = null;     // герой пересобирается при смене периода графика (чип результата за выбранное окно)
  let tradesShown = TRADES_STEP;
  // закрытые сделки: состояние блока, ключи уже виденных сделок (null — первый ответ ещё не пришёл), подсвеченные новые
  let closed: ClosedState = { kind: "loading" };
  let knownClosed: Set<string> | null = null;
  const freshClosed = new Set<string>();
  let closedTimer = 0, closedSeq = 0;
  const feeds: Feeds = {};                   // ответы /api/stats для окон «Всё» и «7 д» (снимок /api/live несёт только ~2000 последних точек)
  let fullTimer = 0;
  const tradesSlot = h("div", { class: "lv-trades-slot" });
  const idle = { open: false, onToggle: (o: boolean) => { idle.open = o; } };   // «Неактивные ноги» не сворачиваются при автообновлении
  const lastFetch = h("span", { class: "muted small lv-updated" });

  const head = () => pageHead("Бот (live)", SUB, [lastFetch,
    h("button", { class: "btn ghost icon-btn sm", type: "button", "aria-label": "Обновить сейчас", "data-tip": "Обновить сейчас", "data-tip-pos": "left", onClick: () => void refresh() }, icon("refresh", 15))]);

  // ---------- график капитала
  const rangeSeg = segmented({ label: "Период графика", options: RANGES.map((r) => ({ value: r.key, label: r.label })), value: S.range, onChange: (v) => { if (isRange(v)) { S.range = v; P.save(); repaintWindow(); void loadFull().then(fullLoop); } } });
  rangeSeg.el.classList.add("lv-range");
  const markerLegend = h("div", { class: "marker-legend lv-mk", hidden: true });
  const chartBody = h("div", { class: "lv-chart-body" });
  const rangeNote = h("p", { class: "muted small lv-range-note", hidden: true });       // «Полная история доступна после обновления бота» (только 404)
  const chartCard = h("section", { class: "card lv-chart", "aria-label": "График капитала" },
    h("header", { class: "card-head" }, h("h3", { text: "Кошелёк счёта" }), rangeSeg.el), rangeNote, chartBody, markerLegend);

  function updateChart(s: LiveSnapshot) {
    const eqAll = parseSeries(s.equity_history);
    const win = resolveWindow(S.range, eqAll, s.equity, Date.now(), feeds);       // то же окно, что в чипе героя
    rangeNote.textContent = win.note ?? "";
    rangeNote.hidden = !win.note;
    if (win.state === "loading") {
      chart?.destroy(); chart = null;
      chartBody.replaceChildren(skeletonChart(300));
      markerLegend.hidden = true;
      return;
    }
    const eq = win.series;
    if (!eq.length) {
      chart?.destroy(); chart = null;
      chartBody.replaceChildren(emptyState("История капитала пока пуста", "График появится, когда бот запишет первые точки.", null, { icon: "chart" }));
      markerLegend.hidden = true;
      return;
    }
    if (!chartBody.contains(chartHost)) chartBody.replaceChildren(chartHost);
    const sec = (p: { t: number }) => Math.floor(p.t / 1000);
    const a = alignSeries([{ t: stride(eq.map(sec)), v: stride(eq.map((p) => p.v)) }]);
    chart ??= lineChart(chartHost, {
      height: 300, yFmt: (v) => num(v, v >= 1000 ? 0 : 2),
      series: [{ label: "Капитал", color: "--c1", width: 2, fmt: (v) => usd(v) }],
    });
    chart.setData(a.x, a.ys);
    const mk = tradeMarkers(s.trades, eq, win.start);
    chart.setMarkers(mk);
    markerLegend.hidden = !mk.length;
    markerLegend.replaceChildren(
      h("span", { class: "lg-item" }, h("i", { class: "mk up", "aria-hidden": "true" }), "Покупка (ордер)"),
      h("span", { class: "lg-item" }, h("i", { class: "mk down", "aria-hidden": "true" }), "Продажа (ордер)"),
      h("span", { class: "muted small", text: `${mk.length} в выбранном окне${win.source === "stats" ? " (из последних ордеров снимка)" : ""} · цвет маркера — покупка/продажа, не прибыль и не лонг/шорт` }));
  }

  // ---------- события
  const evSeg = segmented({ label: "Фильтр событий", options: [{ value: "all", label: "Все" }, { value: "alerts", label: "Предупреждения и ошибки" }, { value: "trades", label: "Сделки" }], value: S.ev, onChange: (v) => { if (isEventFilter(v)) { S.ev = v; P.save(); if (lastSnap) paintEvents(lastSnap); } } });
  evSeg.el.classList.add("lv-range", "lv-evseg");
  const evHost = h("div", { class: "lv-ev-host" });
  const evCount = h("span", { class: "muted small" });
  const eventsCard = h("section", { class: "card lv-events", "aria-label": "События" },
    h("header", { class: "card-head" }, h("h3", { text: "События" }), evCount), evSeg.el, evHost);
  function paintEvents(s: LiveSnapshot) {
    evHost.replaceChildren(eventsList(s.events, S.ev));
    const total = (s.events ?? []).length, shown = filterEvents(s.events, S.ev).length;
    evCount.textContent = total ? (S.ev === "all" ? `${total}` : `${shown} из ${total}`) : "";
  }

  // ---------- закрытые сделки (или откат к списку ордеров)
  const moreTrades = () => { tradesShown += TRADES_STEP; paintTrades(); };
  function paintTrades() {
    const s = lastSnap;
    if (!s?.available) return;
    const st = closed;
    if (st.kind === "loading") tradesSlot.replaceChildren(closedTradesLoading());
    else if (st.kind === "ok") tradesSlot.replaceChildren(closedTradesCard(s, st.res, { limit: tradesShown, onMore: moreTrades, fresh: freshClosed }));
    else tradesSlot.replaceChildren(tradesCard(s.trades, tradesShown, moreTrades, fallbackHint(st)));
  }

  async function loadClosed() {
    const my = ++closedSeq;
    try {
      const res = await loadStats("30d", { maxAgeMs: CLOSED_FRESH_MS });
      if (my !== closedSeq) return;
      if (!res.available && res.reason !== "no_trades") {
        closed = { kind: "fallback", why: "unavailable", message: res.message };
      } else {
        const rows = res.trades?.rows ?? [];
        const added = newClosedKeys(knownClosed, rows);
        knownClosed = new Set(rows.map(closedKey));
        for (const k of added) freshClosed.add(k);
        if (added.length) window.setTimeout(() => { for (const k of added) freshClosed.delete(k); paintTrades(); }, NEW_TRADE_MS);
        closed = { kind: "ok", res };
      }
    } catch (e) {
      const a = asApiError(e);
      if (a.silent || my !== closedSeq) return;
      if (a.status === 404) closed = { kind: "fallback", why: "old_backend" };
      else if (closed.kind !== "ok") closed = { kind: "fallback", why: "error", message: a.message };     // уже показанные итоги не затираем из-за разовой ошибки
    }
    paintTrades();
  }
  const closedLoop = () => {
    clearTimeout(closedTimer);
    if (!visible || document.hidden) return;
    closedTimer = window.setTimeout(async () => { if (!visible || document.hidden) return; await loadClosed(); closedLoop(); }, CLOSED_REFRESH_MS);
  };

  // ---------- полная история для «Всё» и «7 д» (когда снимка не хватает)
  /** Какой диапазон /api/stats нужен выбранному окну: «Всё» — всегда, «7 д» — если в снимке меньше 7 суток; 24 ч — никогда. */
  function neededFeed(): FullRange | null {
    if (S.range === "all") return "all";
    if (!lastSnap?.available) return null;
    const pts = parseSeries(lastSnap.equity_history);
    return statsNeeded(S.range, pts, pts[pts.length - 1]?.t ?? Date.now());
  }
  const repaintWindow = () => { if (lastSnap?.available) { updateChart(lastSnap); paintHero(lastSnap); } };
  async function loadFull() {
    const need = neededFeed();
    if (!need) return;
    if (!feeds[need]) { feeds[need] = { kind: "loading" }; repaintWindow(); }
    try {
      const res = await loadStats(need, { maxAgeMs: FULL_FRESH_MS });
      if (res.available && res.equity) feeds[need] = { kind: "ok", res };
      else if (feeds[need]?.kind !== "ok") feeds[need] = { kind: "error", message: res.message };         // уже полученную историю разовый сбой не затирает
    } catch (e) {
      const a = asApiError(e);
      if (a.silent) { if (feeds[need]?.kind === "loading") delete feeds[need]; return; }
      if (a.status === 404) feeds[need] = { kind: "missing" };
      else if (feeds[need]?.kind !== "ok") feeds[need] = { kind: "error", message: a.message };
    }
    repaintWindow();
  }
  const fullLoop = () => {
    clearTimeout(fullTimer);
    if (!visible || document.hidden) return;
    fullTimer = window.setTimeout(async () => { if (!visible || document.hidden) return; await loadFull(); fullLoop(); }, FULL_REFRESH_MS);
  };

  // ---------- сборка страницы
  function paintHero(s: LiveSnapshot) {
    if (!heroEl?.isConnected || !s.available) return;
    const next = heroCard(s, parseSeries(s.equity_history), Date.now(), S.range, feeds);
    heroEl.replaceWith(next);
    heroEl = next;
  }
  function render(s: LiveSnapshot) {
    lastSnap = s;
    if (!s.available) {
      chart?.destroy(); chart = null;
      renderedHb = null;
      root.replaceChildren(head(), noSnapshot(s.reason));
      return;
    }
    const now = Date.now();
    const eq = parseSeries(s.equity_history);
    renderedHb = heartbeatState(s.heartbeat, now).tone;
    root.replaceChildren(
      head(), staleBanner(s) ?? "", killedBanner(s) ?? "",
      (heroEl = heroCard(s, eq, now, S.range, feeds)),
      h("div", { class: "lv-cols" }, positionCard(s), signalsCard(s, meta, now, idle)),
      riskCard(s, eq, now),
      chartCard,
      tradesSlot, eventsCard);
    paintTrades();
    updateChart(s);
    paintEvents(s);
    const need = neededFeed();
    if (need && !feeds[need]) void loadFull().then(fullLoop);       // «7 д» с неполным снимком: первый запрос полной истории (дальше — по таймеру)
  }

  async function refresh() {
    ctl?.abort();
    const c = (ctl = new AbortController());
    try {
      const s = await api.live(c.signal);
      publishLive(s);
      // та же картинка (и минутная корзина для «N мин назад») — DOM и график не трогаем
      const sig = JSON.stringify(s) + "|" + Math.floor(Date.now() / 60_000);
      if (sig !== lastJson) { lastJson = sig; render(s); }
      lastFetch.textContent = `обновлено ${new Date().toLocaleTimeString("ru-RU")} · авто каждые 10 с`;
    } catch (e) {
      if (c.signal.aborted) return;
      lastJson = "";
      lastSnap = null;
      chart?.destroy(); chart = null;
      root.replaceChildren(head(), errorBanner(asApiError(e), refresh));
    }
  }

  /** Раз в секунду: обратный отсчёт и «N с назад»; если сигнал жизни перешёл через порог — перерисовать страницу (баннер «не отвечает»). */
  function tick() {
    if (!visible || document.hidden) return;
    tickAll(root);
    if (lastSnap?.available && renderedHb != null && heartbeatState(lastSnap.heartbeat, Date.now()).tone !== renderedHb) { lastJson = ""; render(lastSnap); }
  }

  // Опрос не идёт в скрытой вкладке браузера; при возврате — сразу обновляемся и запускаем цикл заново.
  const loop = () => {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    timer = window.setTimeout(async () => {
      if (!visible || document.hidden) return;
      await refresh();
      loop();
    }, REFRESH_MS);
  };
  root.append(head(), h("div", { class: "cards lv-skel" }, ...Array.from({ length: 4 }, skeletonTile)), skeletonChart(260));
  document.addEventListener("visibilitychange", () => {
    if (!visible) return;
    if (document.hidden) { clearTimeout(timer); clearTimeout(closedTimer); clearTimeout(fullTimer); ctl?.abort(); } else { tick(); void refresh().then(loop); void loadClosed().then(closedLoop); void loadFull().then(fullLoop); }
  });

  return {
    el: root,
    show() { visible = true; clearInterval(tickTimer); tickTimer = window.setInterval(tick, 1000); void refresh().then(loop); void loadClosed().then(closedLoop); void loadFull().then(fullLoop); },
    hide() { visible = false; clearTimeout(timer); clearTimeout(closedTimer); clearTimeout(fullTimer); clearInterval(tickTimer); ctl?.abort(); },
  };
}
