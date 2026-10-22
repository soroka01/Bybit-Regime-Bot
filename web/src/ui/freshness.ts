// Полоса свежести локальных данных + явная кнопка докачки новых свечей (сеть — только по клику).
import { api, asApiError } from "../api";
import { localeTag, t } from "../i18n";
import type { DataStatus } from "../types";
import { toast } from "./clipboard";
import { onMetaChange } from "../meta";
import { h } from "./dom";
import { icon } from "./icons";
import { int, parseTime, utcShort } from "./fmt";

/** Какие данные использует вкладка (для полосы свежести). */
export interface DataCtx { symbol: string; tfs: string[] }

const utc = (iso: string | null) => (iso ? utcShort(iso) : "—");
// московское время — подсказка к UTC (в en — «MSK»); 24-часовой формат в обоих языках
const dfMsk = new Intl.DateTimeFormat(localeTag(), { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const msk = (iso: string) => { const d = parseTime(iso); return d ? dfMsk.format(d) : "—"; };

function rel(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return t("time.ago_min", { n: m });
  if (m < 48 * 60) return t("time.ago_h", { n: Math.round(m / 60) });
  return t("time.ago_d", { n: Math.round(m / 1440) });
}

function row(s: DataStatus): HTMLElement {
  const d = s.derivs;
  const sub = (iso: string | null) => (iso ? utcShort(iso).slice(5) : t("common.fresh.none"));
  const main: string[] = [];
  if (!s.exists || !s.last) {
    main.push(t("common.fresh.no_data", { symbol: s.symbol, tf: s.tf }));
  } else {
    const closeMs = +(parseTime(s.now) ?? 0) - (+(parseTime(s.last) ?? 0) + s.step_min * 60000);
    main.push(t("common.fresh.last", { symbol: s.symbol, tf: s.tf, last: utc(s.last), msk: msk(s.last), closed: rel(closeMs), bars: int(s.bars), first: utc(s.first).slice(0, 10) }));
    if ((s.behind_bars ?? 0) > 1) main.push(t("common.fresh.behind", { n: s.behind_bars! }));
  }
  return h("div", { class: "fresh-row" },
    h("i", { class: `dot ${s.exists ? s.level : "bad"}`, "aria-hidden": "true" }),
    h("span", { text: main.join(" · "), title: t("common.fresh.expected", { last: utc(s.expected_last) }) }),
    h("span", { class: "fresh-sub", text: t("common.fresh.derivs", { gran: d.granularity, funding: sub(d.funding), oi: sub(d.oi), lsr: sub(d.lsr) }) }));
}

export function createFreshness(host: HTMLElement, getCtx: () => DataCtx | null, onUpdated: () => Promise<void> | void) {
  const rows = h("div", { class: "fresh-rows" });
  const btn = h("button", { class: "btn", type: "button", title: t("common.fresh.fetch_tip") });
  const idle = () => { btn.replaceChildren(icon("refresh", 14), t("common.fresh.fetch")); btn.disabled = false; };
  idle();
  host.append(rows, btn);
  let busy = false;
  let seq = 0;
  let errorText = "";

  async function refresh() {
    const ctx = getCtx();
    host.hidden = !ctx;
    if (!ctx) return;
    const my = ++seq;
    const sts = await Promise.all(ctx.tfs.map((tf) => api.dataStatus(ctx.symbol, tf).catch(() => null)));
    if (my !== seq) return;
    const kids: HTMLElement[] = sts.filter((s): s is DataStatus => !!s).map(row);
    if (errorText) kids.push(h("div", { class: "fresh-err", role: "alert", text: errorText }));
    rows.replaceChildren(...(kids.length ? kids : [h("span", { class: "muted", text: t("common.fresh.unavailable") })]));
  }

  btn.addEventListener("click", async () => {
    const ctx = getCtx();
    if (!ctx || busy) return;
    busy = true;
    errorText = "";
    btn.disabled = true;
    btn.replaceChildren(h("span", { class: "spinner" }), t("app.loading"));
    let added = 0;
    let last: string | null = null;
    try {
      for (const tf of ctx.tfs) {
        const r = await api.dataUpdate(ctx.symbol, tf);
        added += r.added;
        last = r.status.last;
        if (r.warning) errorText = r.warning;
      }
      toast(added ? t("common.fresh.added", { n: added, last: utc(last) }) : t("common.fresh.no_new", { last: utc(last) }), "ok");
      await onUpdated();
    } catch (e) {
      errorText = asApiError(e).message;
      toast(t("common.fresh.update_failed"), "error");
    } finally {
      busy = false;
      idle();
      void refresh();
    }
  });

  window.addEventListener("tb-context", () => void refresh());
  onMetaChange(() => void refresh());
  window.setInterval(() => { if (!document.hidden && !busy) void refresh(); }, 60_000);
  return { refresh };
}
