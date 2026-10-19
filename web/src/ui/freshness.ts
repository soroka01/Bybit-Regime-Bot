// Полоса свежести локальных данных + явная кнопка докачки новых свечей (сеть — только по клику).
import { api, asApiError } from "../api";
import type { DataStatus } from "../types";
import type { DataCtx } from "../tabs/tab";
import { toast } from "./clipboard";
import { onMetaChange } from "../meta";
import { h } from "./dom";
import { icon } from "./icons";
import { int, parseTime, utcShort } from "./fmt";

const utc = (iso: string | null) => (iso ? utcShort(iso) : "—");
const dfMsk = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
const msk = (iso: string) => { const d = parseTime(iso); return d ? dfMsk.format(d) : "—"; };

function rel(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return `${m} мин назад`;
  if (m < 48 * 60) return `${Math.round(m / 60)} ч назад`;
  return `${Math.round(m / 1440)} дн назад`;
}

export const plural = (n: number, one: string, few: string, many: string) =>
  n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many;

function row(s: DataStatus): HTMLElement {
  const d = s.derivs;
  const sub = (iso: string | null) => (iso ? utcShort(iso).slice(5) : "нет");
  const main: string[] = [];
  if (!s.exists || !s.last) {
    main.push(`${s.symbol} · ${s.tf} · локальных данных нет — нажмите «Подгрузить», чтобы скачать`);
  } else {
    const closeMs = +(parseTime(s.now) ?? 0) - (+(parseTime(s.last) ?? 0) + s.step_min * 60000);
    main.push(`${s.symbol} · ${s.tf} · последняя свеча ${utc(s.last)} UTC (МСК ${msk(s.last)}) · закрыта ${rel(closeMs)} · свечей ${int(s.bars)} · с ${utc(s.first).slice(0, 10)}`);
    if ((s.behind_bars ?? 0) > 1) main.push(`отстаёт на ${s.behind_bars} св.`);
  }
  return h("div", { class: "fresh-row" },
    h("i", { class: `dot ${s.exists ? s.level : "bad"}`, "aria-hidden": "true" }),
    h("span", { text: main.join(" · "), title: `Ожидаемая последняя закрытая свеча: ${utc(s.expected_last)} UTC` }),
    h("span", { class: "fresh-sub", text: `деривативы (${d.granularity}): фандинг ${sub(d.funding)} · OI ${sub(d.oi)} · L/S ${sub(d.lsr)} UTC` }));
}

export function createFreshness(host: HTMLElement, getCtx: () => DataCtx | null, onUpdated: () => Promise<void> | void) {
  const rows = h("div", { class: "fresh-rows" });
  const btn = h("button", { class: "btn", type: "button", title: "Скачать только новые свечи и деривативы с Bybit (публичный API)" });
  const idle = () => { btn.replaceChildren(icon("refresh", 14), "Подгрузить новые свечи"); btn.disabled = false; };
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
    rows.replaceChildren(...(kids.length ? kids : [h("span", { class: "muted", text: "Статус данных недоступен" })]));
  }

  btn.addEventListener("click", async () => {
    const ctx = getCtx();
    if (!ctx || busy) return;
    busy = true;
    errorText = "";
    btn.disabled = true;
    btn.replaceChildren(h("span", { class: "spinner" }), "Загрузка…");
    let added = 0;
    let last: string | null = null;
    try {
      for (const tf of ctx.tfs) {
        const r = await api.dataUpdate(ctx.symbol, tf);
        added += r.added;
        last = r.status.last;
        if (r.warning) errorText = r.warning;
      }
      toast(added ? `Добавлено ${added} ${plural(added, "свеча", "свечи", "свечей")}, последняя ${utc(last)} UTC` : `Новых свечей нет, последняя ${utc(last)} UTC`, "ok");
      await onUpdated();
    } catch (e) {
      errorText = asApiError(e).message;
      toast("Не удалось обновить данные", "error");
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
