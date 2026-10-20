// Кусочки интерфейса «Лучших стратегий» без состояния рейтинга: сводка по вердиктам (она же фильтр), заголовки групп, скелетоны, пояснение.
import type { Verdict } from "../types";
import { h } from "../ui/dom";
import { disclosure, emptyState } from "../ui/kit";
import { ORDER_TEXT, VERDICTS, VERDICT_ORDER } from "../ui/verdicts";
import { type VFilter, type VerdictCounts } from "./lbFilter";

/** Названия вердиктов во множественном числе — для сводки (в карточке остаётся «устойчива», «подозрение на подгонку»…). */
const VERDICT_PLURAL: Record<Verdict, string> = { robust: "устойчивые", overfit: "подгонка", few: "мало сделок", loss: "убыточные" };

interface SummaryHandlers { onFilter(v: VFilter): void; onWeak(on: boolean): void; onGroup(on: boolean): void }

interface Summary {
  el: HTMLElement;
  update(counts: VerdictCounts, o: { filter: VFilter; hideWeak: boolean; group: boolean; shown: number; total: number }): void;
}

/** Сводка: четыре кнопки-фильтра с числами, «скрыть слабые», «группировать», счётчик «показано N из M». Создаётся один раз и обновляется на месте (фокус не теряется). */
export function createSummary(hd: SummaryHandlers): Summary {
  const chips = new Map<Verdict, { b: HTMLButtonElement; n: HTMLElement }>();
  const row = h("div", { class: "lb-vchips", role: "group", "aria-label": "Фильтр по вердикту" });
  for (const v of VERDICT_ORDER) {
    const info = VERDICTS[v];
    const n = h("b", { text: "0" });
    const b = h("button", { type: "button", class: `lb-vchip ${v}`, "aria-pressed": "false", title: info.hint },
      h("span", { class: "g", "aria-hidden": "true", text: info.glyph }), h("span", { class: "lb-vchip-t", text: VERDICT_PLURAL[v] }), n);
    b.addEventListener("click", () => hd.onFilter(b.getAttribute("aria-pressed") === "true" ? "" : v));
    chips.set(v, { b, n });
    row.append(b);
  }
  const reset = h("button", { type: "button", class: "chip-btn lb-reset", text: "Сбросить фильтр", hidden: true, onClick: () => hd.onFilter("") });
  const weak = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", text: "Скрыть слабые", title: "Скрыть «мало сделок» и «убыточные»", onClick: () => hd.onWeak(weak.getAttribute("aria-pressed") !== "true") });
  const group = h("button", { type: "button", class: "chip-btn", "aria-pressed": "false", text: "По вердиктам", title: "Разбить список на группы по вердикту", onClick: () => hd.onGroup(group.getAttribute("aria-pressed") !== "true") });
  const count = h("span", { class: "lb-count muted small", "aria-live": "polite" });
  const el = h("section", { class: "lb-summary", "aria-label": "Сводка рейтинга" }, row, h("div", { class: "lb-sum-tools" }, count, reset, weak, group));
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
      count.textContent = o.shown === o.total ? `Показано ${o.total}` : `Показано ${o.shown} из ${o.total}`;
    },
  };
}

export function groupHead(v: string, n: number): HTMLElement {
  const known = (VERDICT_ORDER as readonly string[]).includes(v);
  const info = known ? VERDICTS[v as Verdict] : null;
  return h("div", { class: `lb-group ${known ? v : "other"}`, role: "heading", "aria-level": 3 },
    h("span", { class: "g", "aria-hidden": "true", text: info?.glyph ?? "?" }), h("span", { text: known ? VERDICT_PLURAL[v as Verdict] : "прочее" }), h("span", { class: "lb-group-n", text: String(n) }));
}

/** Заглушки карточек на время загрузки кэша (aria-hidden; не .lb-card, чтобы не путать с реальными карточками). */
export function skeletonCards(n = 3): HTMLElement[] {
  return Array.from({ length: n }, () => h("div", { class: "lb-skel", "aria-hidden": "true" },
    h("div", { class: "skeleton skel-line w40" }), h("div", { class: "skeleton skel-line w80" }),
    h("div", { class: "lb-skel-nums" }, ...Array.from({ length: 3 }, () => h("div", { class: "skeleton lb-skel-num" }))),
  ));
}

export function emptyCache(action: HTMLElement | null): HTMLElement {
  return emptyState("В кэше пока нет результатов для этих настроек",
    "Рейтинг считается по одной стратегии и сохраняется на диск: повторный показ мгновенный. Нажмите «Запустить», чтобы посчитать.", action, { icon: "chart" });
}

/** «Как читать рейтинг»: открыт при первом визите, дальше запоминается. */
export function howToRead(): HTMLDetailsElement {
  return disclosure("Как читать рейтинг", [
    h("p", {}, h("strong", { text: "Смещение отбора. " }),
      "Параметры каждой стратегии подбираются по первым 67% периода (IS), а рейтинг строится по последним 33% (OOS) — на данных, которых оптимизатор не видел. " +
      "Но мы сравниваем десятки стратегий и выбираем лучшие, поэтому даже лидеры OOS отчасти удачливы."),
    h("p", {}, h("strong", { text: "Короткий OOS шумный. " }), "Метрики за несколько месяцев (особенно годовые: Sharpe, Calmar, CAGR) сильно завышены или занижены. Воспринимайте рейтинг как идею для проверки, а не как сигнал к торговле."),
    h("p", {}, h("strong", { text: "Порядок. " }), `Сначала вердикт (${ORDER_TEXT}), внутри — выбранная метрика по OOS (Calmar считается с полом просадки 5%).`),
    h("ul", {}, ...VERDICT_ORDER.map((v) => h("li", {}, h("strong", { text: `${VERDICTS[v].glyph} ${VERDICT_PLURAL[v]} — ` }), VERDICTS[v].hint))),
  ], { key: "best.how", defaultOpen: true, badge: "смещение отбора" });
}
