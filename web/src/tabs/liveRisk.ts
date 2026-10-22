// Вкладка «Бот (live)»: лимиты риска (расстояние до kill-switch) и предупреждение «стоп × плечо × веса».
import { t } from "../i18n";
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { num, pct, usd } from "../ui/fmt";
import { meterBar } from "../ui/kit";
import { drawdownFrac, plToday, riskMeter, stopRiskWarning, type Pt } from "./liveCalc";

const RISK_TEXT = { ok: t("live.risk_ok"), warn: t("live.risk_warn"), bad: t("live.risk_bad") } as const;
const RISK_G = { ok: "✓", warn: "!", bad: "✕" } as const;

function riskRow(label: string, hint: string, value: number | null, limit: number | null | undefined): HTMLElement {
  const m = riskMeter(value, limit);
  if (!m || value == null || limit == null) {
    return h("div", { class: "lv-risk" }, h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }), h("span", { class: "muted small", text: t("live.no_data") })), h("div", { class: "meter", "aria-hidden": "true" }));
  }
  const vt = t("live.risk_vt", { v: pct(value, 1), l: pct(limit, 0), r: num(m.ratio * 100, 0), state: RISK_TEXT[m.tone] });
  return h("div", { class: `lv-risk ${m.tone}` },
    h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }),
      h("span", { class: `lv-risk-state ${m.tone}` }, h("span", { class: "g", "aria-hidden": "true", text: RISK_G[m.tone] }), ` ${RISK_TEXT[m.tone]}`)),
    meterBar({ label, value: m.ratio, max: 1, tone: m.tone, valueText: vt, marks: [0.6, 0.85] }),
    h("div", { class: "lv-risk-bot" },
      h("span", {}, h("b", { text: pct(value, 1) }), h("span", { class: "muted", text: ` ${t("live.of_limit", { l: pct(limit, 0) })}` })),
      h("span", { class: "muted small", text: m.left > 0 ? t("live.risk_left", { v: num(m.left * 100, 1) }) : t("live.risk_reached") })),
    h("p", { class: "muted small lv-risk-hint", text: hint }));
}

/** Доля без лишних знаков: 40 % -> «40%», 37,5 % -> «37,5%». */
const pctShort = (x: number): string => pct(x, Math.abs(x * 100 - Math.round(x * 100)) < 0.05 ? 0 : 1);

/**
 * Предупреждение «стоп × плечо × веса»: один сработавший биржевой стоп съедает долю счёта не меньше меньшего из лимитов kill-switch.
 * Нет настроек или ног — null (строки просто нет).
 */
function stopRiskNote(s: LiveSnapshot): HTMLElement | null {
  const w = stopRiskWarning(s.settings, s.legs);
  if (!w) return null;
  const cmp = w.risk > w.limit + 1e-9 ? t("live.sr_cmp_more") : t("live.sr_cmp_eq");
  return h("p", { class: "notice warn lv-warn lv-stoprisk", role: "note" },
    h("span", {}, h("strong", { text: `${t("live.sr_t")} ` }),
      t("live.sr_body", { stop: pctShort(w.stopPct), lev: num(w.leverage, 1), w: pctShort(w.weights), risk: pctShort(w.risk), cmp, label: w.limitLabel, lim: pctShort(w.limit) })));
}

export function riskCard(s: LiveSnapshot, eq: readonly Pt[], now: number): HTMLElement {
  const st = s.settings ?? {};
  const equity = s.equity ?? eq[eq.length - 1]?.v ?? null;
  const dd = drawdownFrac(equity, s.peak_equity);
  const today = plToday(eq, s.equity, now);
  const loss = today ? Math.max(0, -(today.pct ?? 0)) : null;
  const chip = (k: string, v: string, tip?: string) => h("span", { class: "chip", title: tip ?? "" }, h("span", { class: "muted", text: k }), h("b", { text: v }));
  return h("section", { class: "card lv-risks", "aria-label": t("live.risks") },
    h("header", { class: "card-head" }, h("h3", { text: t("live.risks") }), h("span", { class: "muted small", text: t("live.risks_sub") })),
    h("div", { class: "lv-risk-grid" },
      riskRow(t("live.dd_peak"), s.peak_equity != null ? t("live.dd_hint", { peak: usd(s.peak_equity) }) : t("live.dd_hint_nopeak"), dd, st.kill_dd),
      riskRow(t("live.daily_loss"), today ? t("live.daily_hint", { base: usd(today.base) }) : t("live.daily_hint_none"), loss, st.kill_daily)),
    stopRiskNote(s),
    h("div", { class: "lv-chips" },
      st.stop_pct != null ? chip(t("live.chip_stop"), pct(st.stop_pct, 0), t("live.chip_stop_tip")) : null,
      st.leverage != null ? chip(t("live.leverage"), `${num(st.leverage, 1)}×${st.max_leverage != null ? ` ${t("live.lev_of", { m: num(st.max_leverage, 0) })}` : ""}`, t("live.chip_lev_tip")) : null));
}
