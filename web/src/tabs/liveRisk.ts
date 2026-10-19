// Вкладка «Бот (live)»: лимиты риска (расстояние до kill-switch) и предупреждение «стоп × плечо × веса».
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { num, pct, usd } from "../ui/fmt";
import { meterBar } from "../ui/kit";
import { drawdownFrac, plToday, riskMeter, stopRiskWarning, type Pt } from "./liveCalc";

const RISK_TEXT = { ok: "в норме", warn: "близко к лимиту", bad: "критично" } as const;
const RISK_G = { ok: "✓", warn: "!", bad: "✕" } as const;

function riskRow(label: string, hint: string, value: number | null, limit: number | null | undefined): HTMLElement {
  const m = riskMeter(value, limit);
  if (!m || value == null || limit == null) {
    return h("div", { class: "lv-risk" }, h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }), h("span", { class: "muted small", text: "нет данных" })), h("div", { class: "meter", "aria-hidden": "true" }));
  }
  const vt = `${pct(value, 1)} из ${pct(limit, 0)} (${num(m.ratio * 100, 0)}% лимита) — ${RISK_TEXT[m.tone]}`;
  return h("div", { class: `lv-risk ${m.tone}` },
    h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }),
      h("span", { class: `lv-risk-state ${m.tone}` }, h("span", { class: "g", "aria-hidden": "true", text: RISK_G[m.tone] }), ` ${RISK_TEXT[m.tone]}`)),
    meterBar({ label, value: m.ratio, max: 1, tone: m.tone, valueText: vt, marks: [0.6, 0.85] }),
    h("div", { class: "lv-risk-bot" },
      h("span", {}, h("b", { text: pct(value, 1) }), h("span", { class: "muted", text: ` из ${pct(limit, 0)}` })),
      h("span", { class: "muted small", text: m.left > 0 ? `запас до kill-switch ${num(m.left * 100, 1)} п.п.` : "лимит достигнут" })),
    h("p", { class: "muted small lv-risk-hint", text: hint }));
}

/** Доля без лишних знаков: 40 % -> «40%», 37,5 % -> «37,5%». */
const pctShort = (x: number): string => pct(x, Math.abs(x * 100 - Math.round(x * 100)) < 0.05 ? 0 : 1);

/**
 * Предупреждение «стоп × плечо × веса»: один сработавший биржевой стоп съедает долю счёта не меньше меньшего из лимитов kill-switch.
 * Нет настроек или ног — null (строки просто нет).
 */
export function stopRiskNote(s: LiveSnapshot): HTMLElement | null {
  const w = stopRiskWarning(s.settings, s.legs);
  if (!w) return null;
  const cmp = w.risk > w.limit + 1e-9 ? "больше лимита" : "равно лимиту";
  return h("p", { class: "notice warn lv-warn lv-stoprisk", role: "note" },
    h("span", {}, h("strong", { text: "Стоп сильнее лимита риска. " }),
      `Стоп ${pctShort(w.stopPct)} при плече ${num(w.leverage, 1)}× и весах ${pctShort(w.weights)} ≈ ${pctShort(w.risk)} счёта за одну сделку — ${cmp} (${w.limitLabel} ${pctShort(w.limit)}): сначала сработает биржевой стоп, затем kill-switch.`));
}

export function riskCard(s: LiveSnapshot, eq: readonly Pt[], now: number): HTMLElement {
  const st = s.settings ?? {};
  const equity = s.equity ?? eq[eq.length - 1]?.v ?? null;
  const dd = drawdownFrac(equity, s.peak_equity);
  const today = plToday(eq, s.equity, now);
  const loss = today ? Math.max(0, -(today.pct ?? 0)) : null;
  const chip = (k: string, v: string, tip?: string) => h("span", { class: "chip", title: tip ?? "" }, h("span", { class: "muted", text: k }), h("b", { text: v }));
  return h("section", { class: "card lv-risks", "aria-label": "Лимиты риска" },
    h("header", { class: "card-head" }, h("h3", { text: "Лимиты риска" }), h("span", { class: "muted small", text: "расстояние до kill-switch" })),
    h("div", { class: "lv-risk-grid" },
      riskRow("Просадка от пика", s.peak_equity != null ? `Считается от пика кошелька (${usd(s.peak_equity)}); срабатывает при просадке не меньше лимита.` : "Считается от пика кошелька; срабатывает при просадке не меньше лимита.", dd, st.kill_dd),
      riskRow("Убыток за день (UTC)", today ? `Считается от кошелька на начало суток UTC (${usd(today.base)}).` : "За текущие сутки UTC ещё нет точек кошелька.", loss, st.kill_daily)),
    stopRiskNote(s),
    h("div", { class: "lv-chips" },
      st.stop_pct != null ? chip("Стоп", pct(st.stop_pct, 0), "Биржевой стоп от цены входа") : null,
      st.leverage != null ? chip("Плечо", `${num(st.leverage, 1)}×${st.max_leverage != null ? ` из ${num(st.max_leverage, 0)}×` : ""}`, "Текущее плечо и потолок бота") : null));
}
