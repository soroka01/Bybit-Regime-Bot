// Вкладка «Портфель»: «ноги» бота (стратегия × таймфрейм × вес), бэктест на часовой сетке с фандингом.
import { api, asApiError } from "../api";
import { t } from "../i18n";
import { strategyDesc } from "../i18n/serverText";
import { signalStrips } from "../charts/strips";
import { MAX_LEVERAGE } from "../leverage";
import { effLeverage, isMarket, MARKET_OPTIONS, marketNote, marketUi, SPOT_LONG_NOTE, switchMarket } from "../market";
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
import { importLiveLegs, isLegCfg, LIVE_WEIGHT_CAP, MAX_LEGS, sanitizeLegs, weightReport, type WeightReport } from "./portfolioLogic";
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
    legs: (v) => v.length <= MAX_LEGS && v.every(isLegCfg),
  });
  const S = P.state;
  const strategies = new Map(meta.strategies.map((s) => [s.name, s]));
  S.legs = sanitizeLegs(S.legs, strategies, meta.portfolio_tfs);        // неизвестное отбрасывается, вес и параметры зажимаются в допустимые пределы
  if (!S.legs.length) S.legs = structuredClone(meta.legs);

  const result = createResultView({ fileHint: () => ["portfolio", S.symbol, `${S.legs.length}legs`] });
  const stripsHost = h("div");
  const strips = signalStrips(stripsHost);
  const stripsLegend = h("div", { class: "strips-legend small muted" },
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--pos)", "aria-hidden": "true" }), t("portfolio.sig.long")),
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--neg)", "aria-hidden": "true" }), t("portfolio.sig.short")),
    h("span", { class: "lg-item" }, h("i", { class: "lg-key area", style: "--k:var(--surface-2);box-shadow:inset 0 0 0 1px var(--border-strong)", "aria-hidden": "true" }), t("portfolio.sig.flat")),
    h("span", { text: t("portfolio.sig.legend") }));
  result.slot.append(h("section", { class: "card" },
    h("header", { class: "card-head" }, h("h3", { text: t("portfolio.sig.title") })),
    h("p", { class: "muted small", text: t("portfolio.sig.note") }),
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
    onRetry: (k, max) => result.setBusy(true, t("portfolio.busy_retry", { k, max })),
    onData: (r) => {
      last = r;
      result.setBusy(false);
      result.setData(r);
      if (r.strips) {
        const rows = Object.entries(r.strips.legs).map(([label, values]) => ({ label, values, max: 1 }));
        const total = S.legs.reduce((a, l) => a + l.weight, 0) || 1;
        rows.push({ label: t("portfolio.sig.total"), values: r.strips.exposure, max: total });
        strips.set(r.strips.t, rows);
      }
    },
    onError: (e) => { result.setBusy(false); result.setError(e, () => runner.now(body())); },
  });
  const trigger = () => { P.save(); updatePanelSummaries(); runner.schedule(body()); };

  // ---------- общие параметры (панель)
  const daysSeg = segmented({ label: t("portfolio.period"), options: DAY_OPTIONS, value: String(S.days), onChange: (v) => { S.days = +v; trigger(); } });
  const levS = leverageControl({ value: S.leverage, botNote: true, onInput: (v) => { S.leverage = v; updateSum(); trigger(); } });
  const feeS = slider({ label: t("portfolio.fee"), min: 0, max: 0.3, step: 0.005, value: S.fee, unit: "%", onInput: (v) => { S.fee = v; S.feeTouched = true; trigger(); } });
  const marketSeg = segmented({
    label: t("portfolio.market"), options: MARKET_OPTIONS, value: S.market,
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
  const slipS = slider({ label: t("portfolio.slip"), min: 0, max: 0.3, step: 0.005, value: S.slip, unit: "%", onInput: (v) => { S.slip = v; trigger(); } });

  // ---------- сумма весов
  const wbar = h("div", { class: "wbar", role: "meter", "aria-label": t("portfolio.wsum.aria"), "aria-valuemin": 0 });
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
    wbar.setAttribute("aria-valuetext", `${rep.headline}${rep.over ? t("portfolio.wsum.over_suffix") : ""}`);
    wbar.replaceChildren(
      ...S.legs.map((l, i) => h("i", { class: "wseg", style: `width:${((l.weight / max) * 100).toFixed(2)}%;--k:var(${LEG_COLORS[i % LEG_COLORS.length]})`, title: t("portfolio.wsum.seg_title", { n: i + 1, leg: `${l.strategy}@${l.tf}`, w: pct(l.weight, 0) }) })),
      h("i", { class: "wmark", "aria-hidden": "true", style: `left:${((LIVE_WEIGHT_CAP / max) * 100).toFixed(2)}%`, title: t("portfolio.wsum.mark_title") }));
    wHead.replaceChildren(
      h("span", { class: `wsum-val ${rep.tone}`, text: rep.headline }),
      h("span", { class: "muted small", text: t("portfolio.wsum.limit", { exp: num(rep.sum * lev, 2) }) }),
      rep.tone === "ok" ? badge(t("portfolio.wsum.badge_ok"), "ok", { glyph: "✓" }) : rep.over ? badge(t("portfolio.wsum.badge_over"), "warn", { glyph: "!" }) : badge(rep.sum <= 1e-9 ? t("portfolio.wsum.badge_none") : t("portfolio.wsum.badge_zero"), rep.sum <= 1e-9 ? "bad" : "warn", { glyph: "!" }));
    wLines.replaceChildren(...rep.lines.map((line) => h("div", { class: `notice${rep.tone === "ok" ? "" : rep.tone === "bad" ? " bad" : " warn"}`, role: rep.over ? "alert" : "status" }, h("span", { text: line }))));
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
          hint: t("portfolio.leg.param_hint", { grid: sp.grid.join(", ") }),
          onInput: (v) => { leg.params[name] = v; changed.textContent = changedText(); trigger(); },
        }).el);
      const changedText = () => { const n = changedParams(leg.params, st.params).length; return n ? t("portfolio.leg.changed", { n }) : t("portfolio.leg.default"); };
      const changed = h("span", { class: "muted small", text: changedText() });
      const det = h("details", { class: "leg-params", open: paramsOpen.has(leg) },
        h("summary", null, h("span", { text: t("portfolio.leg.params", { n: paramEntries.length }) }), changed),
        h("div", { class: "params" }, ...paramSliders));
      det.addEventListener("toggle", () => { if (det.open) paramsOpen.add(leg); else paramsOpen.delete(leg); });
      const stratSel = select({
        label: t("portfolio.leg.strategy"), value: leg.strategy,
        options: meta.strategies.map((s) => ({ value: s.name, label: s.derivative ? t("portfolio.leg.derivative", { name: s.name }) : s.name })),
        onChange: (v) => {
          leg.strategy = v;
          leg.params = Object.fromEntries(Object.entries(strategies.get(v)!.params).map(([k, sp]) => [k, sp.default]));
          renderLegs(); applyMarketUi(); trigger();
        },
      });
      const tfSel = select({
        label: t("portfolio.leg.timeframe"), value: leg.tf,
        options: meta.portfolio_tfs.map((t) => ({ value: t, label: t })),
        onChange: (v) => { leg.tf = v; updateSum(); trigger(); notifyContext(); },
      });
      const w = slider({ label: t("portfolio.leg.weight"), min: 0, max: 1.5, step: 0.05, value: leg.weight, onInput: (v) => { leg.weight = v; updateSum(); trigger(); } });
      const spot = S.market === "spot";
      const lo = toggle({ label: t("portfolio.leg.long_only"), checked: spot || leg.long_only, onChange: (v) => { leg.long_only = v; trigger(); } });
      lo.disable(marketUi(S.market).longLocked, SPOT_LONG_NOTE);
      const rm = h("button", {
        class: "btn ghost icon-btn sm", type: "button", disabled: S.legs.length <= 1,
        "aria-label": t("portfolio.leg.remove_aria", { n: idx + 1 }), "data-tip": S.legs.length <= 1 ? t("portfolio.leg.keep_one") : t("portfolio.leg.remove_tip"), "data-tip-pos": "left",
        onClick: () => { S.legs.splice(idx, 1); renderLegs(); applyMarketUi(); trigger(); notifyContext(); },
      }, icon("trash", 14));
      const chip = h("span", { class: "leg-w", title: t("portfolio.leg.weight_title"), text: pct(leg.weight, 0) });
      legChips.push(chip);
      const card = h("section", { class: "leg leg-card", style: `--leg:var(${color})`, "aria-label": t("portfolio.leg.title", { n: idx + 1 }) },
        h("header", { class: "leg-head" },
          h("span", { class: "leg-n", "aria-hidden": "true", text: String(idx + 1) }),
          h("div", { class: "leg-title" }, h("strong", { text: t("portfolio.leg.title", { n: idx + 1 }) }), h("span", { class: "muted small", text: `${leg.strategy} · ${leg.tf}` })),
          chip, rm),
        h("p", { class: "leg-desc muted small", text: strategyDesc(st.name, st.desc) }),
        h("div", { class: "leg-sel" }, stratSel.el, tfSel.el),
        w.el, lo.el, det,
        h("p", { class: "leg-zero small", text: t("portfolio.leg.zero_note") }));
      legCards.push(card);
      return card;
    }));
    updateSum();
  }

  const addBtn = h("button", {
    class: "btn", type: "button",
    onClick: () => {
      if (S.legs.length >= MAX_LEGS) { toast(t("portfolio.legs.max", { n: MAX_LEGS }), "info"); return; }
      const st = meta.strategies[0]!;
      S.legs.push({
        strategy: st.name, tf: meta.portfolio_tfs.includes("4h") ? "4h" : meta.portfolio_tfs[0]!, weight: 0.25, long_only: false,
        params: Object.fromEntries(Object.entries(st.params).map(([k, sp]) => [k, sp.default])),
      });
      renderLegs(); applyMarketUi(); trigger(); notifyContext();
    },
  }, icon("plus", 14), t("portfolio.legs.add"));
  const defaultsBtn = h("button", {
    class: "btn ghost", type: "button", title: t("portfolio.legs.defaults_tip"),
    onClick: () => { S.legs = structuredClone(meta.legs); importNote.replaceChildren(); renderLegs(); applyMarketUi(); trigger(); notifyContext(); },
  }, icon("reset", 14), t("portfolio.legs.defaults"));
  const liveBtn = h("button", {
    class: "btn", type: "button", title: t("portfolio.legs.live_tip"),
    onClick: () => void importFromLive(),
  }, icon("bot", 14), t("portfolio.legs.live"));

  async function importFromLive() {
    setLoading(liveBtn, true);
    importNote.replaceChildren();
    try {
      const snap = await api.live();
      const r = importLiveLegs(snap, strategies, meta.portfolio_tfs);
      if (!r.ok) {
        importNote.replaceChildren(h("div", { class: "notice warn", role: "alert" }, h("span", null, h("strong", { text: t("portfolio.import.fail") }), r.reason ?? "")));
        return;
      }
      S.legs = r.legs;
      renderLegs(); applyMarketUi(); trigger(); notifyContext();
      const sum = r.legs.reduce((a, l) => a + l.weight, 0);
      const parts = [t("portfolio.import.loaded", { n: r.legs.length, sum: pct(sum, 0) }),
        r.disabled ? t("portfolio.import.disabled", { n: r.disabled }) : "",
        r.skipped.length ? t("portfolio.import.skipped", { list: r.skipped.join("; ") }) : ""];
      importNote.replaceChildren(h("div", { class: `notice${r.skipped.length ? " warn" : ""}`, role: "status" }, h("span", null, h("strong", { text: t("portfolio.import.head") }), parts.join(""))));
      toast(t("portfolio.import.toast"), "ok");
    } catch (e) {
      const err = asApiError(e);
      if (err.silent) return;
      importNote.replaceChildren(h("div", { class: "notice warn", role: "alert" }, h("span", null, h("strong", { text: t("portfolio.import.unavailable") }), err.message)));
    } finally {
      setLoading(liveBtn, false);
    }
  }

  const legsCard = h("section", { class: "card legs-card", "aria-label": t("portfolio.legs.title") },
    h("header", { class: "card-head" },
      h("h3", { text: t("portfolio.legs.title") }),
      h("div", { class: "legs-actions" }, liveBtn, defaultsBtn, addBtn)),
    importNote, wsum, legsBox);

  const botText = (): string => {
    const legs = effLegs();
    const lev = effLeverage(S.market, S.leverage);
    const legsTxt = legs.map((l) => `${l.strategy}@${l.tf} (${plainParams(l.params)}; вес ${l.weight})`).join("; ");
    const marketRu = S.market === "spot" ? "Спот" : "Перп";      // протокол для русского бота (core/legspec.py): слова остаются русскими на любом языке
    const head = `Портфель: ${S.symbol} | Рынок: ${marketRu} | Плечо: ${lev}x | Комиссия: ${+S.fee.toFixed(4)}% | Проскальзывание: ${+S.slip.toFixed(4)}% | Период: ${S.days} дн. | Ноги: ${legsTxt} | Результат: ${metricsLine(last)}`;
    return [head, ...legs.map((l) => pyLeg(l)), `# плечо: ${lev}x`, ...botNotes(S.market, lev)].join("\n");
  };
  const copyBtn = copyButton(t("portfolio.copy_bot"), botText);
  const sendBtn = sendButton(t("portfolio.send_bot"), botText);
  const bar = h("div", { class: "panel-bar" }, h("div", { class: "pb-row" }, copyBtn, sendBtn));

  const secMarket = panelSection({ key: "pf.market", icon: "candles", title: t("portfolio.sec.market"), summary: "", open: true, body: [marketSeg.el, marketNoteEl, daysSeg.el] });
  const secLev = panelSection({ key: "pf.leverage", icon: "gauge", title: t("portfolio.sec.leverage"), summary: "", open: true, body: [levS.el] });
  const secCosts = panelSection({ key: "pf.costs", icon: "percent", title: t("portfolio.sec.costs"), summary: "", body: [feeS.el, slipS.el] });
  function updatePanelSummaries() {
    secMarket.setSummary(`${S.symbol} · ${S.market === "spot" ? t("portfolio.sum.spot") : ""}${S.days >= 365 && S.days % 365 === 0 ? t("portfolio.sum.years", { n: S.days / 365 }) : t("portfolio.sum.days", { n: S.days })}`);
    secLev.setSummary(S.market === "spot" ? t("portfolio.sum.lev_spot") : `${+S.leverage.toFixed(2)}×`);
    secCosts.setSummary(t("portfolio.sum.costs", { fee: num(S.fee, 3), slip: num(S.slip, 3) }));
  }

  const ly = panelLayout([secMarket.el, secLev.el, secCosts.el], [legsCard, result.el], {
    head: pageHead(t("portfolio.head.title"), t("portfolio.head.sub")),
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
