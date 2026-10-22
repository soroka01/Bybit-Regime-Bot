// Вкладка «Бот (live)»: шапка — баннеры «не отвечает» / «остановлен» и герой-карточка (капитал, P/L, просадка, статусы).
import { sparkline } from "../charts/sparkline";
import { t } from "../i18n";
import { killReasonLabel } from "../i18n/storedText";
import type { LiveSnapshot } from "../types";
import { h } from "../ui/dom";
import { datetime, parseTime, pct, spct, susd, toneOf, usd } from "../ui/fmt";
import { icon } from "../ui/icons";
import { heartbeatState } from "../ui/heartbeat";
import { badge, deltaChip, sectionLabel } from "../ui/kit";
import { drawdownFrac, plToday, rangeStart, sliceSeries, type Pt, type RangeKey } from "./liveCalc";
import { tk } from "./liveTick";
import { resolveWindow, todayLabel, type Feeds } from "./liveWindow";

const latchTip = (): string => t("live.latch_tip");

export function staleBanner(s: LiveSnapshot): HTMLElement | null {
  const hb = heartbeatState(s.heartbeat, Date.now());
  if (hb.tone === "ok") return null;
  const d = parseTime(s.heartbeat ?? null);
  return h("div", { class: `banner ${hb.tone === "bad" ? "error" : "warn"}`, role: "alert" },
    h("strong", { text: `${t("live.stale_t")} ` }),
    d ? h("span", {}, `${t("live.stale_last")} `, tk("age", s.heartbeat!), ` (${datetime(s.heartbeat)}). `) : h("span", { text: `${t("live.stale_none")} ` }),
    h("span", { text: t("live.stale_tail") }));
}

export function killedBanner(s: LiveSnapshot): HTMLElement | null {
  if (!s.killed) return null;
  return h("div", { class: "banner error", role: "alert" },
    h("strong", { text: `${t("live.killed")}. ` }),
    h("span", { text: s.kill_reason ? `${t("live.kill_reason", { reason: killReasonLabel(s.kill_reason) })} ` : "" }),
    h("span", { text: t("live.killed_tail") }));
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
  const sp = sparkline(sliceSeries(eq, rangeStart("24h", lastT)).map((p) => p.v), { w: 260, h: 64, label: t("live.spark_label") });

  const hbBadge = h("span", { class: `badge ${hb.tone === "bad" ? "bad" : hb.tone}${hb.tone === "ok" ? " live" : " has-glyph"}`, title: t("live.hb_title", { time: datetime(s.heartbeat) }) },
    hb.tone === "ok" ? null : h("span", { class: "g", "aria-hidden": "true", text: "!" }),
    `${hb.tone === "ok" ? t("live.alive") : t("live.no_heartbeat")} · `,
    s.heartbeat ? tk("age", s.heartbeat) : t("live.no_data"));

  const pills = h("div", { class: "badges lv-pills", role: "list", "aria-label": t("live.bot_status") },
    s.killed ? badge(t("live.killed"), "bad", { glyph: "✕" })
      : s.enabled ? badge(t("live.trading_on"), "ok", { live: true }) : badge(t("live.trading_off"), "off"),
    s.killed ? null : badge(t("live.ks_ok"), "ok", { glyph: "✓", title: t("live.ks_ok_tip") }),
    s.latch ? badge(t("live.latch_wait"), "warn", { glyph: "!", title: latchTip() }) : null,
    hbBadge);
  pills.querySelectorAll(".badge").forEach((b) => b.setAttribute("role", "listitem"));

  const edge = s.killed ? "--neg" : hb.tone !== "ok" ? (hb.tone === "bad" ? "--neg" : "--warn") : "";
  return h("section", { class: "card lv-hero accent-edge", "aria-label": t("live.equity"), style: edge ? `--edge: var(${edge})` : "" },
    h("div", { class: "lv-hero-main" },
      sectionLabel(t("live.wallet")),
      h("div", { class: "lv-equity", text: usd(equity) }),
      h("div", { class: "lv-pls" },
        today ? plChip(todayLabel(today, now), today.abs, today.pct, t("live.today_hint", { since: datetime(new Date(today.since).toISOString()), base: usd(today.base), cur: usd(today.cur) }))
          : h("div", { class: "lv-pl muted small", text: t("live.today_none") }),
        win.state === "loading" ? h("div", { class: "lv-pl lv-pl-loading muted small", role: "status", text: t("live.win_loading", { label: win.label }) })
          : win.pl ? plChip(win.label, win.pl.abs, win.pl.pct, win.title) : null)),
    h("div", { class: "lv-hero-spark", "aria-label": t("live.spark_aria") },
      sp ?? h("div", { class: "muted small", text: t("live.spark_few") }),
      sp ? h("div", { class: "lv-spark-cap muted small", text: t("live.spark_cap") }) : null),
    h("div", { class: "lv-hero-stats" },
      h("div", { class: "lv-stat" }, h("div", { class: "lv-stat-l", text: t("live.dd_peak") }),
        h("div", { class: `lv-stat-v ${dd != null && dd > 0.001 ? "neg" : ""}` }, dd != null && dd > 0.001 ? h("span", { class: "g", "aria-hidden": "true", text: "▼ " }) : null, dd == null ? "—" : dd > 0.001 ? pct(-dd, 1) : t("live.at_peak")),
        h("div", { class: "lv-stat-s muted", text: s.peak_equity != null ? t("live.peak", { v: usd(s.peak_equity) }) : "" })),
      h("div", { class: "lv-stat" }, h("div", { class: "lv-stat-l", text: t("live.last_candle") }),
        h("div", { class: "lv-stat-v sm", text: s.last_bar ? datetime(s.last_bar) : "—" }),
        h("div", { class: "lv-stat-s muted" }, s.last_bar ? tk("age", s.last_bar) : ""))),
    pills,
    s.latch ? h("p", { class: "lv-pill-note muted small" }, icon("info", 14), latchTip()) : null);
}
