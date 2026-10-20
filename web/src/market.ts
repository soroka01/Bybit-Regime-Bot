// Рынок бэктеста: деривативы (перп) или спот. Спот = плечо 1×, только лонг, без фандинга, комиссия по умолчанию 0.10 %.
// Данные те же (свечи перпа — приближение спота). Чистая логика без DOM (тесты: tests/market.test.ts); сервер нормализует
// запрос независимо (web/schemas.py), эти функции нужны, чтобы интерфейс показывал то же самое.
import type { Market } from "./types";

export type { Market };
export const MARKET_OPTIONS: { value: Market; label: string }[] = [
  { value: "perp", label: "Деривативы (перп)" },
  { value: "spot", label: "Спот" },
];
export const isMarket = (v: unknown): v is Market => v === "perp" || v === "spot";
/** Комиссия taker по умолчанию, % (реальный taker аккаунта; зеркало core.engine.DEFAULT_PERP_FEE / schemas): перп 0.10, спот 0.10. */
export const FEE_PCT: Record<Market, number> = { perp: 0.1, spot: 0.1 };
export const SPOT_APPROX_NOTE = "свечи перпа как приближение спота";
export const SPOT_DERIV_NOTE = "сигналы фандинга/OI/LSR считаются по данным перпа";
export const SPOT_LEV_NOTE = "спот: плечо 1×, ликвидации нет";
export const SPOT_LONG_NOTE = "спот: короткие позиции невозможны";
export const SPOT_RULES = "Спот: плечо 1×, только лонг, фандинг не начисляется, комиссия по умолчанию 0,10 %. Бот торгует только перп.";

export const effLeverage = (m: Market, lev: number): number => (m === "spot" ? 1 : lev);
export const effLongOnly = (m: Market, longOnly: boolean): boolean => (m === "spot" ? true : longOnly);
export const marketLabel = (m: Market): string => (m === "spot" ? "Спот" : "Перп");

export interface MarketUi { levDisabled: boolean; longLocked: boolean }
export const marketUi = (m: Market): MarketUi => ({ levDisabled: m === "spot", longLocked: m === "spot" });

export interface MarketFields { market: Market; fee: number; feeTouched: boolean }

/**
 * Смена рынка: поле комиссии переходит на значение по умолчанию нового рынка, если пользователь его не правил (feeTouched).
 * Мутирует состояние; возвращает true, если комиссия изменилась (интерфейсу нужно обновить поле).
 */
export function switchMarket(s: MarketFields, next: Market): boolean {
  s.market = next;
  if (s.feeTouched || s.fee === FEE_PCT[next]) return false;
  s.fee = FEE_PCT[next];
  return true;
}

/** Пояснение под переключателем рынка: для спота — что цены приближённые, а сигналы деривативных стратегий — от перпа. */
export function marketNote(m: Market, derivative: boolean): string {
  if (m !== "spot") return "";
  return `${SPOT_RULES} Данные: ${SPOT_APPROX_NOTE}.` + (derivative ? ` Эта стратегия: ${SPOT_DERIV_NOTE}.` : "");
}
