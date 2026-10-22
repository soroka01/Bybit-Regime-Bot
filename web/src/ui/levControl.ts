// Выбор плеча 1×…100×: нелинейный слайдер (шаги 0.5 → 1 → 5 → 25), числовое поле, пресеты и подсказка о риске ликвидации.
import { t } from "../i18n";
import { botLimitNote, clampLev, LEV_POS_MAX, LEV_PRESETS, levFromPos, liqHintText, posFromLev, riskLevel } from "../leverage";
import { SPOT_LEV_NOTE } from "../market";
import { syncRange, type Control } from "./controls";
import { h } from "./dom";

interface LevControl extends Control<number> {
  /** Заблокировать выбор плеча (спот): показывает note вместо подсказки о ликвидации. */
  setDisabled(on: boolean, note?: string): void;
}

interface LevOpts {
  value: number;
  /** Подсказка «бот ограничен 3×» при плече выше потолка бота. */
  botNote?: boolean;
  label?: string;
  onInput(v: number): void;
}

let uid = 0;

const fmtVal = (v: number) => String(+v.toFixed(2));

export function leverageControl(o: LevOpts): LevControl {
  const id = `lev${++uid}`;
  let value = clampLev(o.value);
  let disabled = false;
  let note = SPOT_LEV_NOTE;

  const range = h("input", { type: "range", id, min: 0, max: LEV_POS_MAX, step: 1, value: posFromLev(value), "aria-label": t("common.lev.scale") });
  const box = h("input", { type: "number", min: 1, max: 100, step: 0.5, value: fmtVal(value), "aria-label": o.label ?? t("common.lev.label"), class: "num-input" });
  const chips = LEV_PRESETS.map((p) => h("button", {
    type: "button", class: "chip-btn", text: `${p}×`, "aria-label": t("common.lev.preset_aria", { p }),
    onClick: () => { if (!disabled) commit(p); },
  }));
  const hint = h("p", { class: "lev-hint small", "aria-live": "polite" });
  const bot = h("p", { class: "lev-hint bot small" });

  function paint(fromBox = false) {
    range.value = String(posFromLev(value));
    range.setAttribute("aria-valuetext", `${fmtVal(value)}×`);
    syncRange(range);
    if (!fromBox) box.value = fmtVal(value);
    chips.forEach((c, i) => c.setAttribute("aria-pressed", String(!disabled && LEV_PRESETS[i] === value)));
    range.disabled = box.disabled = disabled;
    chips.forEach((c) => { c.disabled = disabled; });
    const risk = riskLevel(value);
    hint.className = `lev-hint small ${disabled ? "muted" : risk}`;
    hint.textContent = disabled ? note : liqHintText(value);
    const b = o.botNote && !disabled ? botLimitNote(value) : null;
    bot.textContent = b ?? "";
    bot.hidden = !b;
  }
  function commit(v: number, fromBox = false) {
    value = clampLev(v);
    paint(fromBox);
    o.onInput(value);
  }

  range.addEventListener("input", () => commit(levFromPos(parseFloat(range.value))));
  box.addEventListener("input", () => { if (box.value !== "" && Number.isFinite(parseFloat(box.value))) commit(parseFloat(box.value), true); });
  box.addEventListener("change", () => { if (Number.isFinite(parseFloat(box.value))) commit(parseFloat(box.value)); else paint(); });

  const el = h("div", { class: "field lev" },
    h("div", { class: "field-head" },
      h("label", { for: id, text: o.label ?? t("common.lev.label") }),
      h("span", { class: "field-val" }, box, h("span", { class: "unit", text: "×" }))),
    range,
    h("div", { class: "lev-presets", role: "group", "aria-label": t("common.lev.presets") }, ...chips),
    hint, bot);
  paint();
  return {
    el,
    set(v) { value = clampLev(v); paint(); },
    setDisabled(on, why) { disabled = on; if (why) note = why; paint(); },
  };
}
