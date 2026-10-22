// Диалог «Подобрать параметры»: подбор только параметров стратегии, честный отчёт IS/OOS.
import { api, asApiError } from "../api";
import type { LineChart } from "../charts/line";
import { t } from "../i18n";
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
  { value: "robust", label: t("lab.opt.crit.robust"), hint: t("lab.opt.crit.robust.hint") },
  { value: "calmar", label: "Calmar", hint: t("lab.opt.crit.calmar.hint") },
  { value: "sharpe", label: "Sharpe", hint: t("lab.opt.crit.sharpe.hint") },
  { value: "return", label: t("lab.opt.crit.return"), hint: t("lab.opt.crit.return.hint") },
];

let lastCriterion: Criterion = "robust";

interface OptimizeCtx {
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
  const modal = openModal(t("lab.opt.title", { title: ctx.title }), () => { ctl?.abort(); stopTimer(); curve?.destroy(); });
  const out = h("div", { class: "opt-out" });
  const b0 = ctx.body();

  // ---------- контекст: что именно подбирается
  const ctxChips = h("div", { class: "opt-ctx", "aria-label": t("lab.opt.ctxLabel") },
    h("span", { class: "chip", text: b0.symbol }), h("span", { class: "chip", text: b0.tf }), h("span", { class: "chip", text: daysLabel(b0.days) }),
    h("span", { class: "chip", text: b0.market === "spot" ? t("lab.opt.chipSpot") : t("lab.opt.chipPerp", { lev: +b0.leverage.toFixed(2) }) }),
    b0.long_only && b0.market !== "spot" ? h("span", { class: "chip", text: t("lab.opt.chipLongOnly") }) : null,
    b0.overlays.htf ? h("span", { class: "chip", text: `HTF EMA ${b0.overlays.htf}` }) : null,
    b0.overlays.stop ? h("span", { class: "chip", text: t("lab.atrStop", { k: +b0.overlays.stop.toFixed(1) }) }) : null);

  // ---------- критерий отбора (чипы)
  const critBtns = new Map<Criterion, HTMLButtonElement>();
  const critBox = h("div", { class: "opt-crit", role: "group", "aria-label": t("lab.opt.criterionLabel") });
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
    h("strong", { text: `${t("lab.opt.howTitle")} ` }),
    `${t("lab.opt.howBody")} ` +
    (b0.market === "spot" ? SPOT_RULES : "")));

  const apply = (row: OptRow) => { ctx.onApply(row.params); modal.close(); };
  const notice = (tone: "warn" | "info", title: string, text: string) =>
    h("div", { class: `notice${tone === "warn" ? " warn" : ""}`, role: tone === "warn" ? "alert" : "status" }, h("span", null, h("strong", { text: title }), text));

  function loading() {
    const t0 = Date.now();
    const clock = h("span", { class: "muted small", text: `0 ${t("time.s")}` });
    stopTimer();
    timer = window.setInterval(() => { clock.textContent = `${Math.round((Date.now() - t0) / 1000)} ${t("time.s")}`; }, 1000);
    out.replaceChildren(
      h("div", { class: "opt-loading", role: "status" }, h("div", { class: "inline-busy" }, h("div", { class: "spinner" }), h("span", { text: t("lab.opt.loading") }), clock), progressBar()),
      h("div", { class: "opt-skel", "aria-hidden": "true" }, skeletonLines(3), ...Array.from({ length: 5 }, () => h("div", { class: "skeleton skel-line", style: "height:34px;border-radius:8px" }))));
  }

  function render(r: OptResult) {
    stopTimer();
    curve?.destroy();
    curve = null;
    if (!r.results.length) {
      out.replaceChildren(h("div", { class: "banner error", role: "alert" },
        h("strong", { text: `${t("lab.opt.emptyTitle")} ` }),
        h("span", { text: t("lab.opt.emptyBody", { min: r.min_trades, start: date(r.is_range.start), end: date(r.is_range.end) }) })));
      return;
    }
    const best = r.results[0]!;
    const warn = best.verdict !== "robust";
    const tilde = r.short_oos ? "~" : "";
    const oosDays = t("lab.days.n", { n: Math.round(r.oos_days) });

    const stats = h("div", { class: "opt-stats" },
      stat(t("lab.opt.stat.tested"), t("lab.opt.stat.combos", { n: r.n_combos, count: int(r.n_combos) }), t("lab.opt.stat.valid", { valid: int(r.n_valid), min: r.min_trades })),
      stat("In-sample", `${date(r.is_range.start)} — ${date(r.is_range.end)}`, t("lab.opt.stat.isNote")),
      stat("Out-of-sample", `${date(r.oos_range.start)} — ${date(r.oos_range.end)}`, t("lab.opt.stat.oosNote", { days: oosDays })),
      stat(t("lab.opt.stat.bhOos"), spct(r.buy_hold_oos, 0), t("lab.opt.stat.elapsed", { time: `${num(r.elapsed, 1)} ${t("time.s")}` }) + (r.from_cache ? ` · ${t("lab.opt.stat.cached")}` : "")));

    const notices: HTMLElement[] = [];
    if (r.short_oos) {
      notices.push(notice("warn", `${t("lab.opt.shortOosTitle")} `,
        t("lab.opt.shortOosBody", { days: oosDays }) +
        (r.oos_days < 90 ? ` ${t("lab.opt.shortOosFew", { days: 90 })}` : "")));
    }
    if (warn) notices.push(notice("warn", verdictInfo(best.verdict).warnTitle, verdictInfo(best.verdict).warnBody));

    const th = (t: string, cls = "") => h("th", { scope: "col", class: cls, text: t });
    const rows = r.results.map((row, i) => {
      const isBest = i === 0;
      const sendRow = ctx.copyText ? h("button", { class: "btn sm icon-btn", type: "button", "aria-label": t("lab.opt.sendVariant", { n: i + 1 }), "data-tip": t("lab.opt.sendTip"), "data-tip-pos": "left", onClick: () => void sendGuarded(sendRow!, () => ctx.copyText!(row)) }, icon("bot", 13)) : null;
      const acts = h("div", { class: "btn-group opt-acts" },
        h("button", { class: `btn sm${isBest ? " primary" : ""}`, type: "button", onClick: () => apply(row), "aria-label": t("lab.opt.applyVariant", { n: i + 1 }) }, t("lab.opt.apply")),
        ctx.copyText ? h("button", { class: "btn sm icon-btn", type: "button", "aria-label": t("lab.opt.copyVariant", { n: i + 1 }), "data-tip": t("lab.opt.copyTip"), "data-tip-pos": "left", onClick: () => void copyWithToast(ctx.copyText!(row)) }, icon("copy", 13)) : null,
        sendRow);
      return h("tr", { class: isBest ? "best" : "" },
        h("td", { class: "left rk" }, isBest ? h("span", { class: "best-tag", text: t("lab.opt.bestTag"), title: t("lab.opt.bestTip") }) : String(i + 1)),
        h("td", { class: "left" }, ...Object.entries(row.params).map(([k, v]) => h("span", { class: "chip-param", text: `${k}=${v ?? "—"}` }))),
        h("td", { class: `is ${sgn(row.is.total_return)}`, text: spct(row.is.total_return, 0) }),
        h("td", { class: "is neg", text: pct(row.is.max_drawdown, 0) }),
        h("td", { class: "is", text: num(row.is.sharpe, 2) }),
        h("td", { class: `oos ${sgn(row.oos.total_return)} strong`, text: spct(row.oos.total_return, 0) }),
        h("td", { class: "oos neg", text: pct(row.oos.max_drawdown, 0) }),
        h("td", { class: "oos", title: r.short_oos ? t("lab.opt.annualNoisy", { days: oosDays }) : "", text: tilde + num(row.oos.sharpe, 2) }),
        h("td", { class: "oos", text: int(row.oos.trades) }),
        h("td", { class: "left" }, verdictBadge(row.verdict)),
        h("td", { class: "act" }, acts));
    });

    const table = h("div", { class: "table-wrap opt-table", tabindex: 0, role: "region", "aria-label": t("lab.opt.tableLabel") }, h("table", { class: "data opt" },
      h("thead", null,
        h("tr", { class: "group" }, h("th", { colspan: 2, text: "" }), h("th", { colspan: 3, class: "is", text: t("lab.opt.group.is") }),
          h("th", { colspan: 4, class: "oos", text: t("lab.opt.group.oos") }), h("th", { colspan: 2, text: "" })),
        h("tr", null, th("#", "left"), th(t("lab.opt.col.params"), "left"), th(t("lab.opt.col.return"), "is"), th(t("lab.opt.col.dd"), "is"), th("Sharpe", "is"),
          th(t("lab.opt.col.return"), "oos"), th(t("lab.opt.col.dd"), "oos"), th("Sharpe", "oos"), th(t("lab.opt.col.trades"), "oos"), th(t("lab.opt.col.verdict"), "left"), th("", "act"))),
      h("tbody", null, ...rows)));

    const sendBest = ctx.copyText ? h("button", { class: "btn", type: "button", title: t("lab.opt.sendBestTip"), onClick: () => void sendGuarded(sendBest!, () => ctx.copyText!(best)) }, icon("bot", 14), t("lab.opt.sendBest")) : null;
    const foot = h("div", { class: "opt-foot" },
      h("p", { class: "muted small", text: t("lab.opt.foot") }),
      h("div", { class: "opt-foot-actions" },
        ctx.copyText ? h("button", { class: "btn", type: "button", onClick: () => void copyWithToast(ctx.copyText!(best)) }, icon("copy", 14), t("lab.opt.copyBest")) : null,
        sendBest,
        h("button", { class: "btn primary", type: "button", onClick: () => apply(best) }, icon("check", 14), t("lab.opt.applyBest"))));

    const curveCard = best.curve ? curveBlock() : null;
    out.replaceChildren(h("div", { class: "opt-head" }, h("div", { class: "opt-best" }, h("span", { class: "section-label", text: t("lab.opt.bestLabel") }), verdictBadge(best.verdict), ...Object.entries(best.params).map(([k, v]) => h("span", { class: "chip-param alt", text: `${k}=${v ?? "—"}` }))), badge(t("lab.opt.criterion", { name: CRITERIA.find((c) => c.value === r.criterion)?.label.toLowerCase() ?? r.criterion }), "off")),
      stats, ...notices, table, ...(curveCard ? [curveCard] : []), foot);
    if (curveCard && best.curve) {
      const host = curveCard.querySelector(".chart") as HTMLElement;
      const data = best.curve;
      // динамический import: график строится уже после вставки карточки в документ (host.isConnected). Откладывать загрузку он не умеет — бандл один, без splitting
      void import("../charts/line").then(({ lineChart }) => {
        if (!host.isConnected) return;
        curve = lineChart(host, {
          height: 200, yFmt: (v) => "×" + num(v, v < 10 ? 2 : 1),
          series: [{ label: t("lab.opt.curveSeries"), color: "--c1", width: 2 }, { label: "Buy & Hold", color: "--ref", width: 1.5, dash: [5, 4] }],
        });
        curve.setData(data.t, [data.equity, data.bh]);
      });
    }
  }

  function stat(label: string, value: string, sub: string): HTMLElement {
    return h("div", { class: "opt-stat" }, h("div", { class: "metric-label", text: label }), h("div", { class: "os-v", text: value }), h("div", { class: "metric-sub", text: sub }));
  }
  function curveBlock(): HTMLElement {
    return h("section", { class: "opt-curve" }, h("div", { class: "section-label", text: t("lab.opt.curveTitle") }), h("div", { class: "chart" }));
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

  modal.body.append(h("div", { class: "opt-top" }, ctxChips, h("div", { class: "opt-crit-wrap" }, h("span", { class: "section-label", text: t("lab.opt.criterionLabel") }), critBox, critNote)), note, out);
  run();
}
