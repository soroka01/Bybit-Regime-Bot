// Вкладка «Лаборатория»: одна стратегия, слайдеры параметров, пересчёт «на лету».
import { api } from "../api";
import { SWEEP_METRICS } from "../charts/heatmap";
import { t } from "../i18n";
import { strategyDesc } from "../i18n/serverText";
import { MAX_LEVERAGE } from "../leverage";
import { effLeverage, effLongOnly, isMarket, MARKET_OPTIONS, marketNote, marketUi, SPOT_LONG_NOTE, switchMarket } from "../market";
import { onMetaChange } from "../meta";
import { persisted, takeFlag, takePendingLab } from "../state";
import type { BacktestBody, BacktestResult, Market, Meta, StrategyMeta } from "../types";
import { segmented, select, slider, toggle, type Control } from "../ui/controls";
import { copyButton, labPayload, metricsLine, metricsOf, sendButton } from "../ui/clipboard";
import { combobox } from "../ui/combobox";
import { h } from "../ui/dom";
import { icon } from "../ui/icons";
import { badge, pageHead } from "../ui/kit";
import { leverageControl } from "../ui/levControl";
import { panelSection } from "../ui/panelSection";
import { changedParams } from "../ui/paramLogic";
import { createRunner } from "../ui/runner";
import { filterStrategies } from "../ui/strategySearch";
import { costsSummary, leverageSummary, marketSummary, overlaysSummary, strategySummary } from "./labLogic";
import { openOptimize } from "./optimize";
import { createResultView } from "./resultview";
import { createSweepPanel } from "./sweepPanel";
import { capReason, DAY_OPTIONS, DAY_VALUES, fitDays, notifyContext, panelLayout, type Tab } from "./tab";

interface LabState {
  symbol: string;
  tf: string;
  strategy: string;
  params: Record<string, Record<string, number>>;
  days: number;
  fee: number;       // в процентах
  feeTouched: boolean;   // пользователь правил комиссию: смена рынка её не трогает
  market: Market;
  slip: number;      // в процентах
  leverage: number;
  longOnly: boolean;
  htfOn: boolean;
  htf: number;
  stopOn: boolean;
  stop: number;
  sx: string;
  sy: string;
  sMetric: string;
  sN: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function createLab(meta: Meta): Tab {
  const P = persisted<LabState>("lab", {
    symbol: "BTCUSDT", tf: "1h", strategy: "EMA cross", params: {}, days: meta.defaults.days,
    fee: meta.defaults.fee * 100, feeTouched: false, market: "perp", slip: meta.defaults.slippage * 100, leverage: 1, longOnly: false,
    htfOn: false, htf: meta.overlay_defaults.htf, stopOn: false, stop: meta.overlay_defaults.stop,
    sx: "", sy: "", sMetric: "total_return", sN: 10,
  }, {
    symbol: (v) => meta.symbols.includes(v),
    tf: (v) => meta.timeframes.includes(v),
    strategy: (v) => meta.strategies.some((s) => s.name === v),
    days: (v) => DAY_VALUES.includes(v),
    sMetric: (v) => SWEEP_METRICS.some((m) => m.key === v),
    sN: (v) => v >= 4 && v <= 20,
    fee: (v) => v >= 0 && v <= 0.3, slip: (v) => v >= 0 && v <= 0.3,
    leverage: (v) => v >= 1 && v <= MAX_LEVERAGE, market: isMarket,
    htf: (v) => v >= 10 && v <= 200, stop: (v) => v >= 0.5 && v <= 10,
    params: (v) => Object.values(v).every((m) => m && typeof m === "object" && !Array.isArray(m) && Object.values(m).every((x) => typeof x === "number" && Number.isFinite(x))),
  });
  const S = P.state;
  const byName = new Map(meta.strategies.map((s) => [s.name, s]));
  if (!byName.has(S.strategy)) S.strategy = meta.strategies[0]!.name;
  if (!meta.symbols.includes(S.symbol)) S.symbol = meta.symbols[0]!;
  if (!meta.timeframes.includes(S.tf)) S.tf = "1h";
  S.days = fitDays(meta, S.tf, S.days);
  const strat = (): StrategyMeta => byName.get(S.strategy)!;
  const currentParams = (): Record<string, number> => body().params;

  const result = createResultView({ fileHint: () => [S.symbol, S.strategy, S.tf] });

  let last: BacktestResult | null = null;

  const body = (): BacktestBody => {
    const st = strat();
    const saved = S.params[st.name] ?? {};
    const params: Record<string, number> = {};
    for (const [k, sp] of Object.entries(st.params)) params[k] = clamp(saved[k] ?? sp.default, sp.min, sp.max);
    return {
      symbol: S.symbol, tf: S.tf, strategy: st.name, params, days: S.days,
      fee: S.fee / 100, slippage: S.slip / 100, leverage: effLeverage(S.market, S.leverage),
      long_only: effLongOnly(S.market, S.longOnly), market: S.market,
      overlays: { htf: S.htfOn ? Math.round(S.htf) : null, stop: S.stopOn ? S.stop : null },
    };
  };

  const runner = createRunner<BacktestBody, BacktestResult>({
    delay: 250,
    run: (b, sig) => api.backtest(b, sig),
    onStart: () => result.setBusy(true),
    onRetry: (k, max) => result.setBusy(true, t("lab.busyRetry", { k, max })),
    onData: (r, b) => {
      last = r;
      result.setBusy(false);
      result.setData(r);
      syncParams(r, b);
    },
    onError: (e) => { result.setBusy(false); result.setError(e, () => runner.now(body())); },
  });
  const trigger = () => { P.save(); sweep.inputsChanged(); updateSummaries(); runner.schedule(body()); };

  /** Сервер зажимает параметры в допустимые границы — показываем то, что реально посчиталось. */
  function syncParams(r: BacktestResult, b: BacktestBody) {
    if (!r.params) return;
    const m = (S.params[b.strategy] ??= {});
    for (const [k, v] of Object.entries(r.params)) {
      if (typeof v !== "number" || v === b.params[k] || b.strategy !== S.strategy) continue;
      m[k] = v;
      sliders.get(k)?.set(v);
    }
  }

  // ---------- панель: рынок
  const availNote = h("p", { class: "notice warn lab-avail", hidden: true });
  const tfHint = h("p", { class: "muted small lab-tfhint" });
  const updateAvail = () => {
    const ok = meta.available[S.symbol]?.includes(S.tf);
    availNote.textContent = ok ? "" : t("lab.availNote", { symbol: S.symbol, tf: S.tf });
    availNote.hidden = !!ok;
    tfHint.textContent = (meta.timeframe_minutes[S.tf] ?? 60) < 15
      ? t("lab.tfHint") : "";
  };
  const symbolSel = select({
    label: t("lab.symbol"), value: S.symbol,
    options: meta.symbols.map((s) => ({ value: s, label: s })),
    onChange: (v) => { S.symbol = v; updateAvail(); sweep.clear(); trigger(); notifyContext(); },
  });
  const tfSel = select({
    label: t("lab.timeframe"), value: S.tf,
    options: meta.timeframes.map((tf) => ({ value: tf, label: tf })),
    onChange: (v) => { S.tf = v; applyCap(); updateAvail(); sweep.clear(); trigger(); notifyContext(); },
  });

  // ---------- панель: стратегия (поиск + карточка описания + параметры)
  const comboItems = meta.strategies.map((s) => ({ value: s.name, label: s.name, desc: strategyDesc(s.name, s.desc), tag: s.derivative ? t("lab.derivTag") : undefined }));
  const comboList = comboItems.map((it) => ({ name: it.label, desc: it.desc ?? "", derivative: !!it.tag, it }));
  const stratSel = combobox({
    label: t("lab.strategy"), value: S.strategy, items: comboItems,
    filter: (_items, q) => filterStrategies(comboList, q).map((x) => x.it),
    onChange: (v) => { S.strategy = v; renderParams(); sweep.clear(); sweep.fillAxes(); updateMarketNotes(); trigger(); },
  });
  const descName = h("strong", { class: "sd-name" });
  const descText = h("p", { class: "sd-text" });
  const descTag = h("span", { class: "sd-tag" });
  const desc = h("div", { class: "strat-desc" }, h("div", { class: "sd-top" }, descName, descTag), descText);
  const paramBox = h("div", { class: "params" });
  const paramsAllDef = h("button", {
    class: "btn ghost sm", type: "button", hidden: true, "data-tip": t("lab.paramsResetAllTip"), "data-tip-pos": "left",
    onClick: () => { delete S.params[strat().name]; renderParams(); trigger(); },
  }, icon("reset", 13), t("lab.paramsResetAll"));
  const paramsHead = h("div", { class: "params-head" }, h("span", { class: "section-label", text: t("lab.params") }), paramsAllDef);
  const sliders = new Map<string, Control<number>>();

  function renderParams() {
    const st = strat();
    descName.textContent = st.name;
    descText.textContent = strategyDesc(st.name, st.desc);
    descTag.replaceChildren(...(st.derivative ? [badge(t("lab.derivTag"), "info", { title: t("lab.derivTagTip") })] : []));
    sliders.clear();
    paramBox.replaceChildren(...Object.entries(st.params).map(([name, sp]) => {
      const cur = clamp(S.params[st.name]?.[name] ?? sp.default, sp.min, sp.max);   // устаревшее сохранённое значение не расширяет диапазон
      const c = slider({
        label: name, min: sp.min, max: sp.max, step: sp.step, value: cur, def: sp.default,
        hint: t("lab.paramGrid", { grid: sp.grid.join(", ") }),
        onInput: (v) => { (S.params[st.name] ??= {})[name] = v; trigger(); },
      });
      sliders.set(name, c);
      return c.el;
    }));
    updateSummaries();
  }

  const daysSeg = segmented({
    label: t("lab.period"), options: DAY_OPTIONS, value: String(S.days),
    onChange: (v) => { S.days = +v; trigger(); },
  });
  /** Период не длиннее истории таймфрейма: недоступные кнопки серые с подсказкой, текущее значение подгоняется. */
  function applyCap() {
    S.days = fitDays(meta, S.tf, S.days);
    daysSeg.set(String(S.days));
    daysSeg.disable((v) => capReason(meta, S.tf, +v));
  }
  applyCap();

  // ---------- панель: издержки, плечо, оверлеи
  const feeS = slider({ label: t("lab.fee"), min: 0, max: 0.3, step: 0.005, value: S.fee, unit: "%", onInput: (v) => { S.fee = v; S.feeTouched = true; trigger(); } });
  const slipS = slider({ label: t("lab.slip"), min: 0, max: 0.3, step: 0.005, value: S.slip, unit: "%", onInput: (v) => { S.slip = v; trigger(); } });
  const levS = leverageControl({ value: S.leverage, botNote: true, onInput: (v) => { S.leverage = v; trigger(); } });
  const longT = toggle({ label: t("lab.longOnly"), checked: S.longOnly, onChange: (v) => { S.longOnly = v; trigger(); } });
  const marketSeg = segmented({
    label: t("lab.market"), options: MARKET_OPTIONS, value: S.market,
    onChange: (v) => {
      if (!isMarket(v)) return;
      if (switchMarket(S, v)) feeS.set(S.fee);     // комиссия переходит на умолчание рынка, если её не правили
      applyMarketUi();
      trigger();
    },
  });
  const marketNoteEl = h("p", { class: "muted small market-note" });
  /** Спот: плечо 1× и «только лонг» заблокированы (значения пользователя сохраняются и возвращаются при переходе на перп). */
  function applyMarketUi() {
    const ui = marketUi(S.market);
    levS.setDisabled(ui.levDisabled);
    levS.set(effLeverage(S.market, S.leverage));
    longT.set(effLongOnly(S.market, S.longOnly));
    longT.disable(ui.longLocked, SPOT_LONG_NOTE);
    marketSeg.set(S.market);
    updateMarketNotes();
    updateSummaries();
  }
  function updateMarketNotes() { marketNoteEl.textContent = marketNote(S.market, strat().derivative); }
  const htfT = toggle({ label: t("lab.htf"), checked: S.htfOn, hint: t("lab.htf.hint"), onChange: (v) => { S.htfOn = v; trigger(); } });
  const htfS = slider({ label: t("lab.htf.n"), min: 10, max: 200, step: 1, value: S.htf, onInput: (v) => { S.htf = v; if (S.htfOn) trigger(); else { P.save(); updateSummaries(); } } });
  const stopT = toggle({ label: t("lab.stop"), checked: S.stopOn, hint: t("lab.stop.hint"), onChange: (v) => { S.stopOn = v; trigger(); } });
  const stopS = slider({ label: "k × ATR", min: 0.5, max: 10, step: 0.1, value: S.stop, onInput: (v) => { S.stop = v; if (S.stopOn) trigger(); else { P.save(); updateSummaries(); } } });

  // ---------- нижняя «липкая» панель действий
  const copyPayload = (params: Record<string, number>, res: string): string => {
    const b = body();
    return labPayload({
      strategy: b.strategy, tf: b.tf, params, symbol: b.symbol, longOnly: b.long_only, leverage: b.leverage, market: b.market,
      feePct: S.fee, slipPct: S.slip, days: b.days, overlays: b.overlays, result: res,
    });
  };
  const copyBtn = copyButton(t("lab.copyBot"), () => copyPayload(body().params, metricsLine(last)));
  copyBtn.title = t("lab.copyBotTip");
  const sendBtn = sendButton(t("lab.sendBot"), () => copyPayload(body().params, metricsLine(last)));
  const optBtn = h("button", {
    class: "btn primary lg", type: "button",
    title: t("lab.optimizeTip"),
    onClick: () => {
      const st = strat();
      openOptimize({
        title: st.name,
        body: () => {
          const b: Partial<BacktestBody> = body();
          delete b.params;
          return b as Omit<BacktestBody, "params">;
        },
        onApply: (params) => {
          const m = (S.params[st.name] ??= {});
          for (const [k, v] of Object.entries(params)) {
            const sp = st.params[k];
            m[k] = sp ? Math.min(Math.max(v, sp.min), sp.max) : v;
          }
          renderParams();
          trigger();
        },
        copyText: (row) => copyPayload(row.params, `OOS: ${metricsOf(row.oos)}`),
      });
    },
  }, icon("target", 15), t("lab.optimize"));
  const resetBtn = h("button", {
    class: "btn ghost", type: "button", text: t("lab.reset"), title: t("lab.resetTip"),
    onClick: () => { P.reset(); location.reload(); },
  });
  const bar = h("div", { class: "panel-bar" }, optBtn, h("div", { class: "pb-row" }, copyBtn, sendBtn, resetBtn));

  // ---------- развёртка параметров
  const sweep = createSweepPanel({
    state: S, save: () => P.save(), strat, body, currentParams,
    apply(vals) {
      const st = strat();
      const m = (S.params[st.name] ??= {});
      for (const [k, v] of Object.entries(vals)) { m[k] = v; sliders.get(k)?.set(v); }
      trigger();
    },
  });

  // ---------- секции панели
  const secMarket = panelSection({ key: "lab.market", icon: "candles", title: t("lab.market"), summary: "", open: true, body: [marketSeg.el, marketNoteEl, symbolSel.el, tfSel.el, tfHint, availNote, daysSeg.el] });
  const secStrat = panelSection({ key: "lab.strategy", icon: "flask", title: t("lab.strategy"), summary: "", open: true, body: [stratSel.el, desc, paramsHead, paramBox] });
  const secCosts = panelSection({ key: "lab.costs", icon: "percent", title: t("lab.sec.costs"), summary: "", body: [feeS.el, slipS.el, longT.el] });
  const secLev = panelSection({ key: "lab.leverage", icon: "gauge", title: t("lab.sec.leverage"), summary: "", body: [levS.el] });
  const secOver = panelSection({ key: "lab.overlays", icon: "layers", title: t("lab.sec.overlays"), summary: "", body: [htfT.el, htfS.el, stopT.el, stopS.el] });
  /** Однострочные сводки свёрнутых секций. */
  function updateSummaries() {
    const st = strat();
    const ch = changedParams(body().params, st.params);
    secMarket.setSummary(marketSummary(S));
    secStrat.setSummary(strategySummary(st.name, ch.length, Object.keys(st.params).length));
    secCosts.setSummary(costsSummary({ fee: S.fee, slip: S.slip, longOnly: effLongOnly(S.market, S.longOnly) }));
    secLev.setSummary(leverageSummary(S.leverage, S.market === "spot"));
    secOver.setSummary(overlaysSummary(S));
    paramsAllDef.hidden = !ch.length;
  }

  const ly = panelLayout([secMarket.el, secStrat.el, secCosts.el, secLev.el, secOver.el], [result.el, sweep.el], {
    head: pageHead(t("lab.title"), t("lab.intro", { btn: t("lab.optimize") })),
    bar,
  });

  renderParams();
  updateAvail();
  applyMarketUi();

  /** Настройки, присланные из вкладки «Лучшие стратегии». */
  function applyPending(): boolean {
    const p = takePendingLab();
    if (!p || !byName.has(p.strategy)) return false;
    S.symbol = p.symbol; S.tf = p.tf; S.strategy = p.strategy;
    S.params[p.strategy] = { ...p.params };
    S.days = fitDays(meta, p.tf, p.days); S.longOnly = p.longOnly; S.leverage = p.leverage;
    if (switchMarket(S, p.market)) feeS.set(S.fee);
    S.htfOn = p.htf != null; if (p.htf != null) S.htf = p.htf;
    S.stopOn = p.stop != null; if (p.stop != null) S.stop = p.stop;
    symbolSel.set(S.symbol); tfSel.set(S.tf); stratSel.set(S.strategy); daysSeg.set(String(S.days)); applyCap();
    applyMarketUi(); htfT.set(S.htfOn); htfS.set(S.htf); stopT.set(S.stopOn); stopS.set(S.stop);
    renderParams(); updateAvail(); sweep.clear(); sweep.fillAxes(); P.save(); notifyContext();
    return true;
  }
  // данные загружены/обновлены: пересобираем зависящие от метаданных элементы, не трогая выбор пользователя
  onMetaChange(() => { updateAvail(); applyCap(); });
  let started = false;
  return {
    el: ly.el,
    show() {
      const applied = applyPending();
      if (!started || applied) { started = true; runner.now(body()); }
      if (takeFlag("lab", "opt")) optBtn.click(); // deep link «#/lab?opt» (одноразовый)
    },
    hide() { /* состояние сохраняется; фоновых таймеров нет */ },
    dataContext: () => ({ symbol: S.symbol, tfs: [S.tf] }),
    refresh: () => { if (started) runner.now(body()); },
  };
}
