// Вкладка «Бот (live)»: шапка — баннеры «не отвечает» / «остановлен» и герой-карточка (капитал, P/L, просадка, статусы).
import { sparkline } from "../charts/sparkline";
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { datetime, parseTime, pct, spct, susd, toneOf, usd } from "../ui/fmt";
import { icon } from "../ui/icons";
import { badge, deltaChip, sectionLabel } from "../ui/kit";
import { drawdownFrac, heartbeatState, plToday, rangeStart, sliceSeries, type Pt, type RangeKey } from "./liveCalc";
import { tk } from "./liveTick";
import { resolveWindow, todayLabel, type Feeds } from "./liveWindow";

export const LATCH_TIP = "Защёлка (latch): после срабатывания стопа или ручного закрытия бот не входит в рынок повторно, пока целевая экспозиция не изменится.";

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
