// Вкладка «Статистика»: построители DOM для плиток, карточек, предупреждений и состояний (ответ -> элемент). Без сети и состояния вкладки.
// Логика вынесена в statsLogic.ts (тесты — tests/statsLogic.test.ts). Все тексты — по-русски; тексты сервера — только через textContent.
import type { StatsPositionSide, StatsResponse, StatsTradeRow } from "../types";
import { h } from "../ui/dom";
import { badge, disclosure, emptyState, glyph } from "../ui/kit";
import { icon } from "../ui/icons";
import { sideBadge } from "../ui/side";
import { int, num, usd } from "../ui/fmt";
import {
  agoMs, cmpView, defText, fp, fsp, fsusd, FUNDING_HINT, feeNote, heroKpis, miniKpis, moneyLine, openAccountPct, rangeTitle,
  RECON_REASON, stamp, toneOf, trimMessage, unavailableCopy, warningChips, ymdRu, type CmpView, type Kpi, type MoneySummary, type WarnChip,
} from "./statsLogic";

// ---------------------------------------------------------------- мелкие элементы
/** Значок «i» с определением метрики (фокусируемый; текст — в title и aria-label). */
export const infoMark = (text: string): HTMLElement | null =>
  text ? h("span", { class: "st-info", tabindex: 0, role: "img", "aria-label": text, title: text, text: "i" }) : null;

/** Пометка «оценка»: комиссии, фандинг и реконструированные сделки считаются приблизительно (у сделок с данными Bybit её нет). */
export const estMark = (title = "Оценка: комиссии = объём × ставка тейкера, фандинг по локальным данным; точность ниже, чем у данных биржи"): HTMLElement =>
  h("span", { class: "st-est", title, text: "оценка" });

export { sideBadge };

/** Строка денег по набору сделок: «Закрыто N: заработано ▲ +A $, потеряно ▼ −B $, итого ▲ ±C $». Числа красятся ТОЛЬКО по знаку P/L. */
export function moneyStrip(s: MoneySummary, label = "Закрыто"): HTMLElement {
  if (!s.count) return h("p", { class: "st-money muted", "data-n": "0", text: "Закрытых сделок нет" });
  const m = (v: number, strong = false) => {
    const t = toneOf(v, 0.005);
    return h("b", { class: `st-m ${t}${strong ? " strong" : ""}` }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, fsusd(v));
  };
  return h("p", { class: "st-money", "data-n": String(s.count), "aria-label": moneyLine(s, label) },
    `${label} ${s.count}: заработано `, m(s.profit), ", потеряно ", m(s.loss), ", итого ", m(s.net, true));
}

const signed = (tone: string, text: string): string => `${glyph(tone)} ${text}`.trim();

/** Причина закрытия + бейджи «реконструкция» / «оценка» (общая для таблицы статистики и блока закрытых сделок на вкладке live).
 *  «оценка» — ТОЛЬКО когда итог сделки рассчитан ботом (exact=false); итог по данным Bybit (exact=true) без неё. */
export function reasonCell(r: StatsTradeRow): HTMLElement {
  return h("span", { class: "st-reason" }, h("span", { class: "st-reason-t", text: r.reason || "—", title: r.reason }),
    r.reconstructed ? h("span", { class: "badge warn has-glyph st-mini-badge", title: "Закрытие по стопу/ликвидации на бирже в журнал не пишется: цена выхода реконструирована по событию «Позиция исчезла»" }, h("span", { class: "g", "aria-hidden": "true", text: "!" }), "реконструкция") : null,
    !r.exact ? h("span", { class: "badge off st-mini-badge", title: r.reconstructed ? "Цена выхода и итог — оценка бота" : "Итог рассчитан ботом (комиссия × ставка тейкера, фандинг по локальным данным); станет точным по данным Bybit после обновления фандинга" }, "оценка") : null);
}

// ---------------------------------------------------------------- дельта к предыдущему окну
function cmpChip(v: CmpView | null): HTMLElement | null {
  if (!v) return null;
  if (v.kind === "na") return h("span", { class: "delta na", title: v.title, role: "img", "aria-label": `Сравнение с предыдущим окном: ${v.title}`, text: "—" });
  const g = v.dir === "up" ? "▲" : v.dir === "down" ? "▼" : "=";
  return h("span", { class: `delta ${v.good}`, title: v.title }, h("span", { class: "g", "aria-hidden": "true", text: g }), v.text);
}

// ---------------------------------------------------------------- плитки
export function kpiTile(k: Kpi, res: StatsResponse, hero: boolean): HTMLElement {
  const info = infoMark(defText(res.definitions, k.def));
  const chip = hero ? cmpChip(cmpView(res, k.cmp)) : null;
  const val = h("div", { class: `metric-value ${k.tone}${k.why ? " muted-val" : ""}`, title: k.why ?? "" },
    k.tone && k.value !== "—" ? h("span", { class: "g", "aria-hidden": "true", text: glyph(k.tone) }) : null, k.value);
  return h("div", { class: `metric big st-tile kpi-${k.key}${k.key === "roi" ? " accent-edge" : ""}`, "data-kpi": k.key },
    h("div", { class: "metric-top" }, h("div", { class: "st-lab" }, h("span", { class: "metric-label", text: k.label }), info, k.est ? estMark() : null), chip),
    val,
    k.sub ? h("div", { class: "metric-sub", text: k.sub }) : null);
}

export function heroGrid(res: StatsResponse): HTMLElement {
  const cmpNote = res.comparison?.available && res.comparison.prev_start && res.comparison.prev_end
    ? `▲▼ — изменение к предыдущему окну такой же длины (${stamp(res.comparison.prev_start).slice(0, 8)} – ${stamp(res.comparison.prev_end).slice(0, 8)}). Зелёный/синий — лучше, оранжевый — хуже.` : null;
  return h("div", { class: "st-hero-wrap" },
    h("div", { class: "st-hero" }, ...heroKpis(res).map((k) => kpiTile(k, res, true))),
    cmpNote ? h("p", { class: "muted small st-cmp-note", text: cmpNote }) : null);
}

export function miniGrid(res: StatsResponse): HTMLElement {
  return h("div", { class: "minis st-minis" }, ...miniKpis(res).map((k) =>
    h("div", { class: "mini", "data-kpi": k.key, title: k.why ?? "" },
      h("div", { class: "st-lab" }, h("span", { class: "metric-label", text: k.label }), infoMark(defText(res.definitions, k.def)), k.est ? estMark() : null),
      h("div", { class: `mini-val ${k.tone}${k.why ? " muted-val" : ""}` }, k.tone && k.value !== "—" ? h("span", { class: "g", "aria-hidden": "true", text: glyph(k.tone) }) : null, k.value),
      k.sub ? h("div", { class: "metric-sub", text: k.sub }) : null)));
}

export const allMetrics = (res: StatsResponse): HTMLElement =>
  disclosure("Все метрики", [miniGrid(res)], { key: "stats.all", defaultOpen: window.innerWidth >= 1100 });

// ---------------------------------------------------------------- заголовок страницы: окно, качество данных, среда
export function windowLine(res: StatsResponse): HTMLElement | null {
  const w = res.window;
  if (!w) return null;
  const days = w.length_s / 86400;
  const len = days >= 1 ? `${num(days, days < 10 ? 1 : 0)} дн.` : `${num(w.length_s / 3600, 1)} ч`;
  const partial = res.equity?.starts_before_data || (w.effective_start - w.start) > 60;
  const parts: (string | HTMLElement)[] = [
    h("span", { class: "st-win-main" }, icon("clock", 14), `${rangeTitle(res.range)}: `, h("b", { text: `${ymdRu(w.start_date)} – ${ymdRu(w.end_date)}` }), ` (UTC, ${len})`),
  ];
  if (partial) {
    const eff = res.equity?.eq_start_ts ?? w.effective_start;
    parts.push(h("span", { class: "st-win-note", title: "Данные капитала начинаются позже начала окна: ROI и просадка считаются от первой точки." }, `данные с ${stamp(eff)} UTC`));
  }
  return h("div", { class: "st-window muted small" }, ...parts);
}

/** Строка чипов «Качество данных» + сворачиваемый список с полными текстами. */
export function qualityBlock(res: StatsResponse): HTMLElement {
  const chips = warningChips(res.warnings);
  if (!chips.length) {
    return h("div", { class: "st-quality ok" }, badge("Данные в порядке", "ok", { glyph: "✓", title: "Предупреждений о качестве данных нет" }));
  }
  const chip = (c: WarnChip) => badge(c.label, c.level === "warn" ? "warn" : "info", { glyph: c.level === "warn" ? "!" : "i", title: `${c.text}${c.hint ? "\n" + c.hint : ""}` });
  const nWarn = chips.filter((c) => c.level === "warn").length;
  const list = h("ul", { class: "st-qlist" }, ...chips.map((c) => h("li", { class: c.level },
    h("div", { class: "st-q-head" }, chip(c)),
    h("p", { class: "st-q-text", text: c.text }),
    c.hint ? h("p", { class: "st-q-hint muted small", text: c.hint }) : null)));
  return h("div", { class: "st-quality" },
    h("div", { class: "st-qchips", role: "list", "aria-label": "Качество данных" }, ...chips.map((c) => { const b = chip(c); b.setAttribute("role", "listitem"); return b; })),
    disclosure("Качество данных", [list], { key: "stats.quality", badge: nWarn ? `${nWarn} предупр.` : `${chips.length} заметок` }));
}

// ---------------------------------------------------------------- баннеры и заметки над содержимым
const posText = (p: StatsPositionSide | null | undefined): string => (p ? `${p.side === "long" ? "лонг" : "шорт"} ${num(p.qty, 4)} BTC` : "позиции нет");

export function pageNotices(res: StatsResponse): HTMLElement[] {
  const out: HTMLElement[] = [];
  const mm = res.position_mismatch;
  if (mm) {
    out.push(h("div", { class: "banner warn st-mismatch", role: "alert" },
      h("strong", { text: "Позиция по журналу сделок не совпадает с позицией на бирже. " }),
      h("span", {}, `Журнал сделок даёт: ${posText(mm.replayed)}; по данным бота (биржи): ${posText(mm.actual)}. `),
      h("p", { class: "st-mm-p" }, "Журнал сделок неполон: закрытия по стопу на бирже и неподтверждённые ордера в него не попадают. ",
        "Открытая позиция показана по данным бота, а сделки и сверка кошелька могут быть неточны — не опирайтесь на них, пока расхождение не исчезнет.")));
  }
  const n = res.kpis?.trades ?? 0;
  if (res.available && n === 0) {
    out.push(h("div", { class: "notice st-zero", role: "note" }, h("strong", { text: "За выбранный период закрытых сделок нет. " }),
      h("span", { text: "График капитала и показатели кошелька ниже построены; метрики сделок пока пустые — попробуйте диапазон шире." })));
  } else if (res.available && res.warnings.some((w) => w.code === "few_trades")) {
    out.push(h("div", { class: "notice warn st-few", role: "note" }, h("strong", { text: `Мало сделок (${n} из 10 нужных). ` }),
      h("span", { text: "Статистика пока ненадёжна: win rate, profit factor и Sharpe при такой выборке во многом случайны." })));
  }
  return out;
}

// ---------------------------------------------------------------- карточки
const card = (cls: string, title: string, body: (Node | null)[], head?: (Node | null)[], label = title): HTMLElement =>
  h("section", { class: `card ${cls}`, "aria-label": label }, h("header", { class: "card-head" }, h("h3", { text: title }), ...(head ?? [])), ...body);

const kv = (label: string, value: string | Node, tone = "", extra?: Node | null, tip?: string): HTMLElement =>
  h("div", { class: "st-kv" }, h("dt", { title: tip ?? "" }, label, extra ?? null), h("dd", { class: tone }, value));

export function positionCard(res: StatsResponse): HTMLElement {
  const p = res.open_position;
  if (!p) {
    return card("st-pos is-flat", "Позиция сейчас", [h("div", { class: "st-flat" },
      h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("flat", 22)),
      h("p", { class: "strong", text: "Позиции нет — бот вне рынка" }),
      h("p", { class: "muted", text: "Новая позиция появится здесь сразу после входа бота; закрытые сделки — в таблице ниже." }))],
    [h("span", { class: "lv-side flat st-flat-tag" }, h("span", { "aria-hidden": "true", text: "■ " }), "FLAT")]);
  }
  const t = toneOf(p.upnl, 0.005), tn = toneOf(p.net_est, 0.005);
  const acct = openAccountPct(p.upnl, res.equity?.eq_end);      // % от кошелька сейчас (дополняет доллары)
  const opened = p.opened_ts
    ? h("span", {}, `${stamp(p.opened_ts)} UTC`, h("span", { class: "muted", text: ` · ${agoMs(p.opened_ts * 1000)}` }))
    : h("span", { class: "muted", text: "неизвестно (журнал не сходится)" });
  return card("st-pos", "Позиция сейчас", [
    h("div", { class: "st-pos-top" },
      h("div", {}, h("div", { class: "st-big", text: `${num(p.qty, 4)} BTC` }), h("div", { class: "muted small", text: `≈ ${usd(p.notional, 2)} номинал` })),
      h("div", { class: `st-pos-pnl ${t}` }, h("div", { class: "st-big" }, t ? h("span", { class: "g", "aria-hidden": "true", text: glyph(t) + " " }) : null, fsusd(p.upnl)),
        h("div", { class: "muted small st-pos-pct", text: acct != null ? `${fsp(acct, 2)} счёта` : "" }),
        h("div", { class: "muted small", text: "нереализованный P/L" }))),
    h("dl", { class: "st-kvs" },
      kv("Вход", num(p.entry, 2)), kv("Mark", num(p.mark, 2)),
      kv("Комиссия входа", usd(p.fees_paid_est, 2), "", estMark()),
      kv("Фандинг до сих пор", usd(p.funding_est, 2), "", estMark(p.funding_complete ? undefined : "Часть расчётов фандинга вне локальных данных — принята за 0")),
      kv("Net с учётом издержек", signed(tn, fsusd(p.net_est)), tn, estMark(), "Нереализованный P/L − комиссия входа − фандинг (оценка)"),
      kv("Открыта", opened)),
    p.funding_complete ? null : h("p", { class: "muted small st-note", text: "Фандинг неполный: данные о ставках кончились раньше, остаток принят за 0. " + FUNDING_HINT }),
  ], [p.side ? sideBadge(p.side) : null], "Позиция сейчас");
}

export function sideCard(res: StatsResponse): HTMLElement {
  const by = res.breakdown?.by_side ?? [];
  const get = (k: string) => by.find((x) => x.key === k);
  const l = get("long"), s = get("short");
  const total = (l?.count ?? 0) + (s?.count ?? 0);
  if (!total) return card("st-sides", "Лонг против шорта", [emptyState("Сделок в окне нет", "Разбивка появится, когда в выбранном периоде закроются сделки.", null, { icon: "swap" })]);
  const tile = (side: "long" | "short", g: typeof l) => {
    const n = g?.count ?? 0, t = toneOf(g?.net, 0.005);
    return h("div", { class: `st-side-tile ${side}` },
      h("div", { class: "st-side-top" }, sideBadge(side), h("span", { class: "muted small", text: `${n} ${plural(n)}` })),
      h("div", { class: `st-side-net ${t}` }, signed(t, fsusd(g?.net)), estMark()),
      h("div", { class: "st-side-sub" }, h("span", { class: "muted", text: "Win rate " }), h("b", { text: n ? fp(g?.win_rate_pct, 0) : "—" }),
        h("span", { class: "muted", text: " · в среднем " }), h("b", { text: n ? fsusd(g?.avg_net) : "—" })));
  };
  const lp = total ? ((l?.count ?? 0) / total) * 100 : 0;
  const bar = h("div", { class: "st-split", role: "img", "aria-label": `Сделок: лонг ${l?.count ?? 0}, шорт ${s?.count ?? 0}` },
    l?.count ? h("i", { class: "long", style: `width:${lp}%` }) : null, s?.count ? h("i", { class: "short", style: `width:${100 - lp}%` }) : null);
  return card("st-sides", "Лонг против шорта", [
    h("div", { class: "st-side-grid" }, tile("long", l), tile("short", s)), bar,
    h("div", { class: "st-split-cap muted small" }, h("span", { text: `лонг ${l?.count ?? 0} (${num(lp, 0)}%)` }), h("span", { text: `шорт ${s?.count ?? 0} (${num(100 - lp, 0)}%)` }))]);
}
const plural = (n: number): string => (n % 10 === 1 && n % 100 !== 11 ? "сделка" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "сделки" : "сделок");

export function reasonCard(res: StatsResponse): HTMLElement {
  const rows = [...(res.breakdown?.by_reason ?? [])].sort((a, b) => b.count - a.count);
  if (!rows.length) return card("st-reasons", "Причины закрытия", [emptyState("Сделок в окне нет", undefined, null, { icon: "inbox" })]);
  const maxAbs = Math.max(1e-9, ...rows.map((r) => Math.abs(r.net ?? 0)));
  const tbl = h("table", { class: "data st-rt" },
    h("caption", { class: "sr-only", text: "Результат по причинам закрытия" }),
    h("thead", {}, h("tr", {},
      ...[["Причина", "left"], ["Сделок", ""], ["Win rate", ""], ["Net", "st-net-h"], ["Ср. сделка", ""]].map(([t, c]) => h("th", { class: c, scope: "col" }, h("span", { class: "th-txt", text: t }))))),
    h("tbody", {}, ...rows.map((r) => {
      const t = toneOf(r.net, 0.005);
      return h("tr", {},
        h("td", { class: "left st-reason-k" }, r.key || "—", r.key.includes("реконструкция") ? h("span", { class: "badge off st-mini-badge", text: "оценка", title: "Цена выхода реконструирована" }) : null),
        h("td", { text: int(r.count) }), h("td", { text: fp(r.win_rate_pct, 0) }),
        h("td", { class: `st-net ${t}` }, h("span", { class: "st-bar", "aria-hidden": "true" }, h("i", { class: t, style: `width:${Math.max(3, (Math.abs(r.net ?? 0) / maxAbs) * 100)}%` })), h("span", { class: "st-net-v", text: signed(t, fsusd(r.net)) })),
        h("td", { text: fsusd(r.avg_net) }));
    })));
  return card("st-reasons", "Причины закрытия", [h("div", { class: "table-wrap", tabindex: 0, role: "region", "aria-label": "Причины закрытия" }, tbl)],
    [h("span", { class: "muted small", text: "группа = текст причины до первой запятой" })]);
}

export function costsCard(res: StatsResponse): HTMLElement {
  const k = res.kpis, c = res.config;
  const total = k && (k.fees_total != null || k.funding_total != null) ? (k.fees_total ?? 0) + (k.funding_total ?? 0) : null;
  const note = feeNote(c?.taker_fee_est);
  const stale = res.warnings.find((w) => w.code === "funding_stale");
  return card("st-costs", "Издержки", [
    h("dl", { class: "st-kvs" },
      kv("Комиссии", usd(k?.fees_total, 2), "", estMark(), defText(res.definitions, "fees_est")),
      kv("Фандинг (уплачено)", usd(k?.funding_total, 2), "", estMark(), defText(res.definitions, "funding_est")),
      kv("Итого издержек", usd(total, 2), "st-strong"),
      kv("Комиссии / валовая прибыль", fp(k?.fees_pct_of_gross_profit, 1), "", null, defText(res.definitions, "fees_pct_of_gross_profit")),
      kv("Оборот", usd(k?.turnover, 0), "", null, defText(res.definitions, "turnover")),
      kv("Ставка тейкера счёта", c ? `${num(c.taker_fee_est * 100, c.taker_fee_est * 100 < 0.1 ? 3 : 2)}%` : "—", "", null, "Реальная комиссия рыночных ордеров аккаунта Bybit")),
    note ? h("div", { class: "notice st-fee-note", role: "note" }, h("span", { text: note })) : null,
    stale ? h("p", { class: "muted small st-note", text: `Фандинг для ${stale.trades ?? "части"} сделок неизвестен и принят за 0. ${FUNDING_HINT}` }) : null,
  ]);
}

export function reconCard(res: StatsResponse): HTMLElement | null {
  const r = res.reconciliation;
  if (!r) return null;
  const head = [infoMark(defText(res.definitions, "reconciliation"))];
  if (r.unexplained == null) {
    return card("st-recon", "Сверка кошелька", [
      emptyState("Сверка недоступна", RECON_REASON[r.reason ?? ""] ?? "Не удалось сопоставить Δ кошелька с результатом бота.", null, { icon: "info" })], head);
  }
  const col = (label: string, val: number | null, hint: string, est = false) => {
    const t = toneOf(val, 0.005);
    return h("div", { class: "st-rc" }, h("div", { class: "st-rc-l muted", text: label }), h("div", { class: `st-rc-v ${t}` }, signed(t, fsusd(val))), est ? estMark() : null, h("div", { class: "st-rc-s muted small", text: hint }));
  };
  const op = (s: string) => h("div", { class: "st-op", "aria-hidden": "true", text: s });
  return card("st-recon", "Сверка кошелька", [
    h("div", { class: "st-recon-eq" },
      col("Δ кошелька", r.wallet_delta, "по таблице equity", false), op("−"),
      col("P/L бота", r.bot_pnl_est, "сделки + открытая позиция", true), op("="),
      col("Непояснённое", r.unexplained, "вне бота + ошибка оценок")),
    h("p", { class: "st-note muted" }, h("strong", { text: "Это не ошибка бота. " }),
      "Δ кошелька включает движение вне бота (вводы/выводы, другие монеты на счёте UTA) и ошибку оценок (комиссии, фандинг, реконструированные сделки). ",
      r.flat_at_start === false ? "На старте окна позиция была открыта — её вклад оценён по свече." : r.flat_at_start === true ? "На старте окна позиции не было." : ""),
  ], head);
}

// ---------------------------------------------------------------- состояния страницы
export function oldBackendCard(onRetry: () => void): HTMLElement {
  return h("section", { class: "card st-state st-old", role: "status", "aria-label": "Старая версия бота" },
    h("div", { class: "empty-icon", "aria-hidden": "true" }, icon("power", 26)),
    h("h3", { text: "Бот запущен на старой версии" }),
    h("p", { class: "strong", text: "Перезапустите run.bat, чтобы включить статистику." }),
    h("p", { class: "muted", text: "Сервер дашборда отвечает, но не знает про раздел /api/stats: он был запущен до обновления. После перезапуска вкладка заработает сама — настройки и данные не потеряются. Вкладка проверит снова через минуту." }),
    h("button", { class: "btn", type: "button", onClick: onRetry }, icon("refresh", 14), "Проверить снова"));
}

export function unavailableCard(reason: string, message: string, onRetry: () => void): HTMLElement {
  const u = unavailableCopy(reason);
  return h("section", { class: "card st-state st-unavail", role: "status", "aria-label": u.title, "data-reason": reason },
    h("div", { class: "empty-icon", "aria-hidden": "true" }, icon(u.icon, 26)),
    h("h3", { text: u.title }),
    trimMessage(u.title, message) ? h("p", { class: "strong", text: trimMessage(u.title, message) }) : null,
    h("p", { class: "muted", text: u.hint }),
    h("button", { class: "btn", type: "button", onClick: onRetry }, icon("refresh", 14), "Обновить"),
    h("p", { class: "muted small st-code" }, "Код причины: ", h("code", { text: reason })));
}
