// Вкладка «Бот (live)»: цель экспозиции, сигналы ног, обратный отсчёт до закрытия свечи.
import { t } from "../i18n";
import type { LiveSnapshot, Meta } from "../types";
import { h } from "../ui/dom";
import { num } from "../ui/fmt";
import { icon } from "../ui/icons";
import { SIDE_GLYPH } from "../ui/side";
import { gaugePos, legRows, legTotals, nextClose, signalWord, splitLegs } from "./liveCalc";
import { tk } from "./liveTick";

/** Раскрыт ли блок «Неактивные ноги»: вкладка перерисовывается на каждый новый снимок, состояние хранит вызывающий. */
interface IdleState { open: boolean; onToggle(open: boolean): void }

export function signalsCard(s: LiveSnapshot, meta: Meta | null, now: number, idleState?: IdleState): HTMLElement {
  const tg = s.target_exposure;
  const word = tg == null ? t("live.no_data") : Math.abs(tg) < 0.005 ? t("live.flat_w") : tg > 0 ? t("live.long") : t("live.short");
  const valueText = tg == null ? t("live.no_data") : `${tg > 0 ? "+" : ""}${num(tg, 2)}× — ${word}`;
  const p = tg == null ? 0.5 : gaugePos(tg);
  const fillCls = tg == null ? "" : tg > 0 ? "long" : "short";
  const gauge = h("div", { class: "lv-gauge" },
    h("div", { class: "lg-top" }, h("span", { class: "section-label", text: t("live.target") }),
      h("span", { class: `lg-val ${fillCls}` }, tg == null ? "—" : `${tg > 0 ? "+" : ""}${num(tg, 2)}×`, h("span", { class: "lg-word", text: ` ${word}` }))),
    h("div", { class: "lg-track", role: "meter", "aria-label": t("live.target"), "aria-valuemin": -1, "aria-valuemax": 1, "aria-valuenow": tg == null ? 0 : Math.max(-1, Math.min(1, tg)), "aria-valuetext": valueText },
      h("i", { class: "lg-zero", "aria-hidden": "true" }),
      tg == null ? null : h("i", { class: `lg-fill ${fillCls}`, "aria-hidden": "true", style: p >= 0.5 ? `left:50%;width:${(p - 0.5) * 100}%` : `left:${p * 100}%;width:${(0.5 - p) * 100}%` }),
      tg == null ? null : h("i", { class: `lg-marker ${fillCls}`, "aria-hidden": "true", style: `left:${p * 100}%` })),
    h("div", { class: "lg-scale", "aria-hidden": "true" }, h("span", { text: t("live.scale_short") }), h("span", { text: "0" }), h("span", { text: t("live.scale_long") })));

  const baseTf = meta?.base_tf || "1h";
  const nb = nextClose(baseTf, now);
  const nextBar = nb == null ? null : h("div", { class: "lv-nextbar" }, icon("clock", 15),
    h("span", { class: "muted", text: t("live.next_bar", { tf: baseTf }) }), tk("cd", nb, h("b", { class: "mono" })),
    h("span", { class: "muted small", text: t("live.at_utc", { time: new Date(nb).toISOString().slice(11, 16) }) }));

  const rows = legRows(s.legs, now);
  const { active, idle } = splitLegs(rows);
  const tot = legTotals(active);
  const list = !rows.length ? h("p", { class: "muted", text: t("live.legs_none") })
    : !active.length ? h("p", { class: "muted", text: t("live.legs_idle_all") })
    : h("ul", { class: "lv-legs", "aria-label": t("live.legs_aria") },
    h("li", { class: "lv-leg head", "aria-hidden": "true" }, h("span", { text: t("live.h_leg") }), h("span", { text: t("live.h_weight") }), h("span", { text: t("live.h_signal") }), h("span", { text: t("live.h_contrib") }), h("span", { text: t("live.h_close") })),
    ...active.map((r) => {
      const w = signalWord(r.signal);
      const sigText = { long: `${SIDE_GLYPH.long} ${t("live.long")}`, short: `${SIDE_GLYPH.short} ${t("live.short")}`, flat: `■ ${t("live.flat_w")}`, none: t("live.sig_none") }[w];
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-leg" },
        h("span", { class: "lv-leg-id" },
          h("span", { class: "lv-leg-name" }, h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf })),
          h("span", { class: "lv-leg-sub muted small" }, params ? h("span", { class: "mono", text: params }) : null, r.updated ? h("span", {}, ` · ${t("live.signal_lc")} `, tk("age", r.updated)) : null)),
        h("span", { class: "lv-leg-w", "data-l": t("live.h_weight"), text: `${num(r.weight * 100, 0)}%` }),
        h("span", { class: `lv-leg-sig ${w}`, "data-l": t("live.h_signal"), text: sigText }),
        h("span", { class: "lv-leg-c", title: t("live.contrib_tip"), "data-l": t("live.h_contrib"), text: r.contribution == null ? "—" : `${r.contribution > 0 ? "+" : ""}${num(r.contribution, 2)}` }),
        h("span", { class: "lv-leg-cd mono", "data-l": t("live.h_close") }, r.nextClose ? tk("cd", r.nextClose) : "—"));
    }));

  // неактивные ноги (вес 0 / выключены) — свёрнуто под таблицей
  const idleBox = !idle.length ? null : h("details", { class: "lv-idle", open: !!idleState?.open },
    h("summary", { text: t("live.idle_sum", { n: idle.length }) }),
    h("ul", { class: "lv-idle-list", "aria-label": t("live.idle_aria") }, ...idle.map((r) => {
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-idle-leg" },
        h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf }),
        h("span", { class: "badge off", text: r.enabled ? t("live.idle_zero") : t("live.idle_off"), title: r.enabled ? t("live.idle_zero_tip") : t("live.idle_off_tip") }),
        params ? h("span", { class: "mono muted small", text: params }) : null);
    })));

  idleBox?.addEventListener("toggle", () => idleState?.onToggle(idleBox.open));
  return h("section", { class: "card lv-signals", "aria-label": t("live.signals_t") },
    h("header", { class: "card-head" }, h("h3", { text: t("live.signals_t") })),
    gauge, nextBar, list, idleBox,
    active.length ? h("p", { class: "lv-foot muted small", text: t("live.foot", { w: num(tot.weights * 100, 0), c: `${tot.contribution > 0 ? "+" : ""}${num(tot.contribution, 2)}`, target: tg != null ? ` · ${t("live.foot_target", { v: `${tg > 0 ? "+" : ""}${num(tg, 2)}` })}` : "" }) }) : null,
    h("p", { class: "lv-hint muted small", text: t("live.hint") }));
}
