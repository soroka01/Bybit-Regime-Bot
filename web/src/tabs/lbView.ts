// Кусочки интерфейса «Лучших стратегий» без состояния рейтинга: сводка по вердиктам (она же фильтр), заголовки групп, скелетоны, пояснение.
import { t } from "../i18n";
import type { Verdict } from "../types";
import { h } from "../ui/dom";
import { disclosure, emptyState } from "../ui/kit";
import { ORDER_TEXT, VERDICTS, VERDICT_ORDER } from "../ui/verdicts";
import { type VFilter, type VerdictCounts } from "./lbFilter";

/** Названия вердиктов во множественном числе — для сводки (в карточке остаётся «устойчива», «подозрение на подгонку»…). */
const VERDICT_PLURAL: Record<Verdict, string> = {
  robust: t("leaderboard.verdict.plural.robust"), overfit: t("leaderboard.verdict.plural.overfit"),
  few: t("leaderboard.verdict.plural.few"), loss: t("leaderboard.verdict.plural.loss"),
};

interface SummaryHandlers { onFilter(v: VFilter): void; onWeak(on: boolean): void; onGroup(on: boolean): void }

interface Summary {
  el: HTMLElement;
  update(counts: VerdictCounts, o: { filter: VFilter; hideWeak: boolean; group: boolean; shown: number; total: number }): void;
}

/** Сводка: четыре кнопки-фильтра с числами, «скрыть слабые», «группировать», счётчик «показано N из M». Создаётся один раз и обновляется на месте (фокус не теряется). */
export function createSummary(hd: SummaryHandlers): Summary {
  const chips = new Map<Verdict, { b: HTMLButtonElement; n: HTMLElement }>();
  const row = h("div", { class: "lb-vchips", role: "group", "aria-label": t("leaderboard.sum.filter_aria") });
  for (const v of VERDICT_ORDER) {
    const info = VERDICTS[v];
    const n = h("b", { text: "0" });
    const b = h("button", { type: "button", class: `lb-vchip ${v}`, "aria-pressed": "false", title: info.hint },
      h("span", { class: "g", "aria-hidden": "true", text: info.glyph }), h("span", { class: "lb-vchip-t", text: VERDICT_PLURAL[v] }), n);
    b.addEventListener("click", () => hd.onFilter(b.getAttribute("aria-pressed") === "true" ? "" : v));
    chips.set(v, { b, n });
    row.append(b);
  }
  const reset = h("button", { type: "button", class: "chip-btn lb-reset", text: t("leaderboard.sum.reset"), hidden: true, onClick: () => hd.onFilter("") });
  const weak = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", text: t("leaderboard.sum.weak"), title: t("leaderboard.sum.weak_tip"), onClick: () => hd.onWeak(weak.getAttribute("aria-pressed") !== "true") });
  const group = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", text: t("leaderboard.sum.group"), title: t("leaderboard.sum.group_tip"), onClick: () => hd.onGroup(group.getAttribute("aria-pressed") !== "true") });
  const count = h("span", { class: "lb-count muted small", "aria-live": "polite" });
  const el = h("section", { class: "lb-summary", "aria-label": t("leaderboard.sum.aria") }, row, h("div", { class: "lb-sum-tools" }, count, reset, weak, group));
  return {
    el,
    update(c, o) {
      for (const [v, { b, n }] of chips) {
        n.textContent = String(c[v]);
        b.setAttribute("aria-pressed", String(o.filter === v));
        b.classList.toggle("zero", c[v] === 0);
      }
      weak.setAttribute("aria-pressed", String(o.hideWeak));
      weak.disabled = !!o.filter;
      group.setAttribute("aria-pressed", String(o.group));
      reset.hidden = !o.filter;
      count.textContent = o.shown === o.total ? t("leaderboard.sum.count", { total: o.total }) : t("leaderboard.sum.count_of", { shown: o.shown, total: o.total });
    },
  };
}

export function groupHead(v: string, n: number): HTMLElement {
  const known = (VERDICT_ORDER as readonly string[]).includes(v);
  const info = known ? VERDICTS[v as Verdict] : null;
  return h("div", { class: `lb-group ${known ? v : "other"}`, role: "heading", "aria-level": 3 },
    h("span", { class: "g", "aria-hidden": "true", text: info?.glyph ?? "?" }), h("span", { text: known ? VERDICT_PLURAL[v as Verdict] : t("leaderboard.sum.other") }), h("span", { class: "lb-group-n", text: String(n) }));
}

/** Заглушки карточек на время загрузки кэша (aria-hidden; не .lb-card, чтобы не путать с реальными карточками). */
export function skeletonCards(n = 3): HTMLElement[] {
  return Array.from({ length: n }, () => h("div", { class: "lb-skel", "aria-hidden": "true" },
    h("div", { class: "skeleton skel-line w40" }), h("div", { class: "skeleton skel-line w80" }),
    h("div", { class: "lb-skel-nums" }, ...Array.from({ length: 3 }, () => h("div", { class: "skeleton lb-skel-num" }))),
  ));
}

export function emptyCache(action: HTMLElement | null): HTMLElement {
  return emptyState(t("leaderboard.empty_cache.title"), t("leaderboard.empty_cache.text"), action, { icon: "chart" });
}

/** «Как читать рейтинг»: открыт при первом визите, дальше запоминается. */
export function howToRead(): HTMLDetailsElement {
  return disclosure(t("leaderboard.how.title"), [
    h("p", {}, h("strong", { text: t("leaderboard.how.bias_h") }), t("leaderboard.how.bias")),
    h("p", {}, h("strong", { text: t("leaderboard.how.short_h") }), t("leaderboard.how.short")),
    h("p", {}, h("strong", { text: t("leaderboard.how.order_h") }), t("leaderboard.how.order", { order: ORDER_TEXT })),
    h("ul", {}, ...VERDICT_ORDER.map((v) => h("li", {}, h("strong", { text: `${VERDICTS[v].glyph} ${VERDICT_PLURAL[v]} — ` }), VERDICTS[v].hint))),
  ], { key: "best.how", defaultOpen: true, badge: t("leaderboard.how.badge") });
}
