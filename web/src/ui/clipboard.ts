// Копирование в буфер обмена (navigator.clipboard с запасным вариантом через textarea) + тост (ui/toast.ts).
import { api, asApiError } from "../api";
import { t } from "../i18n";
import { BOT_MAX_LEVERAGE } from "../leverage";
import type { BacktestResult, LegCfg, Market, Metrics, Overlays } from "../types";
import { h } from "./dom";
import { num, pct, spct } from "./fmt";
import { icon } from "./icons";
import { setLoading } from "./kit";
import { toast } from "./toast";

export { toast };

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { value: text, style: "position:fixed;opacity:0;top:0;left:0" });
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
    return ok;
  }
}

export async function copyWithToast(text: string): Promise<void> {
  const ok = await copyText(text);
  toast(ok ? t("common.copied") : t("common.copy_failed"), ok ? "ok" : "error");
}

/** Отправка текста «для бота» в Telegram-карточку (POST /api/bot/proposals) + тосты по исходам. */
export async function sendToBotWithToast(text: string): Promise<void> {
  try {
    const r = await api.proposeToBot(text);
    if (r.ok) {
      if (r.bot_alive) toast(t("common.bot.sent"), "ok");
      else toast(t("common.bot.saved"), "info");
    }
    if (r.errors.length) toast(t("common.bot.rejected", { errors: r.errors.join("; ") }), "error");
  } catch (e) {
    const err = asApiError(e);
    if (!err.silent) toast(err.message, "error");
  }
}

const py = (v: unknown): string =>
  typeof v === "boolean" ? (v ? "True" : "False") : typeof v === "string" ? JSON.stringify(v) : String(v);

const pyParams = (p: Record<string, number>): string =>
  "{" + Object.entries(p).map(([k, v]) => `${JSON.stringify(k)}: ${py(v)}`).join(", ") + "}";

export const pyLeg = (l: LegCfg, weight: number = l.weight): string =>
  `Leg(${JSON.stringify(l.strategy)}, ${JSON.stringify(l.tf)}, ${pyParams(l.params)}, weight=${weight}, long_only=${py(l.long_only)})`;

export const plainParams = (p: Record<string, number>): string =>
  Object.entries(p).map(([k, v]) => `${k}=${v}`).join(", ");

export function metricsLine(r: BacktestResult | null): string {
  return r ? metricsOf(r.metrics) : "нет расчёта";
}

export function metricsOf(m: Metrics): string {
  return `доход ${spct(m.total_return, 0)}, макс. просадка ${pct(m.max_drawdown, 0)}, Sharpe ${num(m.sharpe, 2)}, сделок ${Math.round(m.trades ?? 0)}`;
}

export function copyButton(label: string, build: () => string): HTMLButtonElement {
  return h("button", { class: "btn", type: "button", onClick: () => void copyWithToast(build()) }, icon("copy", 14), label);
}

/** «В бота»: как copyButton, но текст уходит в Telegram; на время запроса кнопка заблокирована (без двойных отправок). */
export function sendButton(label: string, build: () => string): HTMLButtonElement {
  const btn = h("button", {
    class: "btn", type: "button", title: t("common.bot.send_tip"),
    onClick: () => void sendGuarded(btn, build),
  }, icon("bot", 14), label);
  return btn;
}

/** Отправка из обработчика кнопки: на время запроса кнопка заблокирована, повторный клик игнорируется. */
export async function sendGuarded(btn: HTMLButtonElement, build: () => string): Promise<void> {
  if (btn.disabled) return;
  setLoading(btn, true);
  try { await sendToBotWithToast(build()); } finally { setLoading(btn, false); }
}

interface LabPayload {
  strategy: string; tf: string; params: Record<string, number>; symbol: string; longOnly: boolean;
  leverage: number; feePct: number; slipPct: number; days: number; overlays: Overlays; result: string;
  /** Рынок бэктеста (по умолчанию перп). Для спота плечо/лонг уже эффективные (1×, только лонг). */
  market?: Market;
}

/**
 * Строки-комментарии под данными для бота: спот (бот торгует только перп) и превышение потолка плеча бота.
 * ПРОТОКОЛ «для бота» (этот текст и labPayload ниже читает русскоязычный бот, src/tradebot/core/legspec.py) — по-русски при любом языке интерфейса.
 */
export function botNotes(market: Market, leverage: number): string[] {
  const out: string[] = [];
  if (market === "spot") out.push("# бот торгует только перп");
  if (leverage > BOT_MAX_LEVERAGE) out.push(`# бот ограничен ${BOT_MAX_LEVERAGE}× (плечо ${leverage}x в бэктесте недостижимо в боте)`);
  return out;
}

/** Метка рынка в тексте «для бота»: legspec.py сверяет её с «спот» — не зависит от языка интерфейса (market.ts marketLabel показывает интерфейсный текст). */
const botMarket = (m: Market): string => (m === "spot" ? "Спот" : "Перп");

/** Одинаковый текст для «Копировать для бота» в Лаборатории и в «Лучших стратегиях». */
export function labPayload(p: LabPayload): string {
  const ov = [p.overlays.htf ? `HTF EMA(${p.overlays.htf})` : "", p.overlays.stop ? `ATR-стоп ${p.overlays.stop}×` : ""]
    .filter(Boolean).join(", ") || "нет";
  const market = p.market ?? "perp";
  const line1 = `Стратегия: ${p.strategy} | Таймфрейм: ${p.tf} | Параметры: ${plainParams(p.params)} | Символ: ${p.symbol} | Рынок: ${botMarket(market)} | Только лонг: ${p.longOnly ? "да" : "нет"} | Плечо: ${p.leverage}x | Комиссия: ${+p.feePct.toFixed(4)}% | Проскальзывание: ${+p.slipPct.toFixed(4)}% | Период: ${p.days} дн. | Результат: ${p.result} | Оверлеи: ${ov}`;
  return [line1, pyLeg({ strategy: p.strategy, tf: p.tf, params: p.params, weight: 0.5, long_only: p.longOnly }), ...botNotes(market, p.leverage)].join("\n");
}
