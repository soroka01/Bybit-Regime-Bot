// Ссылка на репозиторий проекта: иконка в правом краю верхней панели (на широких экранах с подписью) и команда палитры (palette.ts).
import { t } from "../i18n";
import { h } from "./dom";
import { icon } from "./icons";

export const REPO_URL = "https://github.com/soroka01/Bybit-Regime-Bot";

/** Внешняя ссылка: новая вкладка без доступа к window.opener и без referrer; имя для скринридера — aria-label (подпись скрыта на узких экранах). */
export function githubLink(): HTMLAnchorElement {
  return h("a", {
    class: "btn ghost icon-btn gh-link", href: REPO_URL, target: "_blank", rel: "noopener noreferrer",
    "aria-label": t("common.github.aria"), "data-tip": t("common.github.tip"), "data-tip-pos": "left",
  }, icon("github", 18), h("span", { class: "gh-label", text: "GitHub" }));
}
