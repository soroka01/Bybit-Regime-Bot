// Чистая логика левой панели Лаборатории: подписи периодов и однострочные сводки свёрнутых секций.
import { num } from "../ui/fmt";

export const daysLabel = (d: number): string =>
  d >= 365 && d % 365 === 0 ? (d === 365 ? "1 год" : d < 1825 ? `${d / 365} года` : `${d / 365} лет`) : `${d} д`;

const fee = (v: number): string => num(v, v < 0.1 ? 3 : 2);

export const marketSummary = (o: { symbol: string; tf: string; days: number; market: string }): string =>
  `${o.symbol} · ${o.tf} · ${daysLabel(o.days)}${o.market === "spot" ? " · спот" : ""}`;

export const strategySummary = (name: string, changed: number, total: number): string =>
  total ? `${name} · ${changed ? `изменено ${changed} из ${total}` : "параметры по умолчанию"}` : name;

export const costsSummary = (o: { fee: number; slip: number; longOnly: boolean }): string =>
  `комиссия ${fee(o.fee)} % · проскальз. ${fee(o.slip)} %${o.longOnly ? " · только лонг" : ""}`;

export const leverageSummary = (lev: number, spot: boolean): string => (spot ? "1× · спот, без плеча" : `${+lev.toFixed(2)}×`);

export const overlaysSummary = (o: { htfOn: boolean; htf: number; stopOn: boolean; stop: number }): string => {
  const parts = [o.htfOn ? `HTF EMA ${Math.round(o.htf)}` : "", o.stopOn ? `ATR-стоп ${+o.stop.toFixed(1)}×` : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "выключены";
};
