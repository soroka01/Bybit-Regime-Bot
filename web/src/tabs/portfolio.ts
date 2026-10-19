// Вкладка «Портфель»: «ноги» бота (стратегия × таймфрейм × вес), бэктест на часовой сетке с фандингом.
import { api, asApiError } from "../api";
import { signalStrips } from "../charts/strips";
import { MAX_LEVERAGE } from "../leverage";
import { effLeverage, isMarket, MARKET_OPTIONS, marketLabel, marketNote, marketUi, SPOT_LONG_NOTE, switchMarket } from "../market";
import { persisted } from "../state";
import type { BacktestResult, LegCfg, Market, Meta, PortfolioBody } from "../types";
import { botNotes, copyButton, metricsLine, plainParams, pyLeg, sendButton } from "../ui/clipboard";
import { segmented, select, slider, toggle } from "../ui/controls";
import { h } from "../ui/dom";
import { num, pct } from "../ui/fmt";
import { icon } from "../ui/icons";
import { badge, pageHead, setLoading } from "../ui/kit";
import { leverageControl } from "../ui/levControl";
import { panelSection } from "../ui/panelSection";
import { changedParams } from "../ui/paramLogic";
import { createRunner } from "../ui/runner";
import { toast } from "../ui/toast";
import { importLiveLegs, LIVE_WEIGHT_CAP, MAX_LEGS, weightReport, type WeightReport } from "./portfolioLogic";
import { createResultView } from "./resultview";
import { DAY_OPTIONS, DAY_VALUES, notifyContext, panelLayout, type Tab } from "./tab";

interface PState {
  symbol: string;
  legs: LegCfg[];
  days: number;
  fee: number;
  feeTouched: boolean;   // комиссию правили вручную: смена рынка её не трогает
  slip: number;
  leverage: number;
  market: Market;
}

const LEG_COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];

export function createPortfolio(meta: Meta): Tab {
  const P = persisted<PState>("portfolio", {
    symbol: "BTCUSDT", legs: structuredClone(meta.legs), days: 730,
    fee: meta.defaults.fee * 100, feeTouched: false, slip: meta.defaults.slippage * 100, leverage: 1, market: "perp",
  }, {
    symbol: (v) => meta.symbols.includes(v),
    days: (v) => DAY_VALUES.includes(v),
    fee: (v) => v >= 0 && v <= 0.3, slip: (v) => v >= 0 && v <= 0.3,
    leverage: (v) => v >= 1 && v <= MAX_LEVERAGE, market: isMarket,
    legs: (v) => v.length <= MAX_LEGS && v.every((l) => l && typeof l.strategy === "string" && typeof l.tf === "string" && typeof l.weight === "number" && Number.isFinite(l.weight) && typeof l.long_only === "boolean" && l.params && typeof l.params === "object"),
  });
  const S = P.state;
  const strategies = new Map(meta.strategies.map((s) => [s.name, s]));
  S.legs = S.legs.filter((l) => strategies.has(l.strategy) && meta.portfolio_tfs.includes(l.tf));
  if (!S.legs.length) S.legs = structuredClone(meta.legs);

  const result = createResultView({ fileHint: () => ["portfolio", S.symbol, `${S.legs.length}legs`] });
  const stripsHost = h("div");
  const strips = signalStrips(stripsHost);
  const stripsLegend = h("div", { class: "strips-legend small muted" },
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--pos)", "aria-hidden": "true" }), "лонг"),
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--neg)", "aria-hidden": "true" }), "шорт"),
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--surface-2);box-shadow:inset 0 0 0 1px var(--border-strong)", "aria-hidden": "true" }), "вне позиции"),
    h("span", { text: "яркость — доля позиции; «Итого» — сумма вкладов ног" }));
  result.slot.append(h("section", { class: "card" },
    h("header", { class: "card-head" }, h("h3", { text: "Сигналы по ногам и итоговая экспозиция" })),
    h("p", { class: "muted small", text: "Сигнал старшего таймфрейма применяется с момента закрытия его свечи." }),
    stripsLegend, stripsHost));

  let last: BacktestResult | null = null;

  /** Спот: каждая нога только лонг (сервер клипует сигнал каждой ноги до суммирования), плечо 1×. */
  const effLegs = (): LegCfg[] => (S.market === "spot" ? S.legs.map((l) => ({ ...l, long_only: true })) : S.legs);
  const body = (): PortfolioBody => ({
    symbol: S.symbol, legs: effLegs(), days: S.days, fee: S.fee / 100, slippage: S.slip / 100,
    leverage: effLeverage(S.market, S.leverage), market: S.market,
  });
  const runner = createRunner<PortfolioBody, BacktestResult>({
    delay: 250,
    run: (b, sig) => api.portfolio(b, sig),
    onStart: () => result.setBusy(true),
    onRetry: (k, max) => result.setBusy(true, `Сервер занят, жду… попытка ${k}/${max}`),
    onData: (r) => {
      last = r;
      result.setBusy(false);
      result.setData(r);
      if (r.strips) {
        const rows = Object.entries(r.strips.legs).map(([label, values]) => ({ label, values, max: 1 }));
        const total = S.legs.reduce((a, l) => a + l.weight, 0) || 1;
        rows.push({ label: "Итого", values: r.strips.exposure, max: total });
        strips.set(r.strips.t, rows);
      }
    },
    onError: (e) => { result.setBusy(false); result.setError(e, () => runner.now(body())); },
  });
  const trigger = () => { P.save(); updatePanelSummaries(); runner.schedule(body()); };

  // ---------- общие параметры (панель)
  const daysSeg = segmented({ label: "Период", options: DAY_OPTIONS, value: String(S.days), onChange: (v) => { S.days = +v; trigger(); } });
  const levS = leverageControl({ value: S.leverage, botNote: true, onInput: (v) => { S.leverage = v; updateSum(); trigger(); } });
  const feeS = slider({ label: "Комиссия", min: 0, max: 0.3, step: 0.005, value: S.fee, unit: "%", onInput: (v) => { S.fee = v; S.feeTouched = true; trigger(); } });
  const marketSeg = segmented({
    label: "Рынок", options: MARKET_OPTIONS, value: S.market,
    onChange: (v) => {
      if (!isMarket(v)) return;
      if (switchMarket(S, v)) feeS.set(S.fee);     // комиссия переходит на умолчание рынка, если её не правили
      applyMarketUi();
      renderLegs();                                  // переключатели «только лонг» у ног блокируются в споте
      trigger();
    },
  });
  const marketNoteEl = h("p", { class: "muted small market-note" });
  function applyMarketUi() {
    levS.setDisabled(marketUi(S.market).levDisabled);
    levS.set(effLeverage(S.market, S.leverage));
    marketSeg.set(S.market);
    marketNoteEl.textContent = marketNote(S.market, S.legs.some((l) => strategies.get(l.strategy)?.derivative));
    updateSum();
    updatePanelSummaries();
  }
  const slipS = slider({ label: "Проскальзывание", min: 0, max: 0.3, step: 0.005, value: S.slip, unit: "%", onInput: (v) => { S.slip = v; trigger(); } });

  // ---------- сумма весов
  const wbar = h("div", { class: "wbar", role: "meter", "aria-label": "Суммарный вес ног", "aria-valuemin": 0 });
  const wHead = h("div", { class: "wsum-head" });
  const wLines = h("div", { class: "wsum-lines" });
  const wsum = h("div", { class: "wsum" }, wHead, wbar, wLines);
  const legChips: HTMLElement[] = [];
  const legCards: HTMLElement[] = [];

  const updateSum = () => {
    const lev = effLeverage(S.market, S.leverage);
    const rep: WeightReport = weightReport(S.legs, lev);
    const max = Math.max(1.5, Math.ceil(rep.sum * 4) / 4);
    wbar.className = `wbar ${rep.tone}${rep.over ? " over" : ""}`;
    wbar.style.setProperty("--mark", `${((LIVE_WEIGHT_CAP / max) * 100).toFixed(2)}%`);
    wbar.setAttribute("aria-valuemax", String(max));
    wbar.setAttribute("aria-valuenow", String(+rep.sum.toFixed(3)));
    wbar.setAttribute("aria-valuetext", `${rep.headline}${rep.over ? ", выше лимита бота 100 %" : ""}`);
    wbar.replaceChildren(
      ...S.legs.map((l, i) => h("i", { class: "wseg", style: `width:${((l.weight / max) * 100).toFixed(2)}%;--k:var(${LEG_COLORS[i % LEG_COLORS.length]})`, title: `Нога ${i + 1}: ${l.strategy}@${l.tf} · ${pct(l.weight, 0)}` })),
      h("i", { class: "wmark", "aria-hidden": "true", style: `left:${((LIVE_WEIGHT_CAP / max) * 100).toFixed(2)}%`, title: "лимит живого бота: 100 %" }));
    wHead.replaceChildren(
      h("span", { class: `wsum-val ${rep.tone}`, text: rep.headline }),
      h("span", { class: "muted small", text: `лимит живого бота 100 % · макс. экспозиция ≈ ${num(rep.sum * lev, 2)}× капитала` }),
      rep.tone === "ok" ? badge("в пределах лимита", "ok", { glyph: "✓" }) : rep.over ? badge("выше лимита бота", "warn", { glyph: "!" }) : badge(rep.sum <= 1e-9 ? "нет весов" : "есть нулевые ноги", rep.sum <= 1e-9 ? "bad" : "warn", { glyph: "!" }));
    wLines.replaceChildren(...rep.lines.map((t) => h("div", { class: `notice${rep.tone === "ok" ? "" : rep.tone === "bad" ? " bad" : " warn"}`, role: rep.over ? "alert" : "status" }, h("span", { text: t }))));
    legChips.forEach((c, i) => { const l = S.legs[i]; if (l) c.textContent = pct(l.weight, 0); });
    legCards.forEach((c, i) => c.classList.toggle("is-zero", (S.legs[i]?.weight ?? 1) <= 1e-9));
    updatePanelSummaries();
  };

  // ---------- ноги
  const paramsOpen = new WeakSet<LegCfg>();
  const legsBox = h("div", { class: "legs-grid" });
  const importNote = h("div", { class: "leg-import", "aria-live": "polite" });
  function renderLegs() {
    legChips.length = 0;
    legCards.length = 0;
    legsBox.replaceChildren(...S.legs.map((leg, idx) => {
      const st = strategies.get(leg.strategy)!;
      const color = LEG_COLORS[idx % LEG_COLORS.length]!;
      const paramEntries = Object.entries(st.params);
      const paramSliders = paramEntries.map(([name, sp]) =>
        slider({
          label: name, min: sp.min, max: sp.max, step: sp.step, value: leg.params[name] ?? sp.default, def: sp.default,
          hint: `сетка исследования: ${sp.grid.join(", ")}`,
          onInput: (v) => { leg.params[name] = v; changed.textContent = changedText(); trigger(); },
        }).el);
      const changedText = () => { const n = changedParams(leg.params, st.params).length; return n ? `изменено ${n}` : "по умолчанию"; };
      const changed = h("span", { class: "muted small", text: changedText() });
      const det = h("details", { class: "leg-params", open: paramsOpen.has(leg) },
        h("summary", null, h("span", { text: `Параметры (${paramEntries.length})` }), changed),
        h("div", { class: "params" }, ...paramSliders));
      det.addEventListener("toggle", () => { if (det.open) paramsOpen.add(leg); else paramsOpen.delete(leg); });
      const stratSel = select({
        label: "Стратегия", value: leg.strategy,
        options: meta.strategies.map((s) => ({ value: s.name, label: s.derivative ? `${s.name} · деривативы` : s.name })),
        onChange: (v) => {
          leg.strategy = v;
          leg.params = Object.fromEntries(Object.entries(strategies.get(v)!.params).map(([k, sp]) => [k, sp.default]));
          renderLegs(); applyMarketUi(); trigger();
        },
      });
      const tfSel = select({
        label: "Таймфрейм", value: leg.tf,
        options: meta.portfolio_tfs.map((t) => ({ value: t, label: t })),
        onChange: (v) => { leg.tf = v; updateSum(); trigger(); notifyContext(); },
      });
      const w = slider({ label: "Вес", min: 0, max: 1.5, step: 0.05, value: leg.weight, onInput: (v) => { leg.weight = v; updateSum(); trigger(); } });
      const spot = S.market === "spot";
      const lo = toggle({ label: "Только лонг", checked: spot || leg.long_only, onChange: (v) => { leg.long_only = v; trigger(); } });
      lo.disable(marketUi(S.market).longLocked, SPOT_LONG_NOTE);
      const rm = h("button", {
        class: "btn ghost icon-btn sm", type: "button", disabled: S.legs.length <= 1,
        "aria-label": `Удалить ногу ${idx + 1}`, "data-tip": S.legs.length <= 1 ? "Нужна хотя бы одна нога" : "Удалить ногу", "data-tip-pos": "left",
        onClick: () => { S.legs.splice(idx, 1); renderLegs(); applyMarketUi(); trigger(); notifyContext(); },
      }, icon("trash", 14));
      const chip = h("span", { class: "leg-w", title: "Вес ноги", text: pct(leg.weight, 0) });
      legChips.push(chip);
      const card = h("section", { class: "leg leg-card", style: `--leg:var(${color})`, "aria-label": `Нога ${idx + 1}` },
        h("header", { class: "leg-head" },
          h("span", { class: "leg-n", "aria-hidden": "true", text: String(idx + 1) }),
          h("div", { class: "leg-title" }, h("strong", { text: `Нога ${idx + 1}` }), h("span", { class: "muted small", text: `${leg.strategy} · ${leg.tf}` })),
          chip, rm),
        h("p", { class: "leg-desc muted small", text: st.desc }),
        h("div", { class: "leg-sel" }, stratSel.el, tfSel.el),
        w.el, lo.el, det,
        h("p", { class: "leg-zero small", text: "вес 0 — нога не торгует" }));
      legCards.push(card);
      return card;
    }));
    updateSum();
  }

  const addBtn = h("button", {
    class: "btn", type: "button",
    onClick: () => {
      if (S.legs.length >= MAX_LEGS) { toast(`Не больше ${MAX_LEGS} ног`, "info"); return; }
      const st = meta.strategies[0]!;
      S.legs.push({
        strategy: st.name, tf: meta.portfolio_tfs.includes("4h") ? "4h" : meta.portfolio_tfs[0]!, weight: 0.25, long_only: false,
        params: Object.fromEntries(Object.entries(st.params).map(([k, sp]) => [k, sp.default])),
      });
      renderLegs(); applyMarketUi(); trigger(); notifyContext();
    },
  }, icon("plus", 14), "Добавить ногу");
  const defaultsBtn = h("button", {
    class: "btn ghost", type: "button", title: "Набор ног по умолчанию из дашборда (может отличаться от текущих настроек живого бота)",
    onClick: () => { S.legs = structuredClone(meta.legs); importNote.replaceChildren(); renderLegs(); applyMarketUi(); trigger(); notifyContext(); },
  }, icon("reset", 14), "Стандартный набор");
  const liveBtn = h("button", {
    class: "btn", type: "button", title: "Загрузить ноги, веса и параметры из текущего снимка живого бота (только чтение)",
    onClick: () => void importFromLive(),
  }, icon("bot", 14), "Взять ноги живого бота");

  async function importFromLive() {
    setLoading(liveBtn, true);
    importNote.replaceChildren();
    try {
      const snap = await api.live();
      const r = importLiveLegs(snap, strategies, meta.portfolio_tfs, meta.legs);
      if (!r.ok) {
        importNote.replaceChildren(h("div", { class: "notice warn", role: "alert" }, h("span", null, h("strong", { text: "Не удалось взять ноги бота. " }), r.reason ?? "")));
        return;
      }
      S.legs = r.legs;
      renderLegs(); applyMarketUi(); trigger(); notifyContext();
      const sum = r.legs.reduce((a, l) => a + l.weight, 0);
      const parts = [`Загружено ног: ${r.legs.length}, Σ весов ${pct(sum, 0)}.`,
        r.disabled ? ` Выключенных пропущено: ${r.disabled}.` : "",
        r.skipped.length ? ` Не перенесено: ${r.skipped.join("; ")}.` : "",
        r.legacyLongOnly ? " Признак «только лонг» в снимке бота не хранится — взят из набора по умолчанию." : ""];
      importNote.replaceChildren(h("div", { class: `notice${r.skipped.length ? " warn" : ""}`, role: "status" }, h("span", null, h("strong", { text: "Ноги живого бота. " }), parts.join(""))));
      toast("Ноги бота загружены", "ok");
    } catch (e) {
      const err = asApiError(e);
      if (err.silent) return;
      importNote.replaceChildren(h("div", { class: "notice warn", role: "alert" }, h("span", null, h("strong", { text: "Снимок бота недоступен. " }), err.message)));
    } finally {
      setLoading(liveBtn, false);
    }
  }

  const legsCard = h("section", { class: "card legs-card", "aria-label": "Ноги портфеля" },
    h("header", { class: "card-head" },
      h("h3", { text: "Ноги портфеля" }),
      h("div", { class: "legs-actions" }, liveBtn, defaultsBtn, addBtn)),
    importNote, wsum, legsBox);

  const botText = (): string => {
    const legs = effLegs();
    const lev = effLeverage(S.market, S.leverage);
    const legsTxt = legs.map((l) => `${l.strategy}@${l.tf} (${plainParams(l.params)}; вес ${l.weight})`).join("; ");
    const head = `Портфель: ${S.symbol} | Рынок: ${marketLabel(S.market)} | Плечо: ${lev}x | Комиссия: ${+S.fee.toFixed(4)}% | Проскальзывание: ${+S.slip.toFixed(4)}% | Период: ${S.days} дн. | Ноги: ${legsTxt} | Результат: ${metricsLine(last)}`;
    return [head, ...legs.map((l) => pyLeg(l)), `# плечо: ${lev}x`, ...botNotes(S.market, lev)].join("\n");
  };
  const copyBtn = copyButton("Копировать для бота", botText);
  const sendBtn = sendButton("В бота", botText);
  const bar = h("div", { class: "panel-bar" }, h("div", { class: "pb-row" }, copyBtn, sendBtn));

  const secMarket = panelSection({ key: "pf.market", icon: "candles", title: "Рынок и период", summary: "", open: true, body: [marketSeg.el, marketNoteEl, daysSeg.el] });
  const secLev = panelSection({ key: "pf.leverage", icon: "gauge", title: "Плечо", summary: "", open: true, body: [levS.el] });
  const secCosts = panelSection({ key: "pf.costs", icon: "percent", title: "Издержки", summary: "", body: [feeS.el, slipS.el] });
  function updatePanelSummaries() {
    secMarket.setSummary(`${S.symbol} · ${S.market === "spot" ? "спот · " : ""}${S.days >= 365 && S.days % 365 === 0 ? `${S.days / 365} г.` : `${S.days} д`}`);
    secLev.setSummary(S.market === "spot" ? "1× · спот, без плеча" : `${+S.leverage.toFixed(2)}×`);
    secCosts.setSummary(`комиссия ${num(S.fee, 3)} % · проскальз. ${num(S.slip, 3)} %`);
  }

  const ly = panelLayout([secMarket.el, secLev.el, secCosts.el], [legsCard, result.el], {
    head: pageHead("Портфель бота", "Ноги бота — стратегия × таймфрейм × вес: общий бэктест на часовой сетке с фандингом. Можно взять текущие ноги живого бота и посмотреть, как они торговали бы на истории."),
    bar,
  });

  renderLegs();
  applyMarketUi();
  let started = false;
  return {
    el: ly.el,
    show() { if (!started) { started = true; runner.now(body()); } },
    hide() { /* нет фоновых задач */ },
    dataContext: () => ({ symbol: S.symbol, tfs: [...new Set([meta.base_tf, ...S.legs.map((l) => l.tf)])] }),
    refresh: () => { if (started) runner.now(body()); },
  };
}
