// Вкладка «Лучшие стратегии»: рейтинг по OOS. Параметры каждой стратегии подбираются только по IS
// (тот же оптимизатор, что и в Лаборатории); считаем по одной стратегии, карточки появляются по мере готовности.
// Состав: lbState.ts (автомат состояний), lbRank.ts (порядок), lbCard.ts (отрисовка карточки), здесь — «клей»:
// контролы, сетевые вызовы, цикл расчёта.
import { api, ApiError, asApiError } from "../api";
import { MAX_LEVERAGE } from "../leverage";
import { effLeverage, effLongOnly, FEE_PCT, isMarket, MARKET_OPTIONS, marketUi, SPOT_LONG_NOTE } from "../market";
import { onMetaChange } from "../meta";
import { persisted, setPendingLab, setRoute, takeFlag } from "../state";
import type { LbBody, LbItem, Market, Meta } from "../types";
import { errorBanner, showError } from "../ui/banner";
import { copyWithToast, labPayload, metricsOf, sendToBotWithToast } from "../ui/clipboard";
import { segmented, select, toggle } from "../ui/controls";
import { h } from "../ui/dom";
import { pageHead } from "../ui/kit";
import { icon } from "../ui/icons";
import { leverageControl } from "../ui/levControl";
import { spct } from "../ui/fmt";
import { patchKeyed, type KeyedItem } from "../ui/keyed";
import { withBusyRetry } from "../ui/retry";
import { compactCard, itemCard, itemSig, type CardCtx } from "./lbCard";
import { applyVerdictFilter, countVerdicts, groupByVerdict, isVFilter, type VFilter } from "./lbFilter";
import { createSummary, emptyCache, groupHead, howToRead, skeletonCards } from "./lbView";
import { isRank, RANKS, splitRanked, type Rank } from "./lbRank";
import { countMissing, createLbStore, runDisabled, runLabel, statusText, type Entry, type Phase } from "./lbState";
import { capReason, fitDays, LB_DAYS, notifyContext, type Tab } from "./tab";

interface LState {
  symbol: string;
  tf: string;
  days: number;
  rank: Rank;
  longOnly: boolean;
  leverage: number;
  market: Market;
  variants: boolean;
  /** Вид списка (на расчёт не влияет): скрыть «мало сделок»/«убыточные», фильтр по вердикту, группировка. */
  hideWeak: boolean;
  vf: VFilter;
  group: boolean;
}

/** Пауза для слайдера плеча: loadCached не чаще, чем раз в 150 мс. */
const SLIDER_DEBOUNCE_MS = 150;
const BUSY_ATTEMPTS = 4;

export function createLeaderboard(meta: Meta): Tab {
  const P = persisted<LState>("best", { symbol: "BTCUSDT", tf: "1h", days: 730, rank: "calmar", longOnly: false, leverage: 1, market: "perp", variants: false, hideWeak: false, vf: "", group: false }, {
    symbol: (v) => meta.symbols.includes(v),
    tf: (v) => meta.leaderboard_tfs.includes(v),
    days: (v) => LB_DAYS.includes(v),
    rank: isRank,
    leverage: (v) => v >= 1 && v <= MAX_LEVERAGE,
    market: isMarket,
    vf: isVFilter,
  });
  const S = P.state;
  if (!meta.symbols.includes(S.symbol)) S.symbol = meta.symbols[0]!;
  if (!meta.leaderboard_tfs.includes(S.tf)) S.tf = meta.leaderboard_tfs[0]!;
  S.days = fitDays(meta, S.tf, S.days, LB_DAYS);

  let ctl: AbortController | null = null;
  let rseq = 0;      // номер текущего расчёта (cancel/новый запуск делают предыдущий неактуальным)
  let cseq = 0;      // номер текущей загрузки кэша
  let dirtyTimer = 0;

  // ---------- управление
  const hasKlines = (sym: string, tf: string) => meta.klines_available[sym]?.includes(tf) ?? false;
  const tfOptions = () => meta.leaderboard_tfs.map((t) => ({ value: t, label: hasKlines(S.symbol, t) ? t : `${t} — нет данных` }));
  /**
   * Любое изменение входов рейтинга (символ, ТФ, период, лонг, плечо, варианты): прерываем расчёт, СРАЗУ сбрасываем плана/карточки
   * (чтобы старые карточки не смешались с новыми), затем показываем то, что уже есть в дисковом кэше для новых настроек.
   * debounceMs > 0 откладывает чтение кэша (слайдер плеча). Возвращает промис завершения чтения кэша.
   */
  function markDirty(debounceMs = 0): Promise<void> {
    P.save();
    cancel();
    clearTimeout(dirtyTimer);
    cseq++;                       // ответ предыдущей загрузки кэша уже не нужен
    banner.replaceChildren();
    store.resetFor("loading");
    if (debounceMs > 0) {
      return new Promise((resolve) => {
        dirtyTimer = window.setTimeout(() => { void loadCached().then(resolve); }, debounceMs);
      });
    }
    return loadCached();
  }

  const symSel = select({ label: "Токен", value: S.symbol, options: meta.symbols.map((s) => ({ value: s, label: s })), onChange: (v) => { S.symbol = v; tfSel.setOptions(tfOptions(), S.tf); void markDirty(); notifyContext(); } });
  const tfSel = select({ label: "Свеча (таймфрейм)", value: S.tf, options: tfOptions(), onChange: (v) => { S.tf = v; syncDays(); void markDirty(); notifyContext(); } });
  const dayOptions = () => LB_DAYS.map((d) => {
    const why = capReason(meta, S.tf, d);
    // выбранный период не отключаем (если истории не хватает ни на один вариант — select не должен остаться пустым)
    return { value: String(d), label: why ? `${d} дн. — ${why}` : `${d} дн.`, disabled: why !== null && d !== S.days };
  });
  const daysSel = select({
    label: "Период", value: String(S.days), options: dayOptions(),
    onChange: (v) => { S.days = +v; void markDirty(); },
  });
  const syncDays = () => {
    S.days = fitDays(meta, S.tf, S.days, LB_DAYS);
    daysSel.setOptions(dayOptions(), String(S.days));
  };
  const rankSel = select({
    label: "Ранжировать по", value: S.rank, options: RANKS.map((r) => ({ value: r.value, label: r.label })),
    onChange: (v) => { if (isRank(v)) { S.rank = v; P.save(); paint(); } },
  });
  const longT = toggle({ label: "Только лонг", checked: S.longOnly, onChange: (v) => { S.longOnly = v; void markDirty(); } });
  const varT = toggle({
    label: "Перебирать фильтры (HTF/стоп)", checked: S.variants, hint: "Для каждой стратегии выбирается лучший из 4 вариантов надстроек — по IS. Считается в ~4 раза дольше.",
    onChange: (v) => { S.variants = v; void markDirty(); },
  });
  const levS = leverageControl({ value: S.leverage, botNote: true, onInput: (v) => { S.leverage = v; void markDirty(SLIDER_DEBOUNCE_MS); } });
  const marketSeg = segmented({
    label: "Рынок", options: MARKET_OPTIONS, value: S.market,
    onChange: (v) => { if (isMarket(v)) { S.market = v; applyMarketUi(); void markDirty(); } },
  });
  const marketNoteEl = h("p", { class: "muted small market-note" });
  /** Спот: плечо 1× и «только лонг» заблокированы; комиссия рейтинга — 0,10 % (спот) вместо перпового умолчания. */
  function applyMarketUi() {
    const ui = marketUi(S.market);
    levS.setDisabled(ui.levDisabled);
    levS.set(effLeverage(S.market, S.leverage));
    longT.set(effLongOnly(S.market, S.longOnly));
    longT.disable(ui.longLocked, SPOT_LONG_NOTE);
    marketNoteEl.textContent = S.market === "spot"
      ? "Спот: плечо 1×, только лонг, фандинг не начисляется, комиссия 0,10 %. Свечи перпа как приближение спота; сигналы деривативных стратегий — по данным перпа."
      : "";
  }
  const feePct = () => (S.market === "spot" ? FEE_PCT.spot : meta.defaults.fee * 100);
  const runBtn = h("button", { class: "btn primary", type: "button", text: "Запустить", onClick: () => void run() });
  const forceBtn = h("button", { class: "btn", type: "button", hidden: true, title: "Игнорировать кэш и посчитать все стратегии заново", onClick: () => void run(true) }, icon("refresh", 14), "Пересчитать заново");
  const stopBtn = h("button", { class: "btn", type: "button", text: "Остановить", hidden: true, onClick: () => { cancel(); void loadCached(); } });
  const statusDot = h("i", { class: "lb-dot", "aria-hidden": "true" });
  const statusText_ = h("span", { class: "lb-status-text" });
  const statusEl = h("div", { class: "lb-status", "aria-live": "polite" }, statusDot, statusText_);
  const bar = h("div", { class: "progress lb-bar", hidden: true, role: "progressbar", "aria-label": "Ход расчёта рейтинга", "aria-valuemin": 0 }, h("div", { class: "progress-fill" }));
  const banner = h("div", { class: "banner-slot" });
  const shortBanner = h("div", { class: "banner-slot lb-notice-slot" });
  const list = h("div", { class: "lb-list" });
  const labeled = (label: string, el: HTMLElement) => h("div", { class: "field" }, h("div", { class: "field-head" }, h("span", { class: "field-label", text: label })), el);

  const controls = h("section", { class: "card lb-toolbar", "aria-label": "Настройки рейтинга" },
    h("div", { class: "lb-fields" }, labeled("Рынок", marketSeg.el), symSel.el, tfSel.el, daysSel.el, rankSel.el),
    h("div", { class: "lb-fields2" }, levS.el, h("div", { class: "lb-toggles" }, longT.el, varT.el)),
    marketNoteEl,
    h("div", { class: "lb-actions" }, runBtn, forceBtn, stopBtn, statusEl),
    bar);

  const disclaimer = howToRead();

  const summary = createSummary({
    onFilter: (v) => { S.vf = v; P.save(); paint(); },
    onWeak: (on) => { S.hideWeak = on; P.save(); paint(); },
    onGroup: (on) => { S.group = on; P.save(); paint(); },
  });

  // ---------- автомат состояний: любое set() перерисовывает статус и список
  const store = createLbStore(() => { status(); renderList(); }, (e) => {
    console.error("lb render failed", e);
    banner.replaceChildren(errorBanner(asApiError(e), () => paint()));
  });
  const m = store.model;
  const paint = () => { status(); renderList(); };

  function status() {
    statusText_.textContent = statusText(m);
    statusEl.dataset.phase = m.phase === "cached" ? (m.readyCount > 0 && m.missingCount === 0 ? "fresh" : m.readyCount > 0 ? "partial" : "empty") : m.phase;
    const running = m.phase === "running";
    runBtn.disabled = runDisabled(m);
    runBtn.textContent = runLabel(m);
    forceBtn.hidden = running || m.readyCount === 0;
    stopBtn.hidden = !running;
    bar.hidden = !running;
    (bar.firstElementChild as HTMLElement).style.width = m.total ? `${(m.done / m.total) * 100}%` : "0%";
    bar.setAttribute("aria-valuenow", String(m.done));
    bar.setAttribute("aria-valuemax", String(m.total));
    bar.setAttribute("aria-valuetext", `${m.done} из ${m.total}`);
  }

  /** Один текст «для бота» на «Копировать» и «В бота». */
  const buildText = (it: LbItem): string => {
    const r = it.result!;
    return labPayload({
      strategy: it.strategy, tf: S.tf, params: r.params, symbol: S.symbol, longOnly: effLongOnly(S.market, S.longOnly),
      leverage: effLeverage(S.market, S.leverage), market: S.market,
      feePct: feePct(), slipPct: meta.defaults.slippage * 100, days: S.days, overlays: it.overlays ?? { htf: null, stop: null },
      result: `OOS ${metricsOf(r.oos)}; IS доход ${spct(r.is.total_return, 0)}`,
    });
  };

  const cardCtx = (): CardCtx => ({
    symbol: S.symbol, tf: S.tf, days: S.days, longOnly: effLongOnly(S.market, S.longOnly), leverage: effLeverage(S.market, S.leverage),
    market: S.market, feePct: feePct(), slipPct: meta.defaults.slippage * 100,
    onOpenLab: (it) => {
      const r = it.result!;
      const overlays = it.overlays ?? { htf: null, stop: null };
      setPendingLab({
        symbol: S.symbol, tf: S.tf, strategy: it.strategy, params: r.params, days: S.days,
        longOnly: S.longOnly, leverage: S.leverage, htf: overlays.htf, stop: overlays.stop, market: S.market,
      });
      setRoute("lab");
    },
    onCopy: (it) => void copyWithToast(buildText(it)),
    onSend: (it) => void sendToBotWithToast(buildText(it)),
  });

  let shortSig = "";
  function renderShortBanner() {
    const first = m.order.map((n) => m.entries.get(n)).find((e): e is Extract<Entry, { k: "item" }> => !!e && e.k === "item" && e.item.short_oos !== undefined);
    const days = first?.item.oos_days;
    const show = !!first && !!first.item.short_oos && days != null;
    const can730 = fitDays(meta, S.tf, 730, LB_DAYS) === 730 && S.days !== 730;
    const sig = show ? `${Math.round(days!)}|${can730}` : "";
    if (sig === shortSig) return;     // не пересоздаём баннер (и его кнопку) на каждую карточку
    shortSig = sig;
    if (!show) { shortBanner.replaceChildren(); return; }
    shortBanner.replaceChildren(h("div", { class: "notice warn", role: "status" },
      h("span", {}, h("strong", { text: "Короткий OOS: " }), `проверочный период всего ${Math.round(days!)} дн. — годовые метрики (Sharpe, Calmar, CAGR) завышены, не сравнивайте их.`),
      can730 ? h("button", {
        class: "btn sm", type: "button", text: "Поставить 730 дн.",
        // тот же путь, что и любое изменение настроек: сброс -> чтение кэша для 730 -> расчёт недостающего
        onClick: () => { S.days = 730; daysSel.setOptions(dayOptions(), "730"); void markDirty().then(() => run()); },
      }) : null));
  }

  function renderList() {
    renderShortBanner();
    const { ranked, rest } = splitRanked(m.order, (n) => { const e = m.entries.get(n); return e && e.k === "item" ? e.item : null; }, S.rank);
    const ctx = cardCtx();
    const visible = applyVerdictFilter(ranked, S.vf, S.hideWeak);
    summary.el.hidden = ranked.length === 0;
    summary.update(countVerdicts(ranked), { filter: S.vf, hideWeak: S.hideWeak, group: S.group, shown: visible.length, total: ranked.length });
    // место в рейтинге — по полному порядку, а не по отфильтрованному списку
    const place = new Map(ranked.map((x, k) => [x.name, k + 1]));
    const cardOf = (x: (typeof ranked)[number]): KeyedItem => {
      const k = place.get(x.name)!;
      return {
        key: "i:" + x.name, sig: itemSig(k, x.it, ctx),
        // сбой отрисовки одной карточки (битые данные) не должен ронять весь список
        build: () => {
          try { return itemCard(k, x.it, ctx); } catch (e) {
            console.error("lb card failed", x.name, e);
            return compactCard(x.name, m.plan?.strategies.find((s) => s.name === x.name), { k: "error", error: "не удалось отобразить результат" });
          }
        },
      };
    };
    const items: KeyedItem[] = [];
    if (S.group && !S.vf) {
      for (const g of groupByVerdict(visible)) {
        items.push({ key: "g:" + g.verdict, sig: g.verdict + g.items.length, build: () => groupHead(g.verdict, g.items.length) });
        g.items.forEach((x) => items.push(cardOf(x)));
      }
    } else visible.forEach((x) => items.push(cardOf(x)));
    if (!S.vf) {
      if (!ranked.length && (m.phase === "cached" || m.phase === "idle") && m.plan?.klines && m.readyCount > 0) {
        items.push({ key: "empty-cache", sig: String(m.missingCount), build: () => emptyCache(m.missingCount > 0 ? h("button", { class: "btn primary", type: "button", text: "Запустить расчёт", onClick: () => void run() }) : null) });
      }
      for (const name of rest) {
        const e = m.entries.get(name);
        const p = m.plan?.strategies.find((s) => s.name === name);
        items.push({
          key: "r:" + name, sig: JSON.stringify([e, p?.desc, p?.derivative]), build: () => compactCard(name, p, e),
        });
      }
    }
    if (!items.length) {
      if (m.phase === "loading" || (m.phase === "running" && !m.order.length)) skeletonCards(3).forEach((el, i) => items.push({ key: "skel" + i, sig: "s", build: () => el }));
      else {
        const text = m.phase === "error" ? "Не удалось загрузить рейтинг." : S.vf ? "Нет стратегий с таким вердиктом." : "Нет стратегий для этих настроек.";
        items.push({ key: "empty:" + text, sig: text, build: () => h("p", { class: "muted", text }) });
      }
    }
    patchKeyed(list, items);
  }

  // ---------- кэш и вычисление
  /** Прервать текущий расчёт и перерисовать (не зависает в «Считаю N из M…»). */
  function cancel() {
    rseq++;
    ctl?.abort();
    ctl = null;
    if (m.phase === "running") store.set({ phase: "cached", retry: null });
  }

  const cachedBody = () => ({
    symbol: S.symbol, tf: S.tf, days: S.days, fee: feePct() / 100, slippage: meta.defaults.slippage,
    leverage: effLeverage(S.market, S.leverage), long_only: effLongOnly(S.market, S.longOnly), market: S.market, variants: S.variants,
  });

  const body = (strategy: string, force: boolean): LbBody => ({ ...cachedBody(), strategy, force });

  const dataMissingBanner = () => {
    const err = new ApiError(409, "data_missing", `Нет локальных свечей ${S.symbol} ${S.tf}.`, { symbol: S.symbol, tf: S.tf, estimate: meta.fetch_estimate[S.tf] ?? null });
    banner.replaceChildren(errorBanner(err, () => { void markDirty().then(() => run()); }));
  };

  /** Мгновенно показывает готовые карточки из дискового кэша для текущих настроек (без вычислений). */
  async function loadCached(): Promise<void> {
    if (m.phase === "running") return;          // фоновый расчёт (A16) сам наполняет список
    const my = ++cseq;
    banner.replaceChildren();
    store.set({ phase: "loading", error: "" });
    let res;
    try {
      res = await api.lbCached(cachedBody());
    } catch (e) {
      if (my !== cseq) return;
      const err = asApiError(e);
      store.set({ phase: "error", error: err.message });
      showError(banner, err, () => void loadCached());
      return;
    }
    if (my !== cseq || (m.phase as Phase) === "running") return;
    const entries = new Map<string, Entry>();
    const have = new Map(res.items.map((i) => [i.strategy, i]));
    res.plan.strategies.forEach((s) => {
      const it = have.get(s.name);
      entries.set(s.name, !s.ready ? { k: "skip", skip: s.reason } : it ? { k: "item", item: it } : { k: "none" });
    });
    store.set({
      phase: "cached", plan: res.plan, entries, order: res.plan.strategies.map((s) => s.name),
      readyCount: res.plan.strategies.filter((s) => s.ready).length, missingCount: res.missing.length,
      dataLast: res.data_last, done: 0, total: 0, retry: null,
    });
    if (!res.plan.klines) dataMissingBanner();
  }

  /** Считает недостающие стратегии (или все, если force). Готовые из кэша не трогает. */
  async function run(force = false): Promise<void> {
    if (!m.plan) await loadCached();
    const plan = m.plan;
    if (!plan || !plan.klines) return;
    cancel();
    const my = rseq;
    const c = (ctl = new AbortController());
    banner.replaceChildren();
    const todo = plan.strategies.filter((s) => s.ready && (force || m.entries.get(s.name)?.k !== "item"));
    todo.forEach((s) => { if (m.entries.get(s.name)?.k !== "item") m.entries.set(s.name, { k: "pending" }); });
    if (!store.set({ phase: "running", note: "", total: todo.length, done: 0, retry: null, error: "" })) return;
    try {
      for (const s of todo) {
        if (my !== rseq) return;
        paint();
        let outcome: Entry;
        try {
          const r = await withBusyRetry(() => api.lbItem(body(s.name, force), c.signal), {
            attempts: BUSY_ATTEMPTS, signal: c.signal,
            onWait: (next, n, _err, waitedS) => { if (my === rseq) store.set({ retry: { k: next, n, name: s.name, waitedS } }); },
          });
          outcome = r.ok ? { k: "item", item: r.value } : { k: "busy" };
        } catch (e) {
          if (my !== rseq) return;
          outcome = { k: "error", error: asApiError(e).message };
        }
        if (my !== rseq) return;
        m.entries.set(s.name, outcome);
        const dl = outcome.k === "item" ? outcome.item.data_last ?? m.dataLast : m.dataLast;
        store.set({ done: m.done + 1, missingCount: countMissing(m.entries), dataLast: dl, retry: null });
      }
    } catch (e) {
      // неожиданный сбой (не ошибка отдельной стратегии): показываем в баннере, состояние восстановит finally
      if (my === rseq) {
        const err = asApiError(e);
        banner.replaceChildren(errorBanner(err, () => void run(force)));
        store.set({ phase: "error", error: err.message });
      }
    } finally {
      if (my === rseq) {
        ctl = null;
        store.set((m.phase as Phase) === "running" ? { phase: "cached", retry: null } : { retry: null });
      }
    }
  }

  const el = h("div", { class: "page best" },
    pageHead("Лучшие стратегии", "Рейтинг по out-of-sample: параметры подбираются на первых 67% периода, оцениваются на последних 33%."),
    disclaimer, controls, banner, shortBanner, summary.el, list);
  applyMarketUi();
  paint();

  // данные загружены/обновлены -> перестроить опции (подпись «нет данных» исчезает), выбор не теряется
  onMetaChange(() => {
    tfSel.setOptions(tfOptions(), S.tf);
    const before = S.days;
    syncDays();
    if (S.days !== before) void markDirty();
  });

  return {
    el,
    show() {
      const go = takeFlag("best", "run");           // deep link «#/best?run»: сразу досчитать недостающее (одноразово)
      tfSel.setOptions(tfOptions(), S.tf);
      const before = S.days;
      syncDays();
      let ready: Promise<void> | null = null;
      if (S.days !== before) ready = markDirty();
      else if (m.phase !== "running") ready = loadCached();
      else paint();                                  // расчёт шёл в фоне — просто показываем накопленное
      if (go && ready) void ready.then(() => run());
    },
    dataContext: () => ({ symbol: S.symbol, tfs: [S.tf] }),
    /** Данные обновились: старые записи кэша больше не подходят — перечитываем кэш, а не пересчитываем всё вслепую. */
    refresh() {
      cancel();
      void loadCached().then(() => { if (m.phase === "cached") store.set({ note: m.missingCount > 0 ? "Данные обновились — расчёт нужен заново." : "" }); });
    },
    /**
     * Скрытие вкладки НЕ прерывает расчёт: он идёт в фоне, результаты копятся в модели и карточки отрисуются при показе.
     * Прерывается он только изменением входов рейтинга (markDirty) или обновлением данных (refresh) — оба возможны лишь на видимой вкладке
     * либо через onMetaChange, который сам вызывает markDirty.
     */
    hide() { /* намеренно пусто */ },
  };
}
