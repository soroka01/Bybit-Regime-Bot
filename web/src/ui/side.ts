// Сторона позиции (лонг/шорт) и направление ордера (покупка/продажа) — ТОЛЬКО словами и нейтральной рамкой.
// Правило цвета (см. DESIGN.md §1a): --pos/--neg (и ▲/▼ закрашенные) означают прибыль/убыток и больше ничего.
// Сторона — слово + контурный значок △/▽ цвета текста, без --pos/--neg: красный/оранжевый знак у «продажи» больше не читается как «шорт».
// Тесты: tests/side.test.ts.
import { h } from "./dom";

type Side = "long" | "short";

export const SIDE_WORD: Record<Side, string> = { long: "ЛОНГ", short: "ШОРТ" };
/** Контурные треугольники: отличаются от закрашенных ▲/▼ результата (прибыль/убыток). */
export const SIDE_GLYPH: Record<Side, string> = { long: "△", short: "▽" };

/** Бейдж стороны: `span.side-badge[.long|.short]` — рамка и текст нейтральны (никаких pos/neg). */
export function sideBadge(side: Side, extra = ""): HTMLElement {
  return h("span", { class: `side-badge ${side}${extra ? " " + extra : ""}`, "data-side": side, title: side === "long" ? "Лонг: позиция на рост цены" : "Шорт: позиция на падение цены" },
    h("span", { class: "sb-g", "aria-hidden": "true", text: SIDE_GLYPH[side] }), SIDE_WORD[side]);
}

/** Направление ордера (fill): покупка/продажа — это не лонг/шорт и не прибыль/убыток. */
export function orderBadge(buy: boolean): HTMLElement {
  return h("span", { class: `side-badge order ${buy ? "buy" : "sell"}`, "data-order": buy ? "buy" : "sell", title: buy ? "Покупка (ордер buy)" : "Продажа (ордер sell)" },
    h("span", { class: "sb-g", "aria-hidden": "true", text: buy ? "△" : "▽" }), buy ? "Покупка" : "Продажа");
}
