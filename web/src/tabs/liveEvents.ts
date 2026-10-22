// Вкладка «Бот (live)»: лента событий и заглушка «бот ещё не запускался».
import { t } from "../i18n";
import { h } from "../ui/dom";
import { datetime, parseTime } from "../ui/fmt";
import { icon } from "../ui/icons";
import { emptyState } from "../ui/kit";
import { tk } from "./liveTick";
import { eventKind, filterEvents, isNoiseEvent, type EventFilter, type LiveEvent } from "./liveCalc";

const EV_ICON: Record<string, string> = { info: "info", trade: "swap", signal: "pulse", warn: "warn", error: "error", daily: "chart" };
const EV_WORD: Record<string, string> = { info: t("live.ev_info"), trade: t("live.ev_trade"), signal: t("live.ev_signal"), warn: t("live.ev_warn"), error: t("live.ev_error"), daily: t("live.ev_daily") };

/** Сколько событий показывает лента (остальные есть в снимке, но не отрисовываются). */
export const EVENTS_LIMIT = 60;

/** Подпись счётчика над лентой: честно говорит, когда показана только часть («последние 60 из 100»), а не «100» при 60 строках. */
export function eventsCountText(events: readonly LiveEvent[] | undefined, mode: EventFilter, limit = EVENTS_LIMIT): string {
  const total = (events ?? []).length;
  if (!total) return "";
  const matching = filterEvents(events, mode).length;
  const shown = Math.min(matching, limit);
  if (mode === "all") return matching > limit ? t("live.ev_count_last", { shown, total }) : `${total}`;
  return matching > limit ? t("live.ev_count_last_total", { shown, matching, total }) : t("live.ev_count_of", { matching, total });
}

export function eventsList(events: readonly LiveEvent[] | undefined, mode: EventFilter, limit = EVENTS_LIMIT): HTMLElement {
  const list = filterEvents(events, mode);
  if (!list.length) return emptyState(mode === "all" ? t("live.ev_none") : t("live.ev_none_cat"), undefined, null);
  return h("ol", { class: "lv-timeline", tabindex: 0, "aria-label": t("live.ev_list") }, ...list.slice(0, limit).map((e) => {
    const k = eventKind(e.level);
    const noise = isNoiseEvent(e.text);
    return h("li", { class: `lv-ev ${k}${noise ? " noise" : ""}` },
      h("span", { class: "lv-ev-ic", title: EV_WORD[k] }, icon(EV_ICON[k] ?? "info", 14), h("span", { class: "sr-only", text: EV_WORD[k] })),
      h("div", { class: "lv-ev-body" },
        h("div", { class: "lv-ev-meta" }, h("span", { class: "lv-ev-kind", text: EV_WORD[k] }), h("time", { dateTime: parseTime(e.ts)?.toISOString() ?? "", title: datetime(e.ts) }, tk("age", e.ts, h("span")), ` · ${datetime(e.ts)}`)),
        h("div", { class: "lv-ev-text", text: e.text })));
  }));
}

export function noSnapshot(reason?: string): HTMLElement {
  return h("div", { class: "lv-empty" },
    emptyState(t("live.nosnap_t"), t("live.nosnap_h"), null, { big: true, icon: "power" }),
    reason ? h("p", { class: "muted small lv-reason-line" }, t("live.reason_code"), h("code", { text: reason })) : null);
}
