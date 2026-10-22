// Компактная плашка статуса бота в верхней панели. Переиспользует тот же read-only снимок /api/live, что и вкладка «Бот (live)»:
// пока открыта вкладка live, снимки приходят событием "tb-live" (лишних запросов нет), иначе плашка сама опрашивает раз в 15 с.
import { api, asApiError } from "../api";
import { t } from "../i18n";
import type { LiveSnapshot } from "../types";
import { h } from "./dom";
import { num, spct, usd } from "./fmt";
import { heartbeatState, isLongSide } from "./heartbeat";
import { glyph } from "./kit";
import { SIDE_GLYPH, SIDE_WORD } from "./side";

const POLL_MS = 15_000;
/** Один опрос не живёт дольше этого: зависший запрос не должен блокировать следующие (иначе плашка залипнет до таймаута api.ts в 120 с). */
const POLL_TIMEOUT_MS = 20_000;

/** Вкладка live сообщает плашке свежий снимок. */
export const publishLive = (s: LiveSnapshot): void => { window.dispatchEvent(new CustomEvent("tb-live", { detail: s })); };

export function botStatusPill(): HTMLElement {
  const el = h("a", { class: "bot-pill off", href: "#/live", hidden: true });
  const setLabel = (label: string) => { el.title = label; el.setAttribute("aria-label", label); };
  let lastAt = 0;
  let ctl: AbortController | null = null;      // идущий опрос: не больше одного за раз

  const paint = (s: LiveSnapshot | null) => {
    el.hidden = false;
    if (!s) {   // сервер дашборда недоступен
      el.className = "bot-pill off";
      el.replaceChildren(h("i", { class: "dot warn", "aria-hidden": "true" }), h("span", { text: t("live.pill_offline") }));
      setLabel(t("live.pill_offline_tip"));
      return;
    }
    if (!s.available) {
      el.className = "bot-pill off";
      el.replaceChildren(h("i", { class: "dot", "aria-hidden": "true" }), h("span", { text: t("live.pill_stopped") }));
      setLabel(t("live.pill_stopped_tip"));
      return;
    }
    const stale = heartbeatState(s.heartbeat, Date.now()).tone !== "ok";
    const tradeText = s.killed ? t("live.killed") : s.enabled ? t("live.pill_on") : t("live.pill_off");
    const dot = s.killed ? "bad" : stale ? "warn" : s.enabled ? "ok" : "";
    const pos = s.position;
    const acct = pos && pos.qty && s.equity ? pos.upnl / s.equity : null;
    const long = !!pos && isLongSide(pos.side);
    const parts: (HTMLElement | null)[] = [
      h("i", { class: `dot ${dot}`, "aria-hidden": "true" }),
      h("span", { class: "bp-trade", text: stale && !s.killed ? t("live.no_heartbeat") : tradeText }),
    ];
    if (pos && pos.qty) {
      const tone = pos.upnl > 0 ? "pos" : pos.upnl < 0 ? "neg" : "";
      parts.push(
        h("i", { class: "sep s2", "aria-hidden": "true" }),
        h("span", { class: "bp-pos", title: t("live.pill_side_tip"), text: `${SIDE_GLYPH[long ? "long" : "short"]} ${SIDE_WORD[long ? "long" : "short"]} ${num(pos.qty, 4)}` }),
        h("span", { class: `bp-pnl ${tone}`, text: `${glyph(tone)} ${(pos.upnl > 0 ? "+" : "") + usd(pos.upnl)}`.trim() }),
        acct != null ? h("span", { class: `bp-pct ${tone}`, text: spct(acct, 2), title: t("live.pill_pct_tip") }) : null);
    } else if (s.equity != null && s.peak_equity) {
      const dd = s.equity / s.peak_equity - 1;
      if (dd < -0.001) parts.push(h("i", { class: "sep s2", "aria-hidden": "true" }), h("span", { class: "bp-pnl neg", text: `▼ ${spct(dd, 1)}`, title: t("live.dd_peak") }));
    }
    el.className = "bot-pill";
    el.replaceChildren(...parts.filter((p): p is HTMLElement => !!p));
    const summary = [t("live.sum_bot"), tradeText, stale ? t("live.no_heartbeat_l") : "", pos && pos.qty
      ? t("live.sum_pos", { side: t(long ? "live.long" : "live.short"), qty: num(pos.qty, 4), upnl: usd(pos.upnl), acct: acct != null ? ` (${t("live.of_account", { p: spct(acct, 2) })})` : "" })
      : t("live.sum_flat")].filter(Boolean).join(", ");
    setLabel(`${summary}. ${t("live.sum_open")}`);
  };

  window.addEventListener("tb-live", (e) => {
    lastAt = Date.now();
    ctl?.abort();                    // снимок от вкладки live свежее идущего опроса — его ответ не должен его затереть
    paint((e as CustomEvent<LiveSnapshot>).detail);
  });

  const poll = async () => {
    if (document.hidden || ctl || Date.now() - lastAt < POLL_MS - 1000) return;   // скрыто / опрос уже идёт / свежий снимок пришёл от вкладки live
    const c = (ctl = new AbortController());
    let timedOut = false;
    const to = window.setTimeout(() => { timedOut = true; c.abort(); }, POLL_TIMEOUT_MS);
    try {
      const s = await api.live(c.signal);
      lastAt = Date.now();
      paint(s);
    } catch (e) {
      // отмена (скрыли вкладку / пришёл снимок от live) — молча; таймаут и сетевые ошибки — «Нет связи»
      if (timedOut || !(c.signal.aborted || asApiError(e).silent)) paint(null);
    } finally {
      clearTimeout(to);
      if (ctl === c) ctl = null;
    }
  };
  void poll();
  window.setInterval(() => void poll(), POLL_MS);
  document.addEventListener("visibilitychange", () => { if (document.hidden) ctl?.abort(); else void poll(); });
  return el;
}
