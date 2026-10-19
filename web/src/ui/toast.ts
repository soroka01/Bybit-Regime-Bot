// Стек тостов: несколько уведомлений друг над другом, автоскрытие (ошибки висят дольше), иконка по типу.
import { h } from "./dom";
import { icon } from "./icons";

export type ToastKind = "ok" | "error" | "info";
const ICON: Record<ToastKind, string> = { ok: "ok", error: "error", info: "info" };
const MAX = 4;

let host: HTMLElement | null = null;

export function toast(text: string, kind: ToastKind = "info", ms?: number): void {
  if (!host || !host.isConnected) {
    host = h("div", { class: "toasts", role: "status", "aria-live": "polite", "aria-atomic": "false" });
    document.body.append(host);
  }
  while (host.childElementCount >= MAX) host.firstElementChild?.remove();
  const el = h("div", { class: `toast ${kind}` }, icon(ICON[kind], 18), h("span", { text }));
  host.append(el);
  const life = ms ?? (kind === "error" ? 5200 : 2400);
  window.setTimeout(() => {
    el.classList.add("leaving");
    window.setTimeout(() => el.remove(), 180);
  }, life);
}
