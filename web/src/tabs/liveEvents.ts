// Вкладка «Бот (live)»: лента событий и заглушка «бот ещё не запускался».
import { h } from "../ui/dom";
import { ago, datetime, parseTime } from "../ui/fmt";
import { icon } from "../ui/icons";
import { emptyState } from "../ui/kit";
import { eventKind, filterEvents, isNoiseEvent, type EventFilter, type LiveEvent } from "./liveCalc";

const EV_ICON: Record<string, string> = { info: "info", trade: "swap", signal: "pulse", warn: "warn", error: "error", daily: "chart" };
const EV_WORD: Record<string, string> = { info: "Инфо", trade: "Сделка", signal: "Сигнал", warn: "Внимание", error: "Ошибка", daily: "Итоги дня" };

/** Сколько событий показывает лента (остальные есть в снимке, но не отрисовываются). */
export const EVENTS_LIMIT = 60;

/** Подпись счётчика над лентой: честно говорит, когда показана только часть («последние 60 из 100»), а не «100» при 60 строках. */
export function eventsCountText(events: readonly LiveEvent[] | undefined, mode: EventFilter, limit = EVENTS_LIMIT): string {
  const total = (events ?? []).length;
  if (!total) return "";
  const matching = filterEvents(events, mode).length;
  const shown = Math.min(matching, limit);
  if (mode === "all") return matching > limit ? `последние ${shown} из ${total}` : `${total}`;
  return matching > limit ? `последние ${shown} из ${matching} (всего ${total})` : `${matching} из ${total}`;
}

export function eventsList(events: readonly LiveEvent[] | undefined, mode: EventFilter, limit = EVENTS_LIMIT): HTMLElement {
  const list = filterEvents(events, mode);
  if (!list.length) return emptyState(mode === "all" ? "Событий нет" : "В этой категории событий нет", undefined, null);
  return h("ol", { class: "lv-timeline", tabindex: 0, "aria-label": "События бота" }, ...list.slice(0, limit).map((e) => {
    const k = eventKind(e.level);
    const noise = isNoiseEvent(e.text);
    return h("li", { class: `lv-ev ${k}${noise ? " noise" : ""}` },
      h("span", { class: "lv-ev-ic", title: EV_WORD[k] }, icon(EV_ICON[k] ?? "info", 14), h("span", { class: "sr-only", text: EV_WORD[k] })),
      h("div", { class: "lv-ev-body" },
        h("div", { class: "lv-ev-meta" }, h("span", { class: "lv-ev-kind", text: EV_WORD[k] }), h("time", { dateTime: parseTime(e.ts)?.toISOString() ?? "", title: datetime(e.ts), text: `${ago(e.ts)} · ${datetime(e.ts)}` })),
        h("div", { class: "lv-ev-text", text: e.text })));
  }));
}

export function noSnapshot(reason?: string): HTMLElement {
  return h("div", { class: "lv-empty" },
    emptyState("Бот ещё не запускался", "Снимок состояния недоступен. Бот записывает своё состояние в файл storage/state/bot.db, а эта вкладка только читает его — данные появятся здесь сами в течение 10 секунд после запуска (python -m tradebot).", null, { big: true, icon: "power" }),
    reason ? h("p", { class: "muted small lv-reason-line" }, "Код причины: ", h("code", { text: reason })) : null);
}
