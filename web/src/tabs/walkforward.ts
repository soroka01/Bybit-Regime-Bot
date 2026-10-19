// Вкладка «Walk-forward»: сводка, таблица вариантов с оценкой, OOS-кривые выбранных, доходность по тестовым окнам.
import { api, asApiError } from "../api";
import { alignSeries } from "../charts/align";
import { barChart } from "../charts/bars";
import { lineChart, type LineChart } from "../charts/line";
import { persisted } from "../state";
import type { Curve, Meta, WfRow, WfWindow } from "../types";
import { errorBanner } from "../ui/banner";
import { card } from "../ui/card";
import { segmented, select, toggle } from "../ui/controls";
import { h } from "../ui/dom";
import { date, int, num, pct, sgn, spct } from "../ui/fmt";
import { icon } from "../ui/icons";
import { badge, disclosure, emptyState, pageHead, skeletonChart, skeletonTile } from "../ui/kit";
import { dataTable } from "../ui/table";
import type { Tab } from "./tab";
import { windowMatrix, windowSpan, windowTone, wfVerdict } from "./wfLogic";

interface WState {
  lev: number;
  tf: string;
  strategy: string;
  variant: string;
  minTrades: number;
  q: string;
  selected: string[];
  log: boolean;
}

const keyOf = (r: { tf: string; strategy: string; variant: string }) => `${r.tf}|${r.strategy}|${r.variant}`;
const labelOf = (r: { tf: string; strategy: string; variant: string }) => `${r.strategy} ${r.variant} · ${r.tf}`;
const COLORS = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6", "--c7", "--c8"];
const MAX_SEL = 8;
/** Результаты walk-forward считаются по одному символу; выбора символа в интерфейсе нет. */
const WF_SYMBOL = "BTCUSDT";
const VERDICT_ORDER: Record<string, number> = { stable: 0, unstable: 1, few: 2, loss: 3 };

export function createWalkForward(meta: Meta): Tab {
  const levs = meta.walkforward_levs.length ? meta.walkforward_levs : [1];
  const P = persisted<WState>("wf", { lev: 1, tf: "", strategy: "", variant: "", minTrades: 0, q: "", selected: [], log: true }, {
    lev: (v) => levs.includes(v), minTrades: (v) => v >= 0 && v <= 1000,
    selected: (v) => v.length <= MAX_SEL && v.every((x) => typeof x === "string"),
  });
  const S = P.state;
  if (!levs.includes(S.lev)) S.lev = levs[0]!;

  let rows: WfRow[] = [];
  let windows: WfWindow[] = [];
  let filtered: WfRow[] = [];
  const curveCache = new Map<string, Curve>();
  let loadSeq = 0;

  const status = h("div", { class: "banner-slot" });
  const how = disclosure("Как читать эти результаты", [
    h("p", { text: "Walk-forward: параметры подбираются на 12 месяцах, затем торгуются 3 месяца «вслепую» (out-of-sample). Кривая — склейка таких кусков." }),
    h("ul", null,
      h("li", { text: "Выборка OOS короткая (≈ 2 года, 8 окон): доверительные интервалы очень широкие, один удачный квартал способен перевесить всё остальное." }),
      h("li", { text: "В таблице сотни вариантов (стратегия × таймфрейм × надстройка). Лучшие из них отобраны по результату на тех же данных — это смещение отбора (best-of-many): ожидайте меньше, чем показывает топ списка." }),
      h("li", { text: "Прошлый результат не гарантирует будущий. Плечо усиливает и прибыль, и риск ликвидации; комиссии и проскальзывание учтены приблизительно." }),
      h("li", { text: "Высокое плечо (10× и выше): ликвидация по модели с поддерживающей маржой 0,5 % срабатывает при небольшом ходе против позиции и обнуляет капитал свечи (−100 %), поэтому у таких вариантов видны просадки до −100 % и кривые, упавшие в ноль. Счётчик «Ликвид.» скрипт walk-forward пока не заполняет — ориентируйтесь на просадку." })),
    h("p", { class: "small", text: "Оценка в таблице — быстрая сводка по строке, не серверный вердикт: «убыточен» — OOS не в плюсе; «мало сделок» — меньше 30; «стабилен» — не меньше 60 % прибыльных окон и средний месяц > 0; иначе «нестабилен»." }),
  ], { key: "wf.how", defaultOpen: window.innerWidth >= 900, badge: "смещение отбора" });
  how.classList.add("wf-how");

  // ---------- сводка
  const summary = h("div", { class: "wf-summary", "aria-live": "polite" });
  const levSeg = segmented({
    label: "Плечо", value: String(S.lev),
    options: levs.map((l) => ({ value: String(l), label: `${l}×` })),     // список плеч — с сервера (какие файлы wf_*_lev*.csv есть)
    onChange: (v) => { S.lev = +v; P.save(); load(); },
  });
  const tile = (label: string, value: string, sub: string | (Node | string)[], extra?: HTMLElement | null): HTMLElement =>
    h("div", { class: "metric wf-tile" }, h("div", { class: "metric-top" }, h("div", { class: "metric-label", text: label })),
      h("div", { class: "metric-value wf-v", text: value }), h("div", { class: "metric-sub" }, ...(Array.isArray(sub) ? sub : [sub])), extra ?? null);

  const clearSel = h("button", { class: "btn ghost sm", type: "button", onClick: () => { S.selected = []; P.save(); table.refresh(); renderSelection(); paintSummary(); } }, icon("x", 12), "снять");
  function paintSummary() {
    if (!rows.length) { summary.replaceChildren(); return; }
    const span = windowSpan(windows);
    const filters = [S.tf && `ТФ ${S.tf}`, S.strategy, S.variant, S.minTrades > 0 && `≥ ${S.minTrades} сделок`, S.q.trim() && `«${S.q.trim()}»`].filter(Boolean) as string[];
    const best = [...filtered].filter((r) => r.total_return != null).sort((a, b) => (b.total_return ?? 0) - (a.total_return ?? 0))[0];
    summary.replaceChildren(
      h("div", { class: "metric wf-tile wf-lev" }, h("div", { class: "metric-top" }, h("div", { class: "metric-label", text: "Плечо" })), levSeg.el, h("div", { class: "metric-sub", text: `символ ${WF_SYMBOL}` })),
      tile("Вариантов", `${int(filtered.length)}`, filters.length ? `из ${int(rows.length)} · фильтр: ${filters.join(" · ")}` : `из ${int(rows.length)} · без фильтров`),
      tile("Тестовых окон", span ? String(span.count) : "—", span ? `с ${date(span.first)} · по 3 месяца каждое (последнее с ${date(span.lastStart)})` : "нет данных об окнах"),
      tile("Выбрано кривых", `${S.selected.length} / ${MAX_SEL}`, S.selected.length ? [] : "клик по строке таблицы", S.selected.length ? clearSel : null),
      ...(best ? [tile("Лучший по OOS", spct(best.total_return, 0), [h("span", { class: "wf-best-name", text: labelOf(best), title: "Отобран по результату на тех же данных — смещение отбора" })])] : []));
  }

  // ---------- таблица
  const verdictCell = (r: WfRow) => { const v = wfVerdict(r); return badge(v.text, v.tone, { glyph: v.glyph, title: v.hint }); };
  const table = dataTable<WfRow>({
    caption: "Варианты walk-forward",
    sort: { key: "total_return", dir: -1 },
    limit: 100,
    rowKey: keyOf,
    isSelected: (r) => S.selected.includes(keyOf(r)),
    empty: "Нет вариантов под фильтр",
    onRowClick: (r) => {
      const k = keyOf(r);
      const i = S.selected.indexOf(k);
      if (i >= 0) S.selected.splice(i, 1);
      else if (S.selected.length < MAX_SEL) S.selected.push(k);
      else S.selected = [...S.selected.slice(1), k];
      P.save();
      table.refresh();
      renderSelection();
      paintSummary();
    },
    columns: [
      { key: "sel", label: "", align: "left", title: "Цвет кривой выбранного варианта", value: (r) => { const i = S.selected.indexOf(keyOf(r)); return i < 0 ? 99 : i; },
        node: (r) => { const i = S.selected.indexOf(keyOf(r)); return h("i", { class: `wf-dot${i >= 0 ? " on" : ""}`, style: i >= 0 ? `--k:var(${COLORS[i % COLORS.length]})` : "", "aria-label": i >= 0 ? "выбран" : "не выбран", role: "img" }); } },
      { key: "tf", label: "ТФ", align: "left", value: (r) => r.tf },
      { key: "strategy", label: "Стратегия", align: "left", value: (r) => r.strategy },
      { key: "variant", label: "Вариант", align: "left", value: (r) => r.variant },
      { key: "verdict", label: "Оценка", align: "left", title: "Быстрая сводка по строке (не серверный вердикт)", value: (r) => VERDICT_ORDER[wfVerdict(r).key] ?? 9, node: verdictCell },
      { key: "total_return", label: "Доходность OOS", value: (r) => r.total_return, render: (r) => spct(r.total_return, 0), cls: (r) => sgn(r.total_return) },
      { key: "bh", label: "B&H", value: (r) => r.buy_hold_return, render: (r) => spct(r.buy_hold_return, 0), cls: (r) => sgn(r.buy_hold_return) },
      { key: "max_drawdown", label: "Макс. DD", value: (r) => r.max_drawdown, render: (r) => pct(r.max_drawdown, 0), cls: () => "neg" },
      { key: "sharpe", label: "Sharpe", value: (r) => r.sharpe, render: (r) => num(r.sharpe, 2), cls: (r) => sgn(r.sharpe) },
      { key: "calmar", label: "Calmar", value: (r) => r.calmar, render: (r) => num(r.calmar, 2), cls: (r) => sgn(r.calmar) },
      { key: "avg_month", label: "Ср. месяц", value: (r) => r.avg_month, render: (r) => spct(r.avg_month, 1), cls: (r) => sgn(r.avg_month) },
      { key: "m15", label: "Мес. ≥15%", value: (r) => r.months_15plus, render: (r) => pct(r.months_15plus, 0) },
      { key: "trades", label: "Сделок", value: (r) => r.trades, render: (r) => int(r.trades) },
      { key: "win", label: "Винрейт", value: (r) => r.win_rate, render: (r) => pct(r.win_rate, 0) },
      { key: "wpos", label: "Окон > 0", value: (r) => r.windows_positive, render: (r) => pct(r.windows_positive, 0), title: "Доля прибыльных тестовых окон" },
      { key: "liq", label: "Ликвид.", value: (r) => r.liquidations, render: (r) => int(r.liquidations), cls: (r) => ((r.liquidations ?? 0) > 0 ? "neg" : "") },
    ],
  });

  const countNote = h("p", { class: "muted small wf-count" });
  const applyFilter = () => {
    const q = S.q.trim().toLowerCase();
    filtered = rows.filter((r) =>
      (!S.tf || r.tf === S.tf) && (!S.strategy || r.strategy === S.strategy) && (!S.variant || r.variant === S.variant) &&
      (r.trades ?? 0) >= S.minTrades && (!q || `${r.strategy} ${r.variant}`.toLowerCase().includes(q)));
    table.setRows(filtered);
    countNote.textContent = `Показано ${filtered.length} из ${rows.length} вариантов. Клик по строке — добавить или убрать кривую (до ${MAX_SEL}).`;
    paintSummary();
  };

  const tfSel = select({ label: "Таймфрейм", value: S.tf, options: [{ value: "", label: "Все" }], onChange: (v) => { S.tf = v; P.save(); applyFilter(); } });
  const stSel = select({ label: "Стратегия", value: S.strategy, options: [{ value: "", label: "Все" }], onChange: (v) => { S.strategy = v; P.save(); applyFilter(); } });
  const vaSel = select({ label: "Вариант", value: S.variant, options: [{ value: "", label: "Все" }], onChange: (v) => { S.variant = v; P.save(); applyFilter(); } });
  const mt = h("input", { type: "number", min: 0, max: 1000, step: 5, value: S.minTrades, class: "num-input", "aria-label": "Минимум сделок" });
  mt.addEventListener("input", () => { S.minTrades = Math.max(0, +mt.value || 0); P.save(); applyFilter(); });
  const q = h("input", { type: "search", placeholder: "Поиск…", value: S.q, "aria-label": "Поиск по стратегии", class: "search" });
  q.addEventListener("input", () => { S.q = q.value; P.save(); applyFilter(); });

  const filters = h("div", { class: "filters wf-filters" },
    tfSel.el, stSel.el, vaSel.el,
    h("div", { class: "field" }, h("div", { class: "field-head" }, h("label", { text: "Мин. сделок" })), mt),
    h("div", { class: "field" }, h("div", { class: "field-head" }, h("label", { text: "Поиск" })), q));

  // ---------- графики выбранных
  const curveHost = h("div", { class: "chart" });
  const curveEmpty = emptyState("Кривых пока нет", "Выберите строки в таблице выше — здесь появятся OOS-кривые капитала (до 8 вариантов).", null, { icon: "chart" });
  let curveChart: LineChart | null = null;
  const logT = toggle({ label: "лог. шкала", checked: S.log, onChange: (v) => { S.log = v; P.save(); curveChart?.setLog(v); } });
  const chipsBox = h("div", { class: "wf-chips" });

  const selectedRows = () => S.selected.map((k) => rows.find((r) => keyOf(r) === k)).filter((r): r is WfRow => !!r);

  const barHost = h("div", { class: "chart" });
  const bars = barChart(barHost, () => {
    const sel = selectedRows();
    const starts = [...new Set(windows.map((w) => w.test_start))].sort();
    return {
      categories: starts.map((s) => date(s)),
      series: sel.map((r) => ({
        name: labelOf(r), color: COLORS[S.selected.indexOf(keyOf(r)) % COLORS.length]!,
        values: starts.map((s) => windows.find((w) => keyOf(w) === keyOf(r) && w.test_start === s)?.test_return ?? null),
        titles: starts.map((s) => windows.find((w) => keyOf(w) === keyOf(r) && w.test_start === s)?.params ?? ""),
      })),
      fmt: (v) => pct(v, 0), height: 220,
    };
  });
    const barsEmpty = emptyState("Окна выбранных вариантов появятся здесь", "Выберите строки в таблице: по каждому окну — столбец и ячейка матрицы.", null, { icon: "chart" });
  const barsBody = h("div");

  function paintChips(sel: WfRow[]) {
    chipsBox.replaceChildren(...sel.map((r, i) => h("span", { class: "wf-chip", style: `--k:var(${COLORS[i % COLORS.length]})` },
      h("i", { class: "wf-dot on", "aria-hidden": "true" }), h("span", { text: labelOf(r) }),
      h("button", { type: "button", class: "wf-chip-x", "aria-label": `Убрать ${labelOf(r)}`, onClick: () => {
        S.selected = S.selected.filter((k) => k !== keyOf(r)); P.save(); table.refresh(); renderSelection(); paintSummary();
      } }, icon("x", 11)))));
  }

  /** Матрица «окно × выбранные варианты» (текстовая копия столбчатого графика: значения читаются без наведения). */
  function paintMatrix(sel: WfRow[]) {
    const m = windowMatrix(sel.map(keyOf), windows, (w) => keyOf(w));
    if (!sel.length || !m.starts.length) { barsBody.replaceChildren(barsEmpty); return; }
    const head = h("tr", null, h("th", { class: "left", scope: "col", text: "Окно (начало)" }), ...sel.map((r, i) => h("th", { scope: "col", title: labelOf(r), style: `--k:var(${COLORS[i % COLORS.length]})` }, h("span", { class: "wf-th" }, h("i", { class: "wf-dot on", "aria-hidden": "true" }), r.strategy, h("span", { class: "muted", text: ` ${r.variant} · ${r.tf}` })))));
    const body = m.starts.map((s, j) => h("tr", null, h("th", { class: "left", scope: "row", text: date(s) }),
      ...m.rows.map((row) => {
        const v = row.cells[j];
        const tone = windowTone(v ?? null);
        return h("td", { class: `wf-cell ${tone}`, text: v == null ? "—" : `${tone === "pos" ? "▲ " : tone === "neg" ? "▼ " : ""}${spct(v, 1)}` });
      })));
    const foot = h("tr", { class: "wf-foot" }, h("th", { class: "left", scope: "row", text: "Прибыльных окон" }),
      ...m.rows.map((row) => h("td", { class: row.total && row.positive / row.total >= 0.6 ? "pos strong" : "strong", text: row.total ? `${row.positive} из ${row.total} · ${pct(row.positive / row.total, 0)}` : "—" })));
    barsBody.replaceChildren(
      h("div", { class: "table-wrap wf-matrix", tabindex: 0, role: "region", "aria-label": "Доходность по тестовым окнам" }, h("table", { class: "data" }, h("thead", null, head), h("tbody", null, ...body), h("tfoot", null, foot))));
  }

  let selSeq = 0;
  async function renderSelection() {
    const my = ++selSeq;
    const sel = selectedRows();
    curveChart?.destroy();
    curveChart = null;
    curveHost.replaceChildren();
    barHost.hidden = !sel.length;
    bars.redraw();
    paintChips(sel);
    paintMatrix(sel);
    if (!sel.length) { curveHost.append(curveEmpty); return; }
    try {
      const cs = await Promise.all(sel.map(async (r) => {
        const k = `${S.lev}|${keyOf(r)}`;
        let c = curveCache.get(k);
        if (!c) { c = await api.wfCurve(S.lev, WF_SYMBOL, r.tf, r.strategy, r.variant); curveCache.set(k, c); }
        return c;
      }));
      if (my !== selSeq) return;
      const { x, ys } = alignSeries(cs.map((c) => ({ t: c.t, v: c.equity })));
      curveChart = lineChart(curveHost, {
        height: 320, log: S.log, yFmt: (v) => "×" + num(v, v < 10 ? 2 : 1),
        series: sel.map((r, i) => ({ label: labelOf(r), color: COLORS[i % COLORS.length]!, width: 1.8 })),
      });
      curveChart.setData(x, ys);
    } catch (e) {
      if (my !== selSeq) return;
      const err = asApiError(e);
      curveHost.replaceChildren(errorBanner(err, renderSelection));
    }
  }

  const content = h("div", { class: "wf-content" },
    summary,
    card("Варианты", [filters, countNote, table.el]),
    card("OOS-кривые капитала выбранных вариантов", [chipsBox, curveHost], [logT.el]),
    card("Доходность по тестовым окнам", [barHost, barsBody,
      h("p", { class: "muted small", text: "Каждый столбец — один 3-месячный тестовый кусок; в подсказке — выбранные в этом окне параметры. Прибыльность окон — главный признак устойчивости: одно удачное окно ничего не доказывает." })]));

  function showLoading() {
    content.hidden = true;
    status.replaceChildren(h("div", { class: "wf-skel", role: "status", "aria-label": "Загрузка результатов walk-forward" },
      h("div", { class: "cards" }, ...Array.from({ length: 4 }, skeletonTile)), skeletonChart(220)));
  }
  function showEmpty(code: string, message: string) {
    content.hidden = true;
    if (code === "no_results") {
      status.replaceChildren(emptyState(`Нет результатов walk-forward для плеча ${S.lev}×`,
        `${message}. Результаты создаёт исследование research.walkforward (файлы wf_${WF_SYMBOL}*.csv и wf_windows_${WF_SYMBOL}*.csv в папке результатов); после запуска обновите страницу.`,
        levs.length > 1 ? h("div", { class: "wf-levs" }, h("span", { class: "muted small", text: "Есть результаты для плеча:" }),
          ...levs.filter((l) => l !== S.lev).map((l) => h("button", { class: "btn sm", type: "button", text: `${l}×`, onClick: () => { S.lev = l; levSeg.set(String(l)); P.save(); void load(); } }))) : null,
        { big: true, icon: "chart" }));
    }
  }

  async function load() {
    const my = ++loadSeq;
    showLoading();
    try {
      const [r, w] = await Promise.all([api.wf(S.lev, WF_SYMBOL), api.wfWindows(S.lev, WF_SYMBOL)]);
      if (my !== loadSeq) return;
      rows = r; windows = w;
      if (!rows.length) { showEmpty("no_results", "Файл результатов пуст"); return; }
      status.replaceChildren();
      content.hidden = false;
      const uniq = (f: (x: WfRow) => string) => [...new Set(rows.map(f))];
      const opt = (vals: string[]) => [{ value: "", label: "Все" }, ...vals.map((v) => ({ value: v, label: v }))];
      const tfs = uniq((x) => x.tf), sts = uniq((x) => x.strategy), vas = uniq((x) => x.variant);
      if (!tfs.includes(S.tf)) S.tf = "";
      if (!sts.includes(S.strategy)) S.strategy = "";
      if (!vas.includes(S.variant)) S.variant = "";
      tfSel.setOptions(opt(tfs), S.tf);
      stSel.setOptions(opt(sts), S.strategy);
      vaSel.setOptions(opt(vas), S.variant);
      S.selected = S.selected.filter((k) => rows.some((x) => keyOf(x) === k));
      applyFilter();
      renderSelection();
    } catch (e) {
      if (my !== loadSeq) return;
      const err = asApiError(e);
      if (err.silent) return;
      rows = []; windows = [];
      content.hidden = true;
      if (err.code === "no_results") { showEmpty(err.code, err.message); return; }
      status.replaceChildren(errorBanner(err, load));
    }
  }

  // при отсутствии результатов сводка (с переключателем плеча) скрыта вместе с содержимым — плечи перечислены в пустом состоянии
  const el = h("div", { class: "page wf" },
    pageHead("Walk-forward", "Честная проверка вне выборки: параметры подбираются на истории, затем торгуются «вслепую» на следующем отрезке."),
    how, status, content);

  let started = false;
  return { el, show() { if (!started) { started = true; load(); } }, hide() { /* нет фона */ } };
}
