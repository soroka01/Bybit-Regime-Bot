// Переключатель языка EN | RU в верхней панели (рядом с темой). Выбор сохраняется в localStorage "tb.lang" и страница перезагружается (i18n/index.ts).
import { getLang, LANGS, switchLang, t, type Lang } from "../i18n";
import { flushSaves } from "../state";
import { h } from "./dom";

const NAME: Record<Lang, () => string> = { en: () => t("lang.en"), ru: () => t("lang.ru") };

export function langSwitch(): HTMLElement {
  const wrap = h("div", { class: "theme-switch lang-switch", role: "group", "aria-label": t("lang.group") });
  for (const l of LANGS) {
    const b = h("button", {
      type: "button", lang: l, "aria-label": NAME[l](), "aria-pressed": String(l === getLang()), "data-tip": t("lang.switch_tip", { name: NAME[l]() }),
    }, h("span", { class: "ls-code", text: l.toUpperCase() }));
    b.addEventListener("click", () => { flushSaves(); switchLang(l); });
    wrap.append(b);
  }
  return wrap;
}
