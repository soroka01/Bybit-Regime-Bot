// Отрисовка секций вкладки «Бот (live)»: чистые построители DOM (снимок -> элемент), без сетевых вызовов и состояния.
// Всё «живое» (обратный отсчёт, «N с назад») помечено data-tk и обновляется тикером из live.ts раз в секунду.
import { sparkline } from "../charts/sparkline";
import type { LiveSnapshot, Meta, StatsResponse } from "../types";
import { h } from "../ui/dom";
import { envBadge } from "../ui/env";
import { ago, datetime, num, parseTime, pct, spct, spctU, susd, toneOf, usd } from "../ui/fmt";
import { icon } from "../ui/icons";
import { badge, deltaChip, emptyState, glyph, meterBar, sectionLabel } from "../ui/kit";
import { orderBadge, SIDE_GLYPH, SIDE_WORD, sideBadge } from "../ui/side";
import {
  closedKey, drawdownFrac, eventKind, filterEvents, fmtCountdown, gaugePos, heartbeatState, isBuy, isNoiseEvent, legRows, legTotals, nextClose,
  plToday, positionView, riskMeter, rangeStart, signalWord, sliceSeries, splitLegs, stopRiskWarning, upnlAccountFrac,
  type EventFilter, type LiveEvent, type LiveTrade, type Pt, type PosView, type RangeKey,
} from "./liveCalc";
import { resolveWindow, todayLabel, type Feeds } from "./liveWindow";
import { ddmm, fmtDuration, hhmm, isoUtc, periodSummary, resultText, resultTone } from "./statsLogic";
import { reasonCell } from "./statsView";

export const LATCH_TIP = "Защёлка (latch): после срабатывания стопа или ручного закрытия бот не входит в рынок повторно, пока целевая экспозиция не изменится.";

/** Элемент, который тикер обновляет раз в секунду: cd — обратный отсчёт до метки времени (мс), age — «N с назад» от ISO-времени. */
export const tk = (kind: "cd" | "age", value: number | string, el: HTMLElement = h("span")): HTMLElement => {
  el.dataset.tk = kind;
  el.dataset.v = String(value);
  el.textContent = kind === "cd" ? fmtCountdown(+value - Date.now()) : ago(String(value));
  return el;
};

export function tickAll(root: ParentNode, now = Date.now()): void {
  root.querySelectorAll<HTMLElement>("[data-tk]").forEach((el) => {
    const v = el.dataset.v ?? "";
    el.textContent = el.dataset.tk === "cd" ? fmtCountdown(+v - now) : ago(v);
  });
}

// ---------- шапка: баннеры «не отвечает» и «остановлен»
export function staleBanner(s: LiveSnapshot): HTMLElement | null {
  const hb = heartbeatState(s.heartbeat, Date.now());
  if (hb.tone === "ok") return null;
  const d = parseTime(s.heartbeat ?? null);
  return h("div", { class: `banner ${hb.tone === "bad" ? "error" : "warn"}`, role: "alert" },
    h("strong", { text: "Бот не отвечает. " }),
    d ? h("span", {}, "Последний сигнал жизни — ", tk("age", s.heartbeat!), ` (${datetime(s.heartbeat)}). `) : h("span", { text: "Сигнала жизни нет вовсе. " }),
    h("span", { text: "Цифры ниже могут быть устаревшими; если бот должен работать — проверьте процесс и Telegram." }));
}

export function killedBanner(s: LiveSnapshot): HTMLElement | null {
  if (!s.killed) return null;
  return h("div", { class: "banner error", role: "alert" },
    h("strong", { text: "Аварийная остановка. " }),
    h("span", { text: s.kill_reason ? `Причина: ${s.kill_reason}. ` : "" }),
    h("span", { text: "Новые ордера не выставляются; возобновить работу можно только вручную через Telegram." }));
}

// ---------- герой: капитал, P/L, просадка, статусы
export function heroCard(s: LiveSnapshot, eq: readonly Pt[], now: number, range: RangeKey = "24h", feeds: Feeds = {}): HTMLElement {
  const equity = s.equity ?? eq[eq.length - 1]?.v ?? null;
  const today = plToday(eq, s.equity, now);
  // результат за окно графика (то же окно, что выбрано в переключателе): подпись называет реальный период (liveWindow.resolveWindow)
  const win = resolveWindow(range, eq, equity, now, feeds);
  const dd = drawdownFrac(equity, s.peak_equity);
  const hb = heartbeatState(s.heartbeat, now);

  const plChip = (label: string, abs: number, p: number | null, hint: string) => {
    const tone = toneOf(abs, 0.005);
    return h("div", { class: "lv-pl", title: hint },
      deltaChip(tone, `${susd(abs)}${p != null ? ` · ${spct(p, 2)}` : ""}`), h("span", { class: "lv-pl-l", text: label }));
  };
  const lastT = eq[eq.length - 1]?.t ?? now;
  const sp = sparkline(sliceSeries(eq, rangeStart("24h", lastT)).map((p) => p.v), { w: 260, h: 64, label: "Кошелёк счёта за последние 24 часа" });

  const hbBadge = h("span", { class: `badge ${hb.tone === "bad" ? "bad" : hb.tone}${hb.tone === "ok" ? " live" : " has-glyph"}`, title: `Последний сигнал жизни: ${datetime(s.heartbeat)}` },
    hb.tone === "ok" ? null : h("span", { class: "g", "aria-hidden": "true", text: "!" }),
    hb.tone === "ok" ? "Жив · " : "Нет сигнала жизни · ",
    s.heartbeat ? tk("age", s.heartbeat) : "нет данных");

  const pills = h("div", { class: "badges lv-pills", role: "list", "aria-label": "Статус бота" },
    envBadge(s.env, s.paper),       // рабочая среда (mainnet) не рекламируется: только testnet / paper
    s.killed ? badge("Аварийная остановка", "bad", { glyph: "✕" })
      : s.enabled ? badge("Торговля включена", "ok", { live: true }) : badge("Торговля выключена", "off"),
    s.killed ? null : badge("Kill-switch: не сработал", "ok", { glyph: "✓", title: "Лимиты просадки и дневного убытка не превышены" }),
    s.latch ? badge("Latch: ждёт смены сигнала", "warn", { glyph: "!", title: LATCH_TIP }) : null,
    hbBadge);
  pills.querySelectorAll(".badge").forEach((b) => b.setAttribute("role", "listitem"));

  const edge = s.killed ? "--neg" : hb.tone !== "ok" ? (hb.tone === "bad" ? "--neg" : "--warn") : "";
  return h("section", { class: "card lv-hero accent-edge", "aria-label": "Капитал", style: edge ? `--edge: var(${edge})` : "" },
    h("div", { class: "lv-hero-main" },
      sectionLabel("Кошелёк счёта"),
      h("div", { class: "lv-equity", text: usd(equity) }),
      h("div", { class: "lv-pls" },
        today ? plChip(todayLabel(today, now), today.abs, today.pct, `Изменение кошелька с первой точки текущих суток UTC (${datetime(new Date(today.since).toISOString())}): ${usd(today.base)} → ${usd(today.cur)}`)
          : h("div", { class: "lv-pl muted small", text: "За сегодня данных пока нет" }),
        win.state === "loading" ? h("div", { class: "lv-pl lv-pl-loading muted small", role: "status", text: `${win.label}: загрузка…` })
          : win.pl ? plChip(win.label, win.pl.abs, win.pl.pct, win.title) : null)),
    h("div", { class: "lv-hero-spark", "aria-label": "Капитал за 24 часа" },
      sp ?? h("div", { class: "muted small", text: "Мало точек для графика" }),
      sp ? h("div", { class: "lv-spark-cap muted small", text: "Капитал, 24 ч" }) : null),
    h("div", { class: "lv-hero-stats" },
      h("div", { class: "lv-stat" }, h("div", { class: "lv-stat-l", text: "Просадка от пика" }),
        h("div", { class: `lv-stat-v ${dd != null && dd > 0.001 ? "neg" : ""}` }, dd != null && dd > 0.001 ? h("span", { class: "g", "aria-hidden": "true", text: "▼ " }) : null, dd == null ? "—" : dd > 0.001 ? pct(-dd, 1) : "на пике"),
        h("div", { class: "lv-stat-s muted", text: s.peak_equity != null ? `пик ${usd(s.peak_equity)}` : "" })),
      h("div", { class: "lv-stat" }, h("div", { class: "lv-stat-l", text: "Последняя свеча" }),
        h("div", { class: "lv-stat-v sm", text: s.last_bar ? datetime(s.last_bar) : "—" }),
        h("div", { class: "lv-stat-s muted" }, s.last_bar ? tk("age", s.last_bar) : ""))),
    pills,
    s.latch ? h("p", { class: "lv-pill-note muted small" }, icon("info", 14), LATCH_TIP) : null);
}

// ---------- позиция
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
    const plan = s.paper ? (s.paper_plan as { target?: number; price?: number; side?: string; qty?: number; stop?: number } | null) : null;
    const next = t == null ? "Целевая экспозиция неизвестна — бот определит её на ближайшем закрытии свечи."
      : Math.abs(t) < 1e-9 ? "Целевая экспозиция 0: бот остаётся вне рынка, пока сигналы не изменятся."
        : s.latch ? `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×, но включена защёлка (latch): бот войдёт, только когда сигнал изменится.`
          : s.enabled === false ? `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×, но торговля выключена: ордера не отправляются.`
            : `Цель — ${t > 0 ? "лонг" : "шорт"} ${num(Math.abs(t), 2)}×: бот откроет позицию на ближайшей проверке.`;
    return h("section", { class: "card lv-pos is-flat", "aria-label": "Позиция" }, head(h("span", { class: "lv-side flat" }, h("span", { class: "lv-side-g", "aria-hidden": "true", text: "■" }), "FLAT")),
      h("div", { class: "lv-flat" },
        h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("flat", 22)),
        h("p", { class: "strong", text: "Позиции нет — бот вне рынка" }),
        h("p", { class: "muted", text: next }),
        plan && plan.side ? h("p", { class: "muted small lv-plan", text: `Бумажный план: ${plan.side === "long" ? "лонг" : "шорт"} ${num(plan.qty, 4)} BTC по ${num(plan.price, 2)}${plan.stop ? `, стоп ${num(plan.stop, 2)}` : ""}` }) : null));
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

// ---------- цель и сигналы
/** Раскрыт ли блок «Неактивные ноги»: вкладка перерисовывается на каждый новый снимок, состояние хранит вызывающий. */
export interface IdleState { open: boolean; onToggle(open: boolean): void }

export function signalsCard(s: LiveSnapshot, meta: Meta | null, now: number, idleState?: IdleState): HTMLElement {
  const t = s.target_exposure;
  const word = t == null ? "нет данных" : Math.abs(t) < 0.005 ? "вне рынка" : t > 0 ? "лонг" : "шорт";
  const valueText = t == null ? "нет данных" : `${t > 0 ? "+" : ""}${num(t, 2)}× — ${word}`;
  const p = t == null ? 0.5 : gaugePos(t);
  const fillCls = t == null ? "" : t > 0 ? "long" : "short";
  const gauge = h("div", { class: "lv-gauge" },
    h("div", { class: "lg-top" }, h("span", { class: "section-label", text: "Целевая экспозиция" }),
      h("span", { class: `lg-val ${fillCls}` }, t == null ? "—" : `${t > 0 ? "+" : ""}${num(t, 2)}×`, h("span", { class: "lg-word", text: ` ${word}` }))),
    h("div", { class: "lg-track", role: "meter", "aria-label": "Целевая экспозиция", "aria-valuemin": -1, "aria-valuemax": 1, "aria-valuenow": t == null ? 0 : Math.max(-1, Math.min(1, t)), "aria-valuetext": valueText },
      h("i", { class: "lg-zero", "aria-hidden": "true" }),
      t == null ? null : h("i", { class: `lg-fill ${fillCls}`, "aria-hidden": "true", style: p >= 0.5 ? `left:50%;width:${(p - 0.5) * 100}%` : `left:${p * 100}%;width:${(0.5 - p) * 100}%` }),
      t == null ? null : h("i", { class: `lg-marker ${fillCls}`, "aria-hidden": "true", style: `left:${p * 100}%` })),
    h("div", { class: "lg-scale", "aria-hidden": "true" }, h("span", { text: "−1 шорт" }), h("span", { text: "0" }), h("span", { text: "+1 лонг" })));

  const baseTf = meta?.base_tf || "1h";
  const nb = nextClose(baseTf, now);
  const nextBar = nb == null ? null : h("div", { class: "lv-nextbar" }, icon("clock", 15),
    h("span", { class: "muted", text: `Следующая свеча ${baseTf} через` }), tk("cd", nb, h("b", { class: "mono" })),
    h("span", { class: "muted small", text: `в ${new Date(nb).toISOString().slice(11, 16)} UTC` }));

  const rows = legRows(s.legs, now);
  const { active, idle } = splitLegs(rows);
  const tot = legTotals(active);
  const list = !rows.length ? h("p", { class: "muted", text: "Нет данных о ногах." })
    : !active.length ? h("p", { class: "muted", text: "Нет работающих стратегий — целевая экспозиция 0, бот вне рынка." })
    : h("ul", { class: "lv-legs", "aria-label": "Ноги бота" },
    h("li", { class: "lv-leg head", "aria-hidden": "true" }, h("span", { text: "Нога" }), h("span", { text: "Вес" }), h("span", { text: "Сигнал" }), h("span", { text: "Вклад" }), h("span", { text: "Закрытие" })),
    ...active.map((r) => {
      const w = signalWord(r.signal);
      const sigText = { long: `${SIDE_GLYPH.long} лонг`, short: `${SIDE_GLYPH.short} шорт`, flat: "■ вне рынка", none: "— нет" }[w];
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-leg" },
        h("span", { class: "lv-leg-id" },
          h("span", { class: "lv-leg-name" }, h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf })),
          h("span", { class: "lv-leg-sub muted small" }, params ? h("span", { class: "mono", text: params }) : null, r.updated ? h("span", {}, " · сигнал ", tk("age", r.updated)) : null)),
        h("span", { class: "lv-leg-w", "data-l": "Вес", text: `${num(r.weight * 100, 0)}%` }),
        h("span", { class: `lv-leg-sig ${w}`, "data-l": "Сигнал", text: sigText }),
        h("span", { class: "lv-leg-c", title: "Вклад в целевую экспозицию (сигнал × вес): направление, а не прибыль", "data-l": "Вклад", text: r.contribution == null ? "—" : `${r.contribution > 0 ? "+" : ""}${num(r.contribution, 2)}` }),
        h("span", { class: "lv-leg-cd mono", "data-l": "Закрытие" }, r.nextClose ? tk("cd", r.nextClose) : "—"));
    }));

  // неактивные ноги (вес 0 / выключены) — свёрнуто под таблицей
  const idleBox = !idle.length ? null : h("details", { class: "lv-idle", open: !!idleState?.open },
    h("summary", { text: `Неактивные ноги (${idle.length})` }),
    h("ul", { class: "lv-idle-list", "aria-label": "Неактивные ноги" }, ...idle.map((r) => {
      const params = Object.entries(r.params).map(([k, v]) => `${k}=${v}`).join(" ");
      return h("li", { class: "lv-idle-leg" },
        h("b", { text: r.name }), h("span", { class: "chip-tf", text: r.tf }),
        h("span", { class: "badge off", text: r.enabled ? "не торгуется" : "выключена", title: r.enabled ? "Вес 0 — сигнал считается, но в целевую экспозицию не входит" : "Нога выключена" }),
        params ? h("span", { class: "mono muted small", text: params }) : null);
    })));

  idleBox?.addEventListener("toggle", () => idleState?.onToggle(idleBox.open));
  return h("section", { class: "card lv-signals", "aria-label": "Цель и сигналы" },
    h("header", { class: "card-head" }, h("h3", { text: "Цель и сигналы" })),
    gauge, nextBar, list, idleBox,
    active.length ? h("p", { class: "lv-foot muted small", text: `Σ весов ${num(tot.weights * 100, 0)}% · Σ вкладов ${tot.contribution > 0 ? "+" : ""}${num(tot.contribution, 2)}${t != null ? ` · цель ${t > 0 ? "+" : ""}${num(t, 2)}×` : ""}. Время закрытия — по границам свечей UTC.` }) : null,
    h("p", { class: "lv-hint muted small", text: "Добавить или убрать стратегию: Telegram → 🧩 Стратегии → ➕ Добавить, либо кнопка «В бота» в Лаборатории, Рейтинге и Портфеле." }));
}

// ---------- лимиты риска
const RISK_TEXT = { ok: "в норме", warn: "близко к лимиту", bad: "критично" } as const;
const RISK_G = { ok: "✓", warn: "!", bad: "✕" } as const;

function riskRow(label: string, hint: string, value: number | null, limit: number | null | undefined): HTMLElement {
  const m = riskMeter(value, limit);
  if (!m || value == null || limit == null) {
    return h("div", { class: "lv-risk" }, h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }), h("span", { class: "muted small", text: "нет данных" })), h("div", { class: "meter", "aria-hidden": "true" }));
  }
  const vt = `${pct(value, 1)} из ${pct(limit, 0)} (${num(m.ratio * 100, 0)}% лимита) — ${RISK_TEXT[m.tone]}`;
  return h("div", { class: `lv-risk ${m.tone}` },
    h("div", { class: "lv-risk-top" }, h("span", { class: "lv-risk-l", text: label }),
      h("span", { class: `lv-risk-state ${m.tone}` }, h("span", { class: "g", "aria-hidden": "true", text: RISK_G[m.tone] }), ` ${RISK_TEXT[m.tone]}`)),
    meterBar({ label, value: m.ratio, max: 1, tone: m.tone, valueText: vt, marks: [0.6, 0.85] }),
    h("div", { class: "lv-risk-bot" },
      h("span", {}, h("b", { text: pct(value, 1) }), h("span", { class: "muted", text: ` из ${pct(limit, 0)}` })),
      h("span", { class: "muted small", text: m.left > 0 ? `запас до kill-switch ${num(m.left * 100, 1)} п.п.` : "лимит достигнут" })),
    h("p", { class: "muted small lv-risk-hint", text: hint }));
}

/** Доля без лишних знаков: 40 % -> «40%», 37,5 % -> «37,5%». */
const pctShort = (x: number): string => pct(x, Math.abs(x * 100 - Math.round(x * 100)) < 0.05 ? 0 : 1);

/**
 * Предупреждение «стоп × плечо × веса»: один сработавший биржевой стоп съедает долю счёта не меньше меньшего из лимитов kill-switch.
 * Нет настроек или ног — null (строки просто нет).
 */
export function stopRiskNote(s: LiveSnapshot): HTMLElement | null {
  const w = stopRiskWarning(s.settings, s.legs);
  if (!w) return null;
  const cmp = w.risk > w.limit + 1e-9 ? "больше лимита" : "равно лимиту";
  return h("p", { class: "notice warn lv-warn lv-stoprisk", role: "note" },
    h("span", {}, h("strong", { text: "Стоп сильнее лимита риска. " }),
      `Стоп ${pctShort(w.stopPct)} при плече ${num(w.leverage, 1)}× и весах ${pctShort(w.weights)} ≈ ${pctShort(w.risk)} счёта за одну сделку — ${cmp} (${w.limitLabel} ${pctShort(w.limit)}): сначала сработает биржевой стоп, затем kill-switch.`));
}

export function riskCard(s: LiveSnapshot, eq: readonly Pt[], now: number): HTMLElement {
  const st = s.settings ?? {};
  const equity = s.equity ?? eq[eq.length - 1]?.v ?? null;
  const dd = drawdownFrac(equity, s.peak_equity);
  const today = plToday(eq, s.equity, now);
  const loss = today ? Math.max(0, -(today.pct ?? 0)) : null;
  const chip = (k: string, v: string, tip?: string) => h("span", { class: "chip", title: tip ?? "" }, h("span", { class: "muted", text: k }), h("b", { text: v }));
  return h("section", { class: "card lv-risks", "aria-label": "Лимиты риска" },
    h("header", { class: "card-head" }, h("h3", { text: "Лимиты риска" }), h("span", { class: "muted small", text: "расстояние до kill-switch" })),
    h("div", { class: "lv-risk-grid" },
      riskRow("Просадка от пика", s.peak_equity != null ? `Считается от пика кошелька (${usd(s.peak_equity)}); срабатывает при просадке не меньше лимита.` : "Считается от пика кошелька; срабатывает при просадке не меньше лимита.", dd, st.kill_dd),
      riskRow("Убыток за день (UTC)", today ? `Считается от кошелька на начало суток UTC (${usd(today.base)}).` : "За текущие сутки UTC ещё нет точек кошелька.", loss, st.kill_daily)),
    stopRiskNote(s),
    h("div", { class: "lv-chips" },
      st.stop_pct != null ? chip("Стоп", pct(st.stop_pct, 0), "Биржевой стоп от цены входа") : null,
      st.leverage != null ? chip("Плечо", `${num(st.leverage, 1)}×${st.max_leverage != null ? ` из ${num(st.max_leverage, 0)}×` : ""}`, "Текущее плечо и потолок бота") : null));
}

// ---------- сделки
const dfShort = new Intl.DateTimeFormat("ru-RU", { timeZone: "UTC", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Что показывает блок сделок: ждём первый ответ /api/stats, закрытые сделки готовы, либо откат к списку ордеров (старый бэкенд / ошибка / нет статистики). */
export type ClosedState =
  | { kind: "loading" }
  | { kind: "ok"; res: StatsResponse }
  | { kind: "fallback"; why: "old_backend" | "error" | "unavailable"; message?: string };

/** Подсказка над списком ордеров, когда закрытые сделки получить нельзя. */
export function fallbackHint(st: Extract<ClosedState, { kind: "fallback" }>): string {
  if (st.why === "old_backend") return "Закрытые сделки с итогом в долларах появятся после перезапуска бота (run.bat): сервер дашборда запущен на старой версии. Пока показаны отдельные ордера.";
  if (st.why === "unavailable") return `Итоги закрытых сделок сейчас недоступны${st.message ? `: ${st.message}` : ""}. Пока показаны отдельные ордера.`;
  return `Не удалось загрузить закрытые сделки${st.message ? ` (${st.message})` : ""}. Пока показаны отдельные ордера; повтор через минуту.`;
}

/** Список отдельных ордеров (fills) — прежняя таблица «Последние сделки»; hint — пояснение, почему показаны ордера, а не итоги. */
export function tradesCard(trades: readonly LiveTrade[] | undefined, limit: number, onMore: () => void, hint: string | null = null): HTMLElement {
  const all = [...(trades ?? [])].sort((a, b) => +(parseTime(b.ts) ?? 0) - +(parseTime(a.ts) ?? 0));
  const shown = all.slice(0, limit);
  const body = shown.length ? h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": "Ордера бота" },
    h("table", { class: "data lv-trades" },
      h("thead", {}, h("tr", {}, ...[["Время", true, ""], ["Ордер", true, ""], ["Объём", false, ""], ["Цена", false, ""], ["Номинал", false, "lv-notional"], ["Причина", true, ""]].map(([t, l, c]) => h("th", { class: `${l ? "left" : ""} ${c}`.trim(), scope: "col" }, h("span", { class: "th-txt", text: String(t) }))))),
      h("tbody", {}, ...shown.map((t) => {
        const d = parseTime(t.ts);
        return h("tr", {},
          h("td", { class: "left", title: datetime(t.ts) }, h("time", { dateTime: d?.toISOString() ?? "" }, h("span", { text: ago(t.ts) }), h("span", { class: "lv-t-abs muted", text: d ? ` · ${dfShort.format(d).replace(",", "")}` : "" }))),
          h("td", { class: "left" }, orderBadge(isBuy(t.side))),
          h("td", { text: num(t.qty, 4) }), h("td", { text: num(t.price, 2) }), h("td", { class: "lv-notional", text: usd(t.qty * t.price, 2) }),
          h("td", { class: "left lv-reason", text: t.reason || "—" }));
      }))))
    : emptyState("Сделок пока нет", "Сделки появятся здесь после первого входа бота в рынок.", null);
  return h("section", { class: "card lv-trades-card", "aria-label": "Сделки" },
    h("header", { class: "card-head" }, h("h3", { text: "Последние сделки" }), h("span", { class: "muted small", text: all.length ? `${shown.length} из ${all.length}` : "" })),
    hint ? h("p", { class: "notice lv-trades-hint", role: "note" }, h("span", { text: hint })) : null,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: `Показать ещё ${Math.min(20, all.length - shown.length)}`, onClick: onMore }) : null);
}

export const CLOSED_RANGE_LABEL = "За 30 дн.";

/** Скелетон блока «Закрытые сделки» до первого ответа. */
export function closedTradesLoading(): HTMLElement {
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки", "aria-busy": "true" },
    h("header", { class: "card-head" }, h("h3", { text: "Закрытые сделки" })),
    h("div", { class: "skeleton skel-line", style: "height:34px;margin-bottom:8px" }), h("div", { class: "skeleton skel-line", style: "height:120px" }));
}

export interface ClosedOpts { limit: number; onMore(): void; fresh: ReadonlySet<string> }

const signedM = (v: number, strong = false): HTMLElement => {
  const t = toneOf(v, 0.005);
  return h("b", { class: `st-m ${t}${strong ? " strong" : ""}` }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, susd(v));
};

/** Шапка: «За 30 дн.: закрыто N · плюс +A $ · минус −B $ · итого ±C $» (+ ROI счёта за окно — отдельной приглушённой подписью). */
export function closedStrip(res: StatsResponse): HTMLElement | null {
  const sm = periodSummary(res);
  if (!sm) return null;
  const dot = () => h("span", { class: "lv-dot muted", "aria-hidden": "true", text: "·" });
  const roi = res.equity?.wallet_return_pct;
  return h("div", { class: "lv-strip", role: "group", "aria-label": "Итоги закрытых сделок за 30 дней" },
    h("span", { class: "lv-strip-l", text: `${CLOSED_RANGE_LABEL}:` }),
    h("span", {}, "закрыто ", h("b", { text: String(sm.count) })), dot(),
    h("span", { class: "lv-strip-p" }, "плюс ", signedM(sm.profit)), dot(),
    h("span", { class: "lv-strip-m" }, "минус ", signedM(sm.loss)), dot(),
    h("span", { class: "lv-strip-t" }, "итого ", signedM(sm.net, true)),
    roi != null ? h("span", { class: "lv-strip-roi muted small", title: "Только за 30 дней (не за всё время): изменение кошелька за 30 дней / кошелёк на начале окна (включает вводы и выводы). Не равно сумме сделок: учитывает открытую позицию и движения вне бота." }, `ROI счёта за 30 дн. ${spctU(roi, 2)}`) : null);
}

/** Строка открытой позиции — всегда первая: нереализованный результат (ещё не зафиксирован). */
function openRow(s: LiveSnapshot, res: StatsResponse): HTMLElement | null {
  const v = positionView(s.position, s.settings);
  if (!v) return null;
  const t = toneOf(v.upnl, 0.005);
  const frac = upnlAccountFrac(v.upnl, s.equity);
  const opened = res.open_position?.opened_ts ?? null;
  return h("tr", { class: "lv-open-row", "data-open": "1" },
    h("td", { class: "left" }, h("span", { class: "badge info lv-open-tag", text: "ОТКРЫТА", title: "Позиция ещё не закрыта: результат нереализованный и может измениться" }),
      opened ? h("div", { class: "lv-t-abs muted small" }, "с ", `${ddmm(opened)} ${hhmm(opened)} UTC`) : null),
    h("td", { class: "left" }, sideBadge(v.long ? "long" : "short")),
    h("td", { class: "lv-c-qty", text: num(v.qty, 4) }),
    h("td", { class: "lv-c-px", text: `${num(v.entry, 2)} → ${num(v.mark, 2)}`, title: "Вход → текущая цена (mark)" }),
    h("td", { class: `lv-res ${t}` }, h("div", { class: "lv-res-usd" }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, susd(v.upnl)),
      h("div", { class: "lv-res-pct muted small", text: frac != null ? `${spctU(frac * 100, 2)} счёта · не зафиксирован` : "не зафиксирован" })),
    h("td", { class: "lv-c-hold", text: opened ? fmtDuration(Date.now() / 1000 - opened) : "—" }),
    h("td", { class: "left lv-reason muted", text: "позиция ещё открыта" }));
}

const tkAge = (iso: string): HTMLElement => tk("age", iso, h("span"));

/** «Закрытые сделки»: итоговые суммы окна, открытая позиция сверху и закрытые round trips (новые за последний опрос подсвечены). */
export function closedTradesCard(s: LiveSnapshot, res: StatsResponse, o: ClosedOpts): HTMLElement {
  const all = [...(res.trades?.rows ?? [])].sort((a, b) => b.exit_ts - a.exit_ts || b.n - a.n);
  const shown = all.slice(0, o.limit);
  const open = openRow(s, res);
  const strip = closedStrip(res);
  const total = res.trades?.total ?? all.length;
  const head = h("header", { class: "card-head" }, h("h3", { text: "Закрытые сделки" }),
    h("span", { class: "muted small", text: all.length ? `${shown.length} из ${all.length}${res.trades?.truncated ? ` (всего в окне ${total})` : ""}` : "" }));
  if (!shown.length && !open) {
    return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки" }, head, strip,
      emptyState("Закрытых сделок за 30 дней нет", "Когда бот закроет позицию, здесь появится итог в долларах и в процентах счёта.", null));
  }
  const th = (t: string, left = false, c = "", tip = "") => h("th", { class: `${left ? "left" : ""} ${c}`.trim(), scope: "col", title: tip }, h("span", { class: "th-txt", text: t }));
  const body = h("div", { class: "table-wrap lv-trades-wrap", tabindex: 0, role: "region", "aria-label": "Закрытые сделки бота" },
    h("table", { class: "data lv-trades lv-closed-t" },
      h("thead", {}, h("tr", {},
        th("Закрыта (UTC)", true), th("Сторона", true), th("Объём", false, "lv-c-qty"), th("Вход → выход", false, "lv-c-px", "Средняя цена входа → цена выхода, USDT"),
        th("Итог, $ · % счёта", false, "lv-res-h", "Итог сделки в долларах (после комиссий и фандинга; по данным Bybit, а без них — оценка бота) и в процентах кошелька на входе"),
        th("Держалась", false, "lv-c-hold"), th("Причина", true))),
      h("tbody", {}, open, ...shown.map((r) => {
        const t = resultTone(r);
        const rt = resultText(r.net, r.ret_account_pct);
        const key = closedKey(r);
        const fresh = o.fresh.has(key);
        return h("tr", { class: `${t === "pos" ? "tr-win" : t === "neg" ? "tr-loss" : ""}${fresh ? " tr-new" : ""}`, "data-key": key },
          h("td", { class: "left", title: `${isoUtc(r.exit_ts)} (UTC)` },
            h("time", { dateTime: isoUtc(r.exit_ts) }, tkAge(isoUtc(r.exit_ts)), h("span", { class: "lv-t-abs muted", text: ` · ${ddmm(r.exit_ts)} ${hhmm(r.exit_ts)}` })),
            fresh ? h("span", { class: "badge ok lv-new-tag", text: "новая" }) : null),
          h("td", { class: "left" }, sideBadge(r.side)),
          h("td", { class: "lv-c-qty", text: num(r.qty, 4) }),
          h("td", { class: "lv-c-px", text: `${num(r.entry_price, 2)} → ${num(r.exit_price, 2)}` }),
          h("td", { class: `lv-res ${t}` }, h("div", { class: "lv-res-usd" }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, rt.usd),
            h("div", { class: "lv-res-pct muted small", title: r.ret_account_pct == null ? "Нет точки капитала на входе в сделку — процент от счёта посчитать нельзя" : `Кошелёк на входе ≈ ${usd(r.equity_at_entry, 2)}`, text: rt.pct })),
          h("td", { class: "lv-c-hold", text: fmtDuration(r.hold_s) }),
          h("td", { class: "left lv-reason" }, reasonCell(r)));
      }))));
  return h("section", { class: "card lv-trades-card lv-closed", "aria-label": "Закрытые сделки" }, head, strip,
    body,
    all.length > shown.length ? h("button", { class: "btn ghost sm lv-more", type: "button", text: `Показать ещё ${Math.min(20, all.length - shown.length)}`, onClick: o.onMore }) : null,
    h("p", { class: "muted small lv-closed-note" }, "Итог — по данным Bybit; «оценка» — рассчитано ботом, точнее после обновления фандинга; «реконструкция» — закрытие по стопу на бирже, цена выхода оценена. Проценты разных сделок не складываются: суммы — в долларах."));
}

// ---------- события
const EV_ICON: Record<string, string> = { info: "info", trade: "swap", signal: "pulse", warn: "warn", error: "error", daily: "chart" };
const EV_WORD: Record<string, string> = { info: "Инфо", trade: "Сделка", signal: "Сигнал", warn: "Внимание", error: "Ошибка", daily: "Итоги дня" };

export function eventsList(events: readonly LiveEvent[] | undefined, mode: EventFilter, limit = 60): HTMLElement {
  const list = filterEvents(events, mode);
  if (!list.length) return emptyState(mode === "all" ? "Событий нет" : "В этой категории событий нет", undefined, null);
  return h("ol", { class: "lv-timeline", tabindex: 0, "aria-label": "События бота" }, ...list.slice(0, limit).map((e) => {
    const k = eventKind(e.level);
    const noise = isNoiseEvent(e.text);
    return h("li", { class: `lv-ev ${k}${noise ? " noise" : ""}` },
      h("span", { class: "lv-ev-ic", title: EV_WORD[k] }, icon(EV_ICON[k] ?? "info", 14), h("span", { class: "sr-only", text: EV_WORD[k] })),
      h("div", { class: "lv-ev-body" },
        h("div", { class: "lv-ev-meta" }, h("span", { class: "lv-ev-kind", text: EV_WORD[k] }), h("time", { dateTime: parseTime(e.ts)?.toISOString() ?? "", title: datetime(e.ts), text: `${ago(e.ts)} · ${datetime(e.ts)}` })),
        h("div", { class: "lv-ev-text", text: e.text })));
  }));
}

export function noSnapshot(reason?: string): HTMLElement {
  return h("div", { class: "lv-empty" },
    emptyState("Бот ещё не запускался", "Снимок состояния недоступен. Бот записывает своё состояние в файл storage/state/bot.db, а эта вкладка только читает его — данные появятся здесь сами в течение 10 секунд после запуска (python -m tradebot).", null, { big: true, icon: "power" }),
    reason ? h("p", { class: "muted small lv-reason-line" }, "Код причины: ", h("code", { text: reason })) : null);
}
