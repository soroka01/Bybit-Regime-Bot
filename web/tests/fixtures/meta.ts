// Общая метаинформация сервера для интеграционных тестов вкладок (достаточна для Лаборатории, Портфеля, Walk-forward, Рейтинга, Live, Статистики).
import type { Meta } from "../../src/types";

const param = { min: 2, max: 50, step: 1, default: 12, kind: "int", grid: [5, 12, 20] };

export const testMeta = {
  strategies: [
    { name: "EMA cross", desc: "ema", derivative: false, params: { fast: param } },
    { name: "OI flush", desc: "oi", derivative: true, params: { n: param } },
  ],
  timeframes: ["1h", "4h"], symbols: ["BTCUSDT", "ETHUSDT"], available: { BTCUSDT: ["1h", "4h"] }, klines_available: { BTCUSDT: ["1h", "4h"] },
  leaderboard_tfs: ["1h", "4h"], portfolio_tfs: ["1h", "4h"], base_tf: "1h", timeframe_minutes: { "1h": 60, "4h": 240 }, history_days: { "1h": 1095, "4h": 1095 },
  fetch_estimate: {}, walkforward_levs: [1, 2],
  defaults: { fee: 0.00055, slippage: 0.0002, days: 365, leverage: 1 }, overlay_defaults: { htf: 50, stop: 3 },
  legs: [
    { strategy: "EMA cross", tf: "1h", params: { fast: 12 }, weight: 0.5, long_only: false },
    { strategy: "OI flush", tf: "4h", params: { n: 10 }, weight: 0.5, long_only: true },
  ],
} as unknown as Meta;
