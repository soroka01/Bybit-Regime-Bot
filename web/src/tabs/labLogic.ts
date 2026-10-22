// Чистая логика левой панели Лаборатории: подписи периодов и однострочные сводки свёрнутых секций.
import { t } from "../i18n";
import { num } from "../ui/fmt";

export const daysLabel = (d: number): string =>
  d >= 365 && d % 365 === 0 ? t("lab.days.year", { n: d / 365 }) : t("lab.days.short", { n: d });

const fee = (v: number): string => num(v, v < 0.1 ? 3 : 2);

export const marketSummary = (o: { symbol: string; tf: string; days: number; market: string }): string =>
  `${o.symbol} · ${o.tf} · ${daysLabel(o.days)}${o.market === "spot" ? ` · ${t("lab.summary.spot")}` : ""}`;

export const strategySummary = (name: string, changed: number, total: number): string =>
  total ? `${name} · ${changed ? t("lab.summary.changed", { changed, total }) : t("lab.summary.defaults")}` : name;

export const costsSummary = (o: { fee: number; slip: number; longOnly: boolean }): string =>
  t("lab.summary.costs", { fee: fee(o.fee), slip: fee(o.slip) }) + (o.longOnly ? ` · ${t("lab.summary.longOnly")}` : "");

export const leverageSummary = (lev: number, spot: boolean): string => (spot ? t("lab.summary.spotLev") : `${+lev.toFixed(2)}×`);

export const overlaysSummary = (o: { htfOn: boolean; htf: number; stopOn: boolean; stop: number }): string => {
  const parts = [o.htfOn ? `HTF EMA ${Math.round(o.htf)}` : "", o.stopOn ? t("lab.atrStop", { k: +o.stop.toFixed(1) }) : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : t("lab.summary.off");
};
