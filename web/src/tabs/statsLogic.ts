// Вкладка «Статистика»: чистая логика — barrel. Реализация разнесена по модулям (без DOM и сети; тесты: tests/statsLogic.test.ts).
// Единицы в ответе: *_pct — проценты (1.5 = 1.5 %), время — unix-секунды UTC, деньги — USDT.
//   statsRange.ts      диапазоны окна
//   statsBuckets.ts    корзины P/L (подписи, значения)
//   statsTradeLogic.ts фильтры сделок, CSV
//   statsMoney.ts      суммы по закрытым сделкам, результат сделки
//   statsMarkers.ts    маркеры на графике капитала, единицы оси
//   statsKpi.ts        плитки KPI и сравнение с предыдущим окном
//   statsWarnings.ts   предупреждения, комиссия, причины сверки
//   statsPage.ts       состояние страницы
//   ../charts/calendarModel.ts, ../charts/histModel.ts, ../ui/fmtTime.ts — модели и форматирование, нужные и графикам
// Форматтеры живут в ui/fmt.ts; прежние имена оставлены как совместимые алиасы (тесты и внешние импорты).
// Проценты здесь — в «процентных» единицах ответа (1,5 = 1,5 %), поэтому pctU, а не pct.
export { agoMs, pctU as fp, spctU as fsp, susd as fsusd, toneOf } from "../ui/fmt";
export * from "../ui/fmtTime";
export * from "../charts/calendarModel";
export * from "../charts/histModel";
export * from "./statsRange";
export * from "./statsBuckets";
export * from "./statsTradeLogic";
export * from "./statsMoney";
export * from "./statsMarkers";
export * from "./statsKpi";
export * from "./statsWarnings";
export * from "./statsPage";
