// Вкладка «Бот (live)»: цель экспозиции, сигналы ног, обратный отсчёт до закрытия свечи.
import type { LiveSnapshot, Meta } from "../types";
import { h } from "../ui/dom";
import { num } from "../ui/fmt";
import { icon } from "../ui/icons";
import { SIDE_GLYPH } from "../ui/side";
import { gaugePos, legRows, legTotals, nextClose, signalWord, splitLegs } from "./liveCalc";
import { tk } from "./liveTick";

/** Раскрыт ли блок «Неактивные ноги»: вкладка перерисовывается на каждый новый снимок, состояние хранит вызывающий. */
export interface IdleState { open: boolean; onToggle(open: boolean): void }

export function signalsCard(s: LiveSnapshot, meta: Meta | null, now: number, idleState?: IdleState): HTMLElement {
  const t = s.target_exposure;
  const word = t == null ? "нет данных" : Math.abs(t) < 0.005 ? "вне рынка" : t > 0 ? "лонг" : "шорт";
  const valueText = t == null ? "нет данных" : `${t > 0 ? "+" : ""}${num(t, 2)}× — ${word}`;
  const p = t == null ? 0.5 : gaugePos(t);
  const fillCls = t == null ? "" : t > 0 ? "long" : "short";
  const gauge = h("div", { class: "lv-gauge" },
    h("div", { class: "lg-top" }, h("span", { class: "section-label", text: "Целевая экспозиция" }),
      h("span", { class: `lg-val ${fillCls}` }, t == null ? "—" : `${t > 0 ? "+" : ""}${num(t, 2)}×`, h("span", { class: "lg-word", text: ` ${word}` }))),
    h("div", { class: "lg-track", role: "meter", "aria-label": "Целевая экспозиция", "aria-valuemin": -1, "aria-valuemax": 1, "aria-valuenow": t == null ? 0 : Math.max(-1, Math.min(1, t)), "aria-valuetext": valueText },
      h("i", { class: "lg-zero", "aria-hidden": "true" }),
      t == null ? null : h("i", { class: `lg-fill ${fillCls}`, "aria-hidden": "true", style: p >= 0.5 ? `left:50%;width:${(p - 0.5) * 100}%` : `left:${p * 100}%;width:${(0.5 - p) * 100}%` }),
      t == null ? null : h("i", { class: `lg-marker ${fillCls}`, "aria-hidden": "true", style: `left:${p * 100}%` })),
    h("div", { class: "lg-scale", "aria-hidden": "true" }, h("span", { text: "−1 шорт" }), h("span", { text: "0" }), h("span", { text: "+1 лонг" })));

  const baseTf = meta?.base_tf || "1h";
  const nb = nextClose(baseTf, now);
  const nextBar = nb == null ? null : h("div", { class: "lv-nextbar" }, icon("clock", 15),
    h("span", { class: "muted", text: `Следующая свеча ${baseTf} через` }), tk("cd", nb, h("b", { class: "mono" })),
    h("span", { class: "muted small", text: `в ${new Date(nb).toISOString().slice(11, 16)} UTC` }));

  const rows = legRows(s.legs, now);
  const { active, idle } = splitLegs(rows);
  const tot = legTotals(active);
  const list = !rows.length ? h("p", { class: "muted", text: "Нет данных о ногах." })
    : !active.length ? h("p", { class: "muted", text: "Нет работающих стратегий — целевая экспозиция 0, бот вне рынка." })
    : h("ul", { class: "lv-legs", "aria-label": "Ноги бота" },
    h("li", { class: "lv-leg head", "aria-hidden": "true" }, h("span", { text: "Нога" }), h("span", { text: "Вес" }), h("span", { text: "Сигнал" }), h("span", { text: "Вклад" }), h("span", { text: "Закрытие" })),
    ...active.map((r) => {
      const w = signalWord(r.signal);
      const sigText = { long: `${SIDE_GLYPH.long} лонг`, short: `${SIDE_GLYPH.short} шорт`, flat: "■ вне рынка", none: "— нет" }[w];
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-leg" },
        h("span", { class: "lv-leg-id" },
          h("span", { class: "lv-leg-name" }, h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf })),
          h("span", { class: "lv-leg-sub muted small" }, params ? h("span", { class: "mono", text: params }) : null, r.updated ? h("span", {}, " · сигнал ", tk("age", r.updated)) : null)),
        h("span", { class: "lv-leg-w", "data-l": "Вес", text: `${num(r.weight * 100, 0)}%` }),
        h("span", { class: `lv-leg-sig ${w}`, "data-l": "Сигнал", text: sigText }),
        h("span", { class: "lv-leg-c", title: "Вклад в целевую экспозицию (сигнал × вес): направление, а не прибыль", "data-l": "Вклад", text: r.contribution == null ? "—" : `${r.contribution > 0 ? "+" : ""}${num(r.contribution, 2)}` }),
        h("span", { class: "lv-leg-cd mono", "data-l": "Закрытие" }, r.nextClose ? tk("cd", r.nextClose) : "—"));
    }));

  // неактивные ноги (вес 0 / выключены) — свёрнуто под таблицей
  const idleBox = !idle.length ? null : h("details", { class: "lv-idle", open: !!idleState?.open },
    h("summary", { text: `Неактивные ноги (${idle.length})` }),
    h("ul", { class: "lv-idle-list", "aria-label": "Неактивные ноги" }, ...idle.map((r) => {
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-idle-leg" },
        h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf }),
        h("span", { class: "badge off", text: r.enabled ? "не торгуется" : "выключена", title: r.enabled ? "Вес 0 — сигнал считается, но в целевую экспозицию не входит" : "Нога выключена" }),
        params ? h("span", { class: "mono muted small", text: params }) : null);
    })));

  idleBox?.addEventListener("toggle", () => idleState?.onToggle(idleBox.open));
  return h("section", { class: "card lv-signals", "aria-label": "Цель и сигналы" },
    h("header", { class: "card-head" }, h("h3", { text: "Цель и сигналы" })),
    gauge, nextBar, list, idleBox,
    active.length ? h("p", { class: "lv-foot muted small", text: `Σ весов ${num(tot.weights * 100, 0)}% · Σ вкладов ${tot.contribution > 0 ? "+" : ""}${num(tot.contribution, 2)}${t != null ? ` · цель ${t > 0 ? "+" : ""}${num(t, 2)}×` : ""}. Время закрытия — по границам свечей UTC.` }) : null,
    h("p", { class: "lv-hint muted small", text: "Добавить или убрать стратегию: Telegram → 🧩 Стратегии → ➕ Добавить, либо кнопка «В бота» в Лаборатории, Рейтинге и Портфеле." }));
}
