// Вкладка «Бот (live)»: карточка открытой позиции и линейка цены (ликвидация … стоп … вход … mark).
import { t } from "../i18n";
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { num, pct, spct, susd, toneOf, usd } from "../ui/fmt";
import { icon } from "../ui/icons";
import { SIDE_GLYPH, SIDE_WORD } from "../ui/side";
import { positionView, upnlAccountFrac, type PosView } from "./liveCalc";

const rLabel = (k: string) => ({ liq: t("live.rl_liq"), stop: t("live.rl_stop"), entry: t("live.entry"), mark: t("live.mark") }[k] ?? k);

/** Линейка цены: ликвидация (оценка) … стоп (расчёт) … вход … текущая цена. Подписи — ровной строкой под шкалой, в порядке цен. */
function priceRuler(v: PosView): HTMLElement {
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

  return h("div", { class: "lv-ruler", role: "img", "aria-label": t("live.ruler_aria", { summary }) },
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
  const head = (right?: HTMLElement | null) => h("header", { class: "card-head" }, h("h3", { text: t("live.position") }), right ?? null);
  if (!v) {
    const tg = s.target_exposure;
    const tp = tg == null ? null : { side: tg > 0 ? t("live.long") : t("live.short"), x: num(Math.abs(tg), 2) };
    const next = tg == null || tp == null ? t("live.next_unknown")
      : Math.abs(tg) < 1e-9 ? t("live.next_zero")
        : s.latch ? t("live.next_latch", tp)
          : s.enabled === false ? t("live.next_disabled", tp)
            : t("live.next_open", tp);
    return h("section", { class: "card lv-pos is-flat", "aria-label": t("live.position") }, head(h("span", { class: "lv-side flat" }, h("span", { class: "lv-side-g", "aria-hidden": "true", text: "■" }), t("live.flat_badge"))),
      h("div", { class: "lv-flat" },
        h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("flat", 22)),
        h("p", { class: "strong", text: t("live.no_pos") }),
        h("p", { class: "muted", text: next })));
  }
  const tone = toneOf(v.upnl, 0.005);
  const sd = v.long ? "long" : "short";
  const side = h("span", { class: `lv-side ${sd}`, title: v.long ? t("live.side_long_tip_pos") : t("live.side_short_tip_pos") },
    h("span", { class: "lv-side-g", "aria-hidden": "true", text: SIDE_GLYPH[sd] }), SIDE_WORD[sd]);
  const acct = upnlAccountFrac(v.upnl, s.equity);
  const dist = (d: number | null) => (d == null ? "" : d < 0 ? t("live.dist_past", { p: spct(d, 1) }) : t("live.dist_from", { p: pct(d, 1) }));
  return h("section", { class: "card lv-pos", "aria-label": t("live.position") }, head(side),
    h("div", { class: "lv-pos-top" },
      h("div", { class: "lv-pos-size" }, h("div", { class: "lv-big", text: `${num(v.qty, 4)} BTC` }),
        h("div", { class: "muted small", text: `≈ ${usd(v.notional, 2)}${v.leverage ? ` · ${t("live.lev_inline", { x: num(v.leverage, 1) })}` : ""}` })),
      h("div", { class: `lv-pos-pnl ${tone}` }, h("div", { class: "lv-big" }, tone ? h("span", { class: "g", "aria-hidden": "true", text: tone === "pos" ? "▲ " : "▼ " }) : null, susd(v.upnl)),
        h("div", { class: "muted small lv-pos-pct", text: acct != null ? t("live.of_account", { p: spct(acct, 2) }) : t("live.upnl_unreal") }),
        v.upnlPct != null ? h("div", { class: "muted small", text: t("live.of_margin", { p: spct(v.upnlPct, 2), m: usd(v.margin, 2) }) }) : null)),
    h("div", { class: "lv-facts" },
      fact(t("live.entry"), num(v.entry, 2)),
      fact(t("live.mark"), num(v.mark, 2), v.entry ? t("live.to_entry", { p: spct(v.mark / v.entry - 1, 2) }) : undefined),
      fact(t("live.f_stop"), v.stop != null ? num(v.stop, 2) : "—", dist(v.stopDist) || undefined, v.stopDist != null && v.stopDist < 0 ? "bad" : "", t("live.f_stop_tip", { p: pct(s.settings?.stop_pct, 1) })),
      fact(t("live.f_liq"), v.liq != null ? `≈ ${num(v.liq, 2)}` : "—", dist(v.liqDist) || undefined, "", t("live.f_liq_tip")),
      fact(t("live.f_margin"), v.margin != null ? `≈ ${usd(v.margin, 2)}` : "—", v.margin != null && s.equity ? t("live.of_account", { p: pct(v.margin / s.equity, 0) }) : undefined, "", t("live.f_margin_tip")),
      fact(t("live.f_notional"), s.equity ? `${num(v.notional / s.equity, 2)}×` : "—", t("live.f_notional_sub"), "", t("live.f_notional_tip"))),
    priceRuler(v),
    v.stopBeyondLiq ? h("p", { class: "notice warn lv-warn", role: "note" }, h("span", {}, h("strong", { text: `${t("live.stop_beyond_t")} ` }), t("live.stop_beyond_h"))) : null,
    h("p", { class: "lv-foot muted small", text: t("live.pos_foot") }));
}
