// Панель «Развёртка параметров» Лаборатории: двумерная карта или одномерный профиль метрики по значениям параметров.
import { api, asApiError } from "../api";
import { SWEEP_METRICS, sweepHeatmap, sweepProfile } from "../charts/heatmap";
import { t } from "../i18n";
import type { BacktestBody, StrategyMeta, SweepResult } from "../types";
import { errorBanner } from "../ui/banner";
import { select, slider } from "../ui/controls";
import { h } from "../ui/dom";
import { icon } from "../ui/icons";
import { emptyState, skeletonChart } from "../ui/kit";

/** Часть состояния Лаборатории, которой владеет панель (хранится вместе с остальным состоянием вкладки). */
interface SweepState { sx: string; sy: string; sMetric: string; sN: number }

interface SweepHostApi {
  state: SweepState;
  save(): void;
  strat(): StrategyMeta;
  /** Тело бэктеста с текущими настройками Лаборатории. */
  body(): BacktestBody;
  /** Текущие значения параметров выбранной стратегии. */
  currentParams(): Record<string, number>;
  /** Применить значения параметров (клик по ячейке/точке): обновить слайдеры и пересчитать бэктест. */
  apply(vals: Record<string, number>): void;
}

export function createSweepPanel(api_: SweepHostApi) {
  const S = api_.state;
  const host = h("div", { class: "sweep-out" });
  const msg = h("p", { class: "small sweep-msg", role: "status", "aria-live": "polite" });
  const staleBtn = h("button", { class: "btn sm", type: "button", text: t("lab.sweep.rebuild"), onClick: () => void run() });
  const staleEl = h("div", { class: "notice warn sw-stale", role: "status", hidden: true },
    h("span", null, h("strong", { text: `${t("lab.sweep.staleTitle")} ` }), h("span", { class: "sw-stale-why", text: t("lab.sweep.staleWhy") })), staleBtn);
  const staleWhy = staleEl.querySelector(".sw-stale-why") as HTMLElement;
  let res: SweepResult | null = null;
  let resStrategy = "";          // стратегия, для которой посчитан res
  let resKey = "";               // отпечаток входов расчёта (для пометки «устарело»)
  let stale = false;
  let busy = false;
  let seq = 0;
  let ctl: AbortController | null = null;
  let disposeChart: () => void = () => {};

  const names = () => Object.keys(api_.strat().params);
  const otherThan = (n: string) => names().find((x) => x !== n) ?? "";

  const xSel = select({
    label: t("lab.sweep.axisX"), value: "", options: [],
    onChange: (v) => {
      S.sx = v;
      if (names().length > 1 && S.sy === v) {
        S.sy = otherThan(v);
        ySel.set(S.sy);
        msg.textContent = t("lab.sweep.axesDifferReplaced", { name: S.sy });
      } else msg.textContent = "";
      api_.save();
    },
  });
  const ySel = select({
    label: t("lab.sweep.axisY"), value: "", options: [],
    onChange: (v) => {
      if (v === S.sx) {
        S.sy = otherThan(S.sx);
        ySel.set(S.sy);
        msg.textContent = t("lab.sweep.axesDifferPicked", { name: S.sy });
      } else { S.sy = v; msg.textContent = ""; }
      api_.save();
    },
  });
  // метрика — чипы (смена только перерисовывает уже посчитанную карту)
  const metricBtns = new Map<string, HTMLButtonElement>();
  const metricBox = h("div", { class: "sw-metrics", role: "group", "aria-label": t("lab.sweep.metric") });
  for (const m of SWEEP_METRICS) {
    const b = h("button", { type: "button", class: "chip-btn", text: m.label, onClick: () => { S.sMetric = m.key; api_.save(); paintMetric(); render(); } });
    metricBtns.set(m.key, b);
    metricBox.append(b);
  }
  const paintMetric = () => metricBtns.forEach((b, k) => b.setAttribute("aria-pressed", String(k === S.sMetric)));
  const nS = slider({ label: t("lab.sweep.steps"), min: 4, max: 20, step: 1, value: S.sN, onInput: (v) => { S.sN = v; api_.save(); } });
  const runBtn = h("button", { class: "btn primary", type: "button", onClick: () => void run() });
  const btnIdle = () => { runBtn.replaceChildren(icon("grid", 14), names().length < 2 ? t("lab.sweep.buildProfile") : t("lab.sweep.buildMap")); };

  /** Всё, от чего зависит результат развёртки, кроме значений самих осей (их меняет клик по карте). */
  function inputsKey(): string {
    const { params, ...rest } = api_.body();
    const others = Object.fromEntries(Object.entries(params).filter(([k]) => k !== S.sx && k !== S.sy));
    return JSON.stringify({ ...rest, others });
  }

  function setHost(...nodes: (Node | string)[]) {
    disposeChart();
    disposeChart = () => {};
    host.replaceChildren(...nodes);
  }

  function markStale(why?: string) {
    stale = true;
    host.classList.add("stale");
    if (why) staleWhy.textContent = why;
    staleEl.hidden = false;
  }
  function clearStale() {
    stale = false;
    host.classList.remove("stale");
    staleEl.hidden = true;
  }

  function fillAxes() {
    const ns = names();
    if (!ns.includes(S.sx)) S.sx = ns[0] ?? "";
    const one = ns.length < 2;
    if (one) S.sy = "";
    else if (!ns.includes(S.sy) || S.sy === S.sx) S.sy = otherThan(S.sx);
    const opts = ns.map((n) => ({ value: n, label: n }));
    xSel.setOptions(opts, S.sx);
    ySel.setOptions(opts, S.sy);
    ySel.el.hidden = one;                       // у однопараметрических стратегий оси Y нет
    xSel.el.querySelector("label")!.textContent = one ? t("lab.sweep.param") : t("lab.sweep.axisX");
    runBtn.disabled = false;
    runBtn.title = one ? t("lab.sweep.profileTip") : "";
    msg.textContent = "";
    paintMetric();
    btnIdle();
  }

  /** Отмена текущего расчёта (смена стратегии/символа/таймфрейма): результат уже не нужен. */
  function abort() {
    seq++;
    ctl?.abort();
    ctl = null;
    busy = false;
    runBtn.disabled = false;
    btnIdle();
  }

  /** Сбросить результат и показать подсказку (вызывается при смене символа/таймфрейма/стратегии). */
  function clear() {
    abort();
    res = null;
    clearStale();
    const ns = names();
    const title = ns.length < 2 ? t("lab.sweep.emptyProfileTitle", { name: ns[0] ?? "" }) : t("lab.sweep.emptyMapTitle");
    const text = ns.length < 2
      ? t("lab.sweep.emptyProfile")
      : t("lab.sweep.emptyMap", { btn: t("lab.sweep.buildMap") }) +
        (ns.length > 2 ? ` ${t("lab.sweep.others")}` : "");
    setHost(emptyState(title, text, null, { icon: "grid" }));
  }

  /** Вызывается при любом изменении настроек Лаборатории: если изменились входы расчёта — помечаем результат устаревшим. */
  function inputsChanged() {
    if (!res || stale) return;
    if (inputsKey() === resKey) return;
    markStale();
  }

  const keyRow = (best: boolean) => h("div", { class: "sw-key small muted" },
    h("span", null, h("i", { class: "sw-ring", "aria-hidden": "true" }), ` ${t("lab.sweep.keyCurrent")}`),
    best ? h("span", null, h("i", { class: "sw-star", "aria-hidden": "true", text: "★" }), ` ${t("lab.sweep.keyBest")}`) : null,
    h("span", null, icon("check", 12), ` ${t("lab.sweep.keyApply")}`));

  function render() {
    if (!res) return;
    const r = res;
    // защита от рассинхронизации: результат другой стратегии/осей не рисуем под новыми именами
    const st = api_.strat();
    if (resStrategy !== st.name || !(r.x.name in st.params) || (r.y && !(r.y.name in st.params))) { clear(); return; }
    const cur = api_.currentParams();
    const mLabel = SWEEP_METRICS.find((m) => m.key === S.sMetric)?.label ?? "";
    if (!r.y) {
      const prof = sweepProfile(r, S.sMetric, cur[r.x.name] ?? 0, (x) => applyPick({ [r.x.name]: x }));
      setHost(h("p", { class: "sw-cap small muted" }, h("strong", { text: mLabel }), ` ${t("lab.sweep.capProfile", { x: r.x.name, n: r.x.values.length })}`), prof.el, keyRow(true));
      disposeChart = prof.destroy;
      return;
    }
    const others = names().length > 2 ? ` ${t("lab.sweep.others")}` : "";
    setHost(
      h("p", { class: "sw-cap small muted" }, h("strong", { text: mLabel }), ` ${t("lab.sweep.capMap", { x: r.x.name, y: r.y.name, a: r.x.values.length, b: r.y.values.length, others })}`),
      sweepHeatmap(r, S.sMetric, cur, (x, y) => applyPick({ [r.x.name]: x, [r.y!.name]: y })), keyRow(true));
  }

  function applyPick(vals: Record<string, number>) {
    api_.apply(vals);
    window.setTimeout(render, 400);
  }

  async function run() {
    if (busy || !S.sx) return;
    const two = names().length > 1;
    if (two && (!S.sy || S.sy === S.sx)) {   // не молчим: заменяем ось Y и сообщаем
      S.sy = otherThan(S.sx);
      ySel.set(S.sy);
      msg.textContent = t("lab.sweep.axesDifferReplaced", { name: S.sy });
    } else msg.textContent = "";
    const my = ++seq;
    ctl?.abort();
    const c = (ctl = new AbortController());
    busy = true;
    clearStale();
    runBtn.disabled = true;
    runBtn.replaceChildren(h("span", { class: "spinner" }), t("lab.busy"));
    setHost(h("div", { class: "inline-busy" }, h("div", { class: "spinner" }), h("span", { text: two ? t("lab.sweep.busyMap") : t("lab.sweep.busyProfile") })), skeletonChart(two ? 200 : 180));
    // всё, от чего зависит ответ, фиксируем в момент запроса
    const b = api_.body();
    const st = api_.strat();
    const stName = st.name;
    const key = inputsKey();
    const ax = (n: string) => ({ name: n, min: st.params[n]!.grid[0]!, max: st.params[n]!.grid.at(-1)!, n: Math.round(S.sN) });
    try {
      const r = await api.sweep({ ...b, x: ax(S.sx), ...(two ? { y: ax(S.sy) } : {}) }, c.signal);
      if (my !== seq || api_.strat().name !== stName) return;   // стратегия сменилась / запрос отменён — результат не нужен
      res = r; resStrategy = stName; resKey = key;
      render();
      if (inputsKey() !== key) markStale(t("lab.sweep.staleMid"));
    } catch (e) {
      if (my !== seq) return;
      const err = asApiError(e);
      if (err.silent) return;
      setHost(errorBanner(err, () => void run()));
    } finally {
      if (my === seq) {
        busy = false;
        ctl = null;
        runBtn.disabled = false;
        btnIdle();
      }
    }
  }

  const el = h("section", { class: "card sweep", "aria-label": t("lab.sweep.title") },
    h("header", { class: "card-head" },
      h("h3", { text: t("lab.sweep.title") }),
      h("span", { class: "muted small", text: t("lab.sweep.sub") })),
    h("div", { class: "sweep-controls" }, xSel.el, ySel.el, nS.el, runBtn),
    h("div", { class: "field sw-metric-field" }, h("div", { class: "field-head" }, h("span", { class: "field-label", text: t("lab.sweep.metric") })), metricBox),
    msg, staleEl, host);

  fillAxes();
  clear();

  return { el, fillAxes, clear, render, inputsChanged, abort, dispose: () => { abort(); disposeChart(); } };
}
