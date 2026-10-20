// Вкладка «Бот (live)»: карточка открытой позиции и линейка цены (ликвидация … стоп … вход … mark).
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { num, pct, spct, susd, toneOf, usd } from "../ui/fmt";
import { icon } from "../ui/icons";
import { SIDE_GLYPH, SIDE_WORD } from "../ui/side";
import { positionView, upnlAccountFrac, type PosView } from "./liveCalc";

const rLabel = (k: string) => ({ liq: "Ликвид.≈", stop: "Стоп*", entry: "Вход", mark: "Mark" }[k] ?? k);

/** Линейка цены: ликвидация (оценка) … стоп (расчёт) … вход … текущая цена. Подписи — ровной строкой под шкалой, в порядке цен. */
export function priceRuler(v: PosView): HTMLElement {
  const items = ([["liq", v.liq], ["stop", v.stop], ["entry", v.entry], ["mark", v.mark]] as [string, number | null][])
    .filter((x): x is [string, number] => x[1] != null && Number.isFinite(x[1]));
  const lo = Math.min(...items.map((x) => x[1])), hi = Math.max(...items.map((x) => x[1]));
  const pad = (hi - lo) * 0.1 || hi * 0.01 || 1;
  const X = (p: number) => ((p - (lo - pad)) / (hi - lo + 2 * pad)) * 100;

  const sorted = [...items].sort((a, b) => a[1] - b[1]);       // слева направо, как на шкале
  const placed = sorted.map(([k, p]) => ({ k, p, x: X(p) }));

  const tone = v.upnl > 0 ? "pos" : v.upnl < 0 ? "neg" : "";
  const eX = X(v.entry), mX = X(v.mark);
  const dangerFrom = v.stop != null ? X(v.stop) : null;
  const zone = dangerFrom == null ? null : v.long
    ? h("i", { class: "rl-zone", style: `left:0;width:${Math.max(0, dangerFrom)}%` })
    : h("i", { class: "rl-zone", style: `left:${Math.min(100, dangerFrom)}%;right:0` });
  const summary = placed.map((x) => `${rLabel(x.k).replace("*", "")} ${num(x.p, 2)}`).join(", ");

  return h("div", { class: "lv-ruler", role: "img", "aria-label": `Шкала цены: ${summary}` },
    h("div", { class: "rl-track" },
      zone,
      h("i", { class: `rl-pnl ${tone}`, style: `left:${Math.min(eX, mX)}%;width:${Math.abs(mX - eX)}%` }),
      ...placed.filter((x) => x.k !== "mark").map((x) => h("i", { class: `rl-tick ${x.k}`, style: `left:${x.x}%` })),
      h("i", { class: "rl-dot", style: `left:${mX}%` })),
    h("div", { class: "rl-legend" },
      ...placed.map((x) => h("div", { class: `rl-item ${x.k}` },
        h("i", { class: `rl-key ${x.k}`, "aria-hidden": "true" }),
        h("div", { class: "rl-item-t" }, h("span", { class: "rl-name", text: rLabel(x.k) }), h("span", { class: "rl-price", text: num(x.p, 2) }))))));
}

const fact = (label: string, value: string, sub?: string, cls = "", tip?: string) =>
  h("div", { class: `lv-fact ${cls}`, title: tip ?? "" }, h("div", { class: "lv-fact-l", text: label }), h("div", { class: "lv-fact-v", text: value }), sub ? h("div", { class: "lv-fact-s muted", text: sub }) : null);

export function positionCard(s: LiveSnapshot): HTMLElement {
  const v = positionView(s.position, s.settings);
  const head = (right?: HTMLElement | null) => h("header", { class: "card-head" }, h("h3", { text: "Позиция" }), right ?? null);
  if (!v) {
    const t = s.target_exposure;
    const next = t == null ? "Целевая экспозиция неизвестна — бот определит её на ближайшем закрытии свечи."
      : Math.abs(t) < 1e-9 ? "Целевая экспозиция 0: бот остаётся вне рынка, пока сигналы не изменятся."
        : s.latch ? `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×, но включена защёлка (latch): бот войдёт, только когда сигнал изменится.`
          : s.enabled === false ? `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×, но торговля выключена: ордера не отправляются.`
            : `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×: бот откроет позицию на ближайшей проверке.`;
    return h("section", { class: "card lv-pos is-flat", "aria-label": "Позиция" }, head(h("span", { class: "lv-side flat" }, h("span", { class: "lv-side-g", "aria-hidden": "true", text: "■" }), "FLAT")),
      h("div", { class: "lv-flat" },
        h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("flat", 22)),
        h("p", { class: "strong", text: "Позиции нет — бот вне рынка" }),
        h("p", { class: "muted", text: next })));
  }
  const tone = toneOf(v.upnl, 0.005);
  const sd = v.long ? "long" : "short";
  const side = h("span", { class: `lv-side ${sd}`, title: v.long ? "Лонг: позиция на рост цены. Цвет результата от стороны не зависит." : "Шорт: позиция на падение цены. Цвет результата от стороны не зависит." },
    h("span", { class: "lv-side-g", "aria-hidden": "true", text: SIDE_GLYPH[sd] }), SIDE_WORD[sd]);
  const acct = upnlAccountFrac(v.upnl, s.equity);
  const dist = (d: number | null) => (d == null ? "" : d < 0 ? `цена уже за уровнем (${spct(d, 1)})` : `${pct(d, 1)} от цены`);
  return h("section", { class: "card lv-pos", "aria-label": "Позиция" }, head(side),
    h("div", { class: "lv-pos-top" },
      h("div", { class: "lv-pos-size" }, h("div", { class: "lv-big", text: `${num(v.qty, 4)} BTC` }),
        h("div", { class: "muted small", text: `≈ ${usd(v.notional, 2)}${v.leverage ? ` · плечо ${num(v.leverage, 1)}×` : ""}` })),
      h("div", { class: `lv-pos-pnl ${tone}` }, h("div", { class: "lv-big" }, tone ? h("span", { class: "g", "aria-hidden": "true", text: tone === "pos" ? "▲ " : "▼ " }) : null, susd(v.upnl)),
        h("div", { class: "muted small lv-pos-pct", text: acct != null ? `${spct(acct, 2)} счёта` : "нереализованный результат" }),
        v.upnlPct != null ? h("div", { class: "muted small", text: `${spct(v.upnlPct, 2)} от маржи ≈ ${usd(v.margin, 2)}` }) : null)),
    h("div", { class: "lv-facts" },
      fact("Вход", num(v.entry, 2)),
      fact("Mark", num(v.mark, 2), v.entry ? `${spct(v.mark / v.entry - 1, 2)} к входу` : undefined),
      fact("Стоп (расчёт)", v.stop != null ? num(v.stop, 2) : "—", dist(v.stopDist) || undefined, v.stopDist != null && v.stopDist < 0 ? "bad" : "", `Выведено из цены входа и stop_pct = ${pct(s.settings?.stop_pct, 1)}; фактический стоп стоит на бирже`),
      fact("Ликвидация (оценка)", v.liq != null ? `≈ ${num(v.liq, 2)}` : "—", dist(v.liqDist) || undefined, "", "Оценка: вход × (1 ∓ (1/плечо − 0,5 % поддерживающей маржи)); точное значение — на бирже"),
      fact("Маржа (оценка)", v.margin != null ? `≈ ${usd(v.margin, 2)}` : "—", v.margin != null && s.equity ? `${pct(v.margin / s.equity, 0)} счёта` : undefined, "", "Номинал позиции / плечо"),
      fact("Номинал / счёт", s.equity ? `${num(v.notional / s.equity, 2)}×` : "—", "фактическое плечо на весь счёт", "", "Номинал позиции по mark, делённый на кошелёк (equity) счёта")),
    priceRuler(v),
    v.stopBeyondLiq ? h("p", { class: "notice warn lv-warn", role: "note" }, h("span", {}, h("strong", { text: "Стоп дальше ликвидации. " }), "При таком плече позиция ликвидируется раньше, чем сработает стоп.")) : null,
    h("p", { class: "lv-foot muted small", text: "* Стоп и ликвидация — расчётные оценки по настройкам бота, не данные биржи." }));
}
