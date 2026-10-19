// Диалог «Подобрать параметры»: подбор только параметров стратегии, честный отчёт IS/OOS.
import { api, asApiError } from "../api";
import type { LineChart } from "../charts/line";
import { SPOT_RULES } from "../market";
import type { Criterion, OptBody, OptResult, OptRow } from "../types";
import { errorBanner } from "../ui/banner";
import { copyWithToast, sendGuarded } from "../ui/clipboard";
import { h } from "../ui/dom";
import { date, int, num, pct, sgn, spct } from "../ui/fmt";
import { icon } from "../ui/icons";
import { badge, progressBar, skeletonLines } from "../ui/kit";
import { openModal, verdictBadge } from "../ui/modal";
import { verdictInfo } from "../ui/verdicts";
import { daysLabel } from "./labLogic";

const CRITERIA: { value: Criterion; label: string; hint: string }[] = [
  { value: "robust", label: "Устойчивый", hint: "Устойчивый: плато (Calmar + соседние значения) — ищет не острый пик, а широкую область хороших параметров" },
  { value: "calmar", label: "Calmar", hint: "Calmar: CAGR / максимальная просадка" },
  { value: "sharpe", label: "Sharpe", hint: "Sharpe: доходность на единицу волатильности" },
  { value: "return", label: "Доходность", hint: "Максимальная итоговая доходность на IS (самый склонный к подгонке критерий)" },
];

let lastCriterion: Criterion = "robust";

export interface OptimizeCtx {
  title: string;
  body(): Omit<OptBody, "criterion" | "top">;
  onApply(params: Record<string, number>): void;
  /** Текст «Копировать для бота» для строки результата; без него кнопка копирования скрыта. */
  copyText?(row: OptRow): string;
}

export function openOptimize(ctx: OptimizeCtx): void {
  let ctl: AbortController | null = null;
  let timer = 0;
  let curve: LineChart | null = null;
  const stopTimer = () => { window.clearInterval(timer); timer = 0; };
  const modal = openModal(`Подбор параметров — ${ctx.title}`, () => { ctl?.abort(); stopTimer(); curve?.destroy(); });
  const out = h("div", { class: "opt-out" });
  const b0 = ctx.body();

  // ---------- контекст: что именно подбирается
  const ctxChips = h("div", { class: "opt-ctx", "aria-label": "Контекст подбора" },
    h("span", { class: "chip", text: b0.symbol }), h("span", { class: "chip", text: b0.tf }), h("span", { class: "chip", text: daysLabel(b0.days) }),
    h("span", { class: "chip", text: b0.market === "spot" ? "Спот" : `Перп · ${+b0.leverage.toFixed(2)}×` }),
    b0.long_only && b0.market !== "spot" ? h("span", { class: "chip", text: "только лонг" }) : null,
    b0.overlays.htf ? h("span", { class: "chip", text: `HTF EMA ${b0.overlays.htf}` }) : null,
    b0.overlays.stop ? h("span", { class: "chip", text: `ATR-стоп ${+b0.overlays.stop.toFixed(1)}×` }) : null);

  // ---------- критерий отбора (чипы)
  const critBtns = new Map<Criterion, HTMLButtonElement>();
  const critBox = h("div", { class: "opt-crit", role: "group", "aria-label": "Критерий отбора (по IS)" });
  const critNote = h("p", { class: "muted small opt-crit-note", "aria-live": "polite" });
  const paintCrit = () => {
    critBtns.forEach((b, k) => b.setAttribute("aria-pressed", String(k === lastCriterion)));
    critNote.textContent = CRITERIA.find((c) => c.value === lastCriterion)!.hint;
  };
  for (const c of CRITERIA) {
    const b = h("button", { type: "button", class: "chip-btn", text: c.label, title: c.hint, onClick: () => { if (lastCriterion !== c.value) { lastCriterion = c.value; paintCrit(); run(); } } });
    critBtns.set(c.value, b);
    critBox.append(b);
  }
  paintCrit();

  const note = h("div", { class: "notice" }, h("span", null,
    h("strong", { text: "Как это устроено. " }),
    "Подбираются только параметры стратегии, остальное — как выбрано. Период делится по времени: 67 % — in-sample (IS, по нему выбираем), 33 % — out-of-sample (OOS, только проверка). " +
    (b0.market === "spot" ? SPOT_RULES : "")));

  const apply = (row: OptRow) => { ctx.onApply(row.params); modal.close(); };
  const notice = (tone: "warn" | "info", title: string, text: string) =>
    h("div", { class: `notice${tone === "warn" ? " warn" : ""}`, role: tone === "warn" ? "alert" : "status" }, h("span", null, h("strong", { text: title }), text));

  function loading() {
    const t0 = Date.now();
    const clock = h("span", { class: "muted small", text: "0 с" });
    stopTimer();
    timer = window.setInterval(() => { clock.textContent = `${Math.round((Date.now() - t0) / 1000)} с`; }, 1000);
    out.replaceChildren(
      h("div", { class: "opt-loading", role: "status" }, h("div", { class: "inline-busy" }, h("div", { class: "spinner" }), h("span", { text: "Перебираю сетку параметров…" }), clock), progressBar()),
      h("div", { class: "opt-skel", "aria-hidden": "true" }, skeletonLines(3), ...Array.from({ length: 5 }, () => h("div", { class: "skeleton skel-line", style: "height:34px;border-radius:8px" }))));
  }

  function render(r: OptResult) {
    stopTimer();
    curve?.destroy();
    curve = null;
    if (!r.results.length) {
      out.replaceChildren(h("div", { class: "banner error", role: "alert" },
        h("strong", { text: "Не из чего выбирать. " }),
        h("span", { text: `Ни одна комбинация не дала минимум ${r.min_trades} сделок на IS (${date(r.is_range.start)} — ${date(r.is_range.end)}). Увеличьте период или смените таймфрейм.` })));
      return;
    }
    const best = r.results[0]!;
    const warn = best.verdict !== "robust";
    const tilde = r.short_oos ? "~" : "";

    const stats = h("div", { class: "opt-stats" },
      stat("Перебрано", `${int(r.n_combos)} комбинаций`, `подходят по числу сделок: ${int(r.n_valid)} (минимум ${r.min_trades})`),
      stat("In-sample", `${date(r.is_range.start)} — ${date(r.is_range.end)}`, "по нему выбираем"),
      stat("Out-of-sample", `${date(r.oos_range.start)} — ${date(r.oos_range.end)}`, `проверка · ${Math.round(r.oos_days)} дн.`),
      stat("Buy & Hold на OOS", spct(r.buy_hold_oos, 0), `расчёт ${num(r.elapsed, 1)} с${r.from_cache ? " · из кэша" : ""}`));

    const notices: HTMLElement[] = [];
    if (r.short_oos) {
      notices.push(notice("warn", "Короткий проверочный период. ",
        `Проверочный период (OOS) всего ${Math.round(r.oos_days)} дн. Годовые метрики (Sharpe, Calmar, CAGR) по такому окну сильно завышены — не сравнивайте их. Для надёжной оценки выберите период 730 или 1095 дн.` +
        (r.oos_days < 90 ? " При OOS меньше 90 дн. вердикт — только «мало сделок»." : "")));
    }
    if (warn) notices.push(notice("warn", verdictInfo(best.verdict).warnTitle, verdictInfo(best.verdict).warnBody));

    const th = (t: string, cls = "") => h("th", { scope: "col", class: cls, text: t });
    const rows = r.results.map((row, i) => {
      const isBest = i === 0;
      const sendRow = ctx.copyText ? h("button", { class: "btn sm icon-btn", type: "button", "aria-label": `В бота: вариант ${i + 1}`, "data-tip": "В бота (Telegram)", "data-tip-pos": "left", onClick: () => void sendGuarded(sendRow!, () => ctx.copyText!(row)) }, icon("bot", 13)) : null;
      const acts = h("div", { class: "btn-group opt-acts" },
        h("button", { class: `btn sm${isBest ? " primary" : ""}`, type: "button", onClick: () => apply(row), "aria-label": `Применить параметры варианта ${i + 1}` }, "Применить"),
        ctx.copyText ? h("button", { class: "btn sm icon-btn", type: "button", "aria-label": `Копировать для бота: вариант ${i + 1}`, "data-tip": "Копировать для бота", "data-tip-pos": "left", onClick: () => void copyWithToast(ctx.copyText!(row)) }, icon("copy", 13)) : null,
        sendRow);
      return h("tr", { class: isBest ? "best" : "" },
        h("td", { class: "left rk" }, isBest ? h("span", { class: "best-tag", text: "лучший", title: "Лучший по выбранному критерию на IS" }) : String(i + 1)),
        h("td", { class: "left" }, ...Object.entries(row.params).map(([k, v]) => h("span", { class: "chip-param", text: `${k}=${v ?? "—"}` }))),
        h("td", { class: `is ${sgn(row.is.total_return)}`, text: spct(row.is.total_return, 0) }),
        h("td", { class: "is neg", text: pct(row.is.max_drawdown, 0) }),
        h("td", { class: "is", text: num(row.is.sharpe, 2) }),
        h("td", { class: `oos ${sgn(row.oos.total_return)} strong`, text: spct(row.oos.total_return, 0) }),
        h("td", { class: "oos neg", text: pct(row.oos.max_drawdown, 0) }),
        h("td", { class: "oos", title: r.short_oos ? `аннуализировано по ${Math.round(r.oos_days)} дн. — шумная оценка` : "", text: tilde + num(row.oos.sharpe, 2) }),
        h("td", { class: "oos", text: int(row.oos.trades) }),
        h("td", { class: "left" }, verdictBadge(row.verdict)),
        h("td", { class: "act" }, acts));
    });

    const table = h("div", { class: "table-wrap opt-table", tabindex: 0, role: "region", "aria-label": "Результаты подбора" }, h("table", { class: "data opt" },
      h("thead", null,
        h("tr", { class: "group" }, h("th", { colspan: 2, text: "" }), h("th", { colspan: 3, class: "is", text: "In-sample · по нему выбрано" }),
          h("th", { colspan: 4, class: "oos", text: "Out-of-sample · честная проверка" }), h("th", { colspan: 2, text: "" })),
        h("tr", null, th("#", "left"), th("Параметры", "left"), th("Доход", "is"), th("Просадка", "is"), th("Sharpe", "is"),
          th("Доход", "oos"), th("Просадка", "oos"), th("Sharpe", "oos"), th("Сделок", "oos"), th("Вердикт", "left"), th("", "act"))),
      h("tbody", null, ...rows)));

    const sendBest = ctx.copyText ? h("button", { class: "btn", type: "button", title: "Отправить в бота: карточка придёт в Telegram, вес и подтверждение — там", onClick: () => void sendGuarded(sendBest!, () => ctx.copyText!(best)) }, icon("bot", 14), "Лучший — в бота") : null;
    const foot = h("div", { class: "opt-foot" },
      h("p", { class: "muted small", text: "Лучшее на истории ≠ лучшее в будущем: смотрите на столбцы OOS. Если OOS сильно хуже IS — это подгонка. Даже «устойчивый» результат остаётся оценкой по короткой выборке." }),
      h("div", { class: "opt-foot-actions" },
        ctx.copyText ? h("button", { class: "btn", type: "button", onClick: () => void copyWithToast(ctx.copyText!(best)) }, icon("copy", 14), "Копировать лучший для бота") : null,
        sendBest,
        h("button", { class: "btn primary", type: "button", onClick: () => apply(best) }, icon("check", 14), "Применить лучший")));

    const curveCard = best.curve ? curveBlock() : null;
    out.replaceChildren(h("div", { class: "opt-head" }, h("div", { class: "opt-best" }, h("span", { class: "section-label", text: "лучший вариант" }), verdictBadge(best.verdict), ...Object.entries(best.params).map(([k, v]) => h("span", { class: "chip-param alt", text: `${k}=${v ?? "—"}` }))), badge(r.criterion === "robust" ? "критерий: устойчивый" : `критерий: ${CRITERIA.find((c) => c.value === r.criterion)?.label.toLowerCase() ?? r.criterion}`, "off")),
      stats, ...notices, table, ...(curveCard ? [curveCard] : []), foot);
    if (curveCard && best.curve) {
      const host = curveCard.querySelector(".chart") as HTMLElement;
      const data = best.curve;
      // динамический import: график строится уже после вставки карточки в документ (host.isConnected). Откладывать загрузку он не умеет — бандл один, без splitting
      void import("../charts/line").then(({ lineChart }) => {
        if (!host.isConnected) return;
        curve = lineChart(host, {
          height: 200, yFmt: (v) => "×" + num(v, v < 10 ? 2 : 1),
          series: [{ label: "Стратегия (OOS)", color: "--c1", width: 2 }, { label: "Buy & Hold", color: "--ref", width: 1.5, dash: [5, 4] }],
        });
        curve.setData(data.t, [data.equity, data.bh]);
      });
    }
  }

  function stat(label: string, value: string, sub: string): HTMLElement {
    return h("div", { class: "opt-stat" }, h("div", { class: "metric-label", text: label }), h("div", { class: "os-v", text: value }), h("div", { class: "metric-sub", text: sub }));
  }
  function curveBlock(): HTMLElement {
    return h("section", { class: "opt-curve" }, h("div", { class: "section-label", text: "Кривая капитала на OOS · лучший вариант" }), h("div", { class: "chart" }));
  }

  function run() {
    ctl?.abort();
    curve?.destroy();
    curve = null;
    const c = (ctl = new AbortController());
    loading();
    api.optimize({ ...ctx.body(), criterion: lastCriterion, top: 10 }, c.signal).then((r) => {
      if (c.signal.aborted) return;
      render(r);
    }).catch((e) => {
      if (c.signal.aborted) return;
      stopTimer();
      const err = asApiError(e);
      if (err.silent) return;
      out.replaceChildren(errorBanner(err, run));
    });
  }

  modal.body.append(h("div", { class: "opt-top" }, ctxChips, h("div", { class: "opt-crit-wrap" }, h("span", { class: "section-label", text: "Критерий отбора (по IS)" }), critBox, critNote)), note, out);
  run();
}
