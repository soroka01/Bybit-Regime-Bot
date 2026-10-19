// Компактная плашка статуса бота в верхней панели. Переиспользует тот же read-only снимок /api/live, что и вкладка «Бот (live)»:
// пока открыта вкладка live, снимки приходят событием "tb-live" (лишних запросов нет), иначе плашка сама опрашивает раз в 15 с.
import { api, asApiError } from "../api";
import type { LiveSnapshot } from "../types";
import { h } from "./dom";
import { envFlag } from "./env";
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
  const setLabel = (t: string) => { el.title = t; el.setAttribute("aria-label", t); };
  let lastAt = 0;
  let ctl: AbortController | null = null;      // идущий опрос: не больше одного за раз

  const paint = (s: LiveSnapshot | null) => {
    el.hidden = false;
    if (!s) {   // сервер дашборда недоступен
      el.className = "bot-pill off";
      el.replaceChildren(h("i", { class: "dot warn", "aria-hidden": "true" }), h("span", { text: "Нет связи" }));
      setLabel("Статус бота: сервер дашборда недоступен");
      return;
    }
    if (!s.available) {
      el.className = "bot-pill off";
      el.replaceChildren(h("i", { class: "dot", "aria-hidden": "true" }), h("span", { text: "Бот не запущен" }));
      setLabel("Бот не запущен: снимок состояния недоступен");
      return;
    }
    const stale = heartbeatState(s.heartbeat, Date.now()).tone !== "ok";
    const flag = envFlag(s.env, s.paper);       // рабочую среду (mainnet) не показываем; предупреждение — только testnet / paper
    const envText = s.paper ? "PAPER" : "TESTNET";
    const tradeText = s.killed ? "Аварийная остановка" : s.enabled ? "Торговля вкл" : "Торговля выкл";
    const dot = s.killed ? "bad" : stale ? "warn" : s.enabled ? "ok" : "";
    const pos = s.position;
    const acct = pos && pos.qty && s.equity ? pos.upnl / s.equity : null;
    const long = !!pos && isLongSide(pos.side);
    const parts: (HTMLElement | null)[] = [
      h("i", { class: `dot ${dot}`, "aria-hidden": "true" }),
      flag ? h("span", { class: "bp-env warn", text: envText, title: flag.title }) : null,
      flag ? h("i", { class: "sep", "aria-hidden": "true" }) : null,
      h("span", { class: "bp-trade", text: stale && !s.killed ? "Нет сигнала жизни" : tradeText }),
    ];
    if (pos && pos.qty) {
      const tone = pos.upnl > 0 ? "pos" : pos.upnl < 0 ? "neg" : "";
      parts.push(
        h("i", { class: "sep s2", "aria-hidden": "true" }),
        h("span", { class: "bp-pos", title: "Сторона позиции; цвет результата от стороны не зависит", text: `${SIDE_GLYPH[long ? "long" : "short"]} ${SIDE_WORD[long ? "long" : "short"]} ${num(pos.qty, 4)}` }),
        h("span", { class: `bp-pnl ${tone}`, text: `${glyph(tone)} ${(pos.upnl > 0 ? "+" : "") + usd(pos.upnl)}`.trim() }),
        acct != null ? h("span", { class: `bp-pct ${tone}`, text: spct(acct, 2), title: "Нереализованный результат / кошелёк счёта" }) : null);
    } else if (s.equity != null && s.peak_equity) {
      const dd = s.equity / s.peak_equity - 1;
      if (dd < -0.001) parts.push(h("i", { class: "sep s2", "aria-hidden": "true" }), h("span", { class: "bp-pnl neg", text: `▼ ${spct(dd, 1)}`, title: "Просадка от пика" }));
    }
    el.className = "bot-pill";
    el.replaceChildren(...parts.filter((p): p is HTMLElement => !!p));
    const summary = ["Бот", flag ? `ВНИМАНИЕ: ${envText}` : "", tradeText, stale ? "нет сигнала жизни" : "", pos && pos.qty ? `позиция ${long ? "лонг" : "шорт"} ${num(pos.qty, 4)}, нереализованный результат ${usd(pos.upnl)}${acct != null ? ` (${spct(acct, 2)} счёта)` : ""}` : "позиции нет"].filter(Boolean).join(", ");
    setLabel(summary + ". Открыть вкладку «Бот (live)»");
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
