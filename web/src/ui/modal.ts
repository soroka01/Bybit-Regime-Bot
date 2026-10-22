// Модальное окно на <dialog> (фокус-ловушка и Esc — из коробки), плюс возврат фокуса и подпись через aria-labelledby.
import { t } from "../i18n";
import { h } from "./dom";
import { icon } from "./icons";
import type { Verdict } from "../types";
import { badge, type Tone } from "./kit";
import { verdictInfo } from "./verdicts";

let modalUid = 0;

interface Modal {
  body: HTMLElement;
  close(): void;
}

export function openModal(title: string, onClose?: () => void): Modal {
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const body = h("div", { class: "modal-body" });
  const titleId = `mt${++modalUid}`;
  const dlg = h("dialog", { class: "modal", "aria-labelledby": titleId, tabindex: -1 },
    h("header", { class: "modal-head" }, h("h2", { id: titleId, text: title }),
      h("button", { class: "btn ghost icon-btn", type: "button", "aria-label": t("common.modal.close_aria"), "data-tip": t("common.modal.close_tip"), "data-tip-pos": "left", onClick: () => dlg.close() }, icon("x", 16))),
    body);
  // Esc и клик по подложке закрывают окно; фокус возвращается на элемент, открывший его
  dlg.addEventListener("close", () => { dlg.remove(); onClose?.(); if (opener?.isConnected) opener.focus({ preventScroll: true }); });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  dlg.focus({ preventScroll: true });   // ловушку фокуса и инертность фона обеспечивает <dialog>; начальный фокус — на самом окне (читается заголовок)
  return { body, close: () => dlg.close() };
}

export function verdictBadge(v: Verdict, hint?: string): HTMLElement {
  const d = verdictInfo(v);
  return badge(d.text, d.tone as Tone, { title: hint ?? d.hint, glyph: d.glyph });
}
