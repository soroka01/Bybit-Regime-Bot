// Отрисовка секций вкладки «Бот (live)»: чистые построители DOM (снимок -> элемент), без сетевых вызовов и состояния — barrel.
// Всё «живое» (обратный отсчёт, «N с назад») помечено data-tk и обновляется тикером из live.ts раз в секунду.
//   liveTick.ts      tk / tickAll
//   liveHero.ts      баннеры и герой
//   livePosition.ts  позиция и линейка цены
//   liveSignals.ts   цель и сигналы ног
//   liveRisk.ts      лимиты риска
//   liveTrades.ts    закрытые сделки / ордера
//   liveEvents.ts    события, «нет снимка»
export * from "./liveTick";
export * from "./liveHero";
export * from "./livePosition";
export * from "./liveSignals";
export * from "./liveRisk";
export * from "./liveTrades";
export * from "./liveEvents";
