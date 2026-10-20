// Типы ответов API (см. tradebot/web/service.py)

export type Num = number | null;
export type Market = "perp" | "spot";

/** Эффективные настройки расчёта (сервер): для спота плечо 1×, только лонг, комиссия. Для перпа в ответе не передаются. */
export interface MarketEffective {
  leverage: number;
  long_only: boolean;
  fee: number;
}

export interface ParamSpec {
  min: number;
  max: number;
  step: number;
  default: number;
  kind: "int" | "float";
  grid: number[];
}

export interface StrategyMeta {
  name: string;
  desc: string;
  derivative: boolean;
  params: Record<string, ParamSpec>;
}

export interface LegCfg {
  strategy: string;
  tf: string;
  params: Record<string, number>;
  weight: number;
  long_only: boolean;
}

export interface FetchEstimate { days: number; klines_requests: number; derivs_requests: number; requests: number; minutes: number }

export interface Meta {
  strategies: StrategyMeta[];
  timeframes: string[];
  symbols: string[];
  available: Record<string, string[]>;
  klines_available: Record<string, string[]>;
  leaderboard_tfs: string[];
  timeframe_minutes: Record<string, number>;
  history_days: Record<string, number>;
  fetch_estimate: Record<string, FetchEstimate>;
  portfolio_tfs: string[];
  base_tf: string;
  walkforward_levs: number[];
  defaults: { fee: number; slippage: number; days: number; leverage: number };
  legs: LegCfg[];
  overlay_defaults: { htf: number; stop: number };
}

export interface Overlays {
  htf: number | null;
  stop: number | null;
}

export interface BacktestBody {
  symbol: string;
  tf: string;
  strategy: string;
  params: Record<string, number>;
  days: number;
  fee: number;
  slippage: number;
  leverage: number;
  market: Market;
  long_only: boolean;
  overlays: Overlays;
}

export interface PortfolioBody {
  symbol: string;
  legs: LegCfg[];
  days: number;
  fee: number;
  slippage: number;
  leverage: number;
  market: Market;
}

export interface SweepAxis { name: string; min: number; max: number; n: number }
export interface SweepBody extends BacktestBody {
  x: SweepAxis;
  y?: SweepAxis; // нет y — одномерный профиль параметра x
}

/** Метрики бэктеста (core.engine). Первые 13 ключей есть всегда (в т.ч. IS/OOS/full подбора и точки развёртки);
 *  остальные — только в полном бэктесте/портфеле. Для развёртки с нулём сделок точка — `Partial<Metrics>` (пустой объект). */
export interface Metrics {
  total_return: Num;
  buy_hold_return: Num;
  cagr: Num;
  max_drawdown: Num;
  sharpe: Num;
  calmar: Num;
  trades: Num;
  win_rate: Num;
  avg_month: Num;
  median_month: Num;
  months_15plus: Num;
  green_months: Num;
  liquidations: Num;
  // только полный бэктест / портфель
  worst_month?: Num;
  profit_factor?: Num;
  avg_trade?: Num;
  sortino?: Num;
  exposure?: Num;
  bh_max_drawdown?: Num;
}

/** Метрика по строковому ключу (для динамических колонок/критериев); отсутствующая — null. */
export const metricAt = (m: Partial<Metrics> | null | undefined, key: string): Num => (m as Record<string, Num | undefined> | null | undefined)?.[key] ?? null;

export interface Marker {
  t: number;
  p: Num;
  side: "long" | "short";
  ret?: Num;
}

export interface TradeRow {
  entry: string;
  exit: string;
  side: "long" | "short";
  ret: Num;
  bars: number;
  entry_price: Num;
  exit_price: Num;
}

export interface BacktestResult {
  metrics: Metrics;
  data_range: { start: string; end: string; bars: number; step_min: number };
  t: number[];
  equity: Num[];
  bh: Num[];
  drawdown: Num[];
  price: Num[];
  markers: { entries: Marker[]; exits: Marker[]; truncated: boolean };
  monthly: { y: number; m: number; v: Num }[];
  yearly: { y: number; v: Num }[];
  trades: TradeRow[];
  trades_total: number;
  /** Только /api/backtest. */
  params?: Record<string, number>;
  days_effective?: number;
  /** Только /api/portfolio. */
  strips?: { t: number[]; exposure: Num[]; legs: Record<string, Num[]> };
  legs?: (LegCfg & { key: string })[];
  /** Только при market = "spot" (для перпа не передаётся). */
  market?: Market;
  effective?: MarketEffective;
}

export interface SweepResult {
  x: { name: string; values: number[] };
  y: { name: string; values: number[] } | null;
  /** 1D: все метрики в каждой точке x. */
  points: Partial<Metrics>[] | null;
  grids: Record<string, Num[][]>;
  base: Record<string, number>;
  market?: Market;
  effective?: MarketEffective;
}

export interface WfRow {
  tf: string;
  strategy: string;
  variant: string;
  windows: number;
  windows_positive: Num;
  total_return: Num;
  cagr: Num;
  buy_hold_return: Num;
  max_drawdown: Num;
  sharpe: Num;
  calmar: Num;
  trades: Num;
  win_rate: Num;
  profit_factor: Num;
  avg_month: Num;
  median_month: Num;
  months_15plus: Num;
  worst_month: Num;
  liquidations: Num;
}

export interface WfWindow {
  tf: string;
  strategy: string;
  variant: string;
  test_start: string;
  params: string;
  test_return: Num;
}

export interface Curve {
  t: number[];
  equity: Num[];
}

export interface LiveSnapshot {
  available: boolean;
  reason?: string;
  enabled?: boolean;
  killed?: boolean;
  kill_reason?: string | null;
  latch?: boolean | string | null;
  equity?: Num;
  peak_equity?: Num;
  position?: { side: string; qty: number; entry: number; upnl: number; mark: number } | null;
  target_exposure?: Num;
  last_bar?: string | null;
  heartbeat?: string | null;
  settings?: Record<string, Num>;
  legs?: {
    key: string; strategy: string; tf: string; params: Record<string, number>; long_only: boolean;
    weight: number; enabled: boolean; signal: Num; updated: string | null;
  }[];
  trades?: { ts: string; side: string; qty: number; price: number; reason: string; order_id: string }[];
  equity_history?: [string, Num][];
  events?: { ts: string; level: string; text: string }[];
}

/** Ответ POST /api/bot/proposals: сколько предложений принято ботом (карточка в Telegram) и какие отклонены. */
export interface ProposalResult { ok: boolean; added: number; errors: string[]; bot_alive: boolean }

export type Verdict = "robust" | "overfit" | "few" | "loss";
export type Criterion = "robust" | "calmar" | "sharpe" | "return";

export interface OptBody {
  symbol: string;
  tf: string;
  strategy: string;
  days: number;
  fee: number;
  slippage: number;
  leverage: number;
  market: Market;
  long_only: boolean;
  overlays: Overlays;
  criterion: Criterion;
  top: number;
}

export interface OptRow {
  params: Record<string, number>;
  score: Num;
  robust_score: Num;
  is: Metrics;
  oos: Metrics;
  full: Metrics;
  verdict: Verdict;
  /** Только у лучшей строки карточки рейтинга. Значения могут быть null (NaN на стороне сервера). */
  curve?: { t: number[]; equity: Num[]; bh: Num[] };
}

export interface Range { start: string; end: string; bars: number; days: number }

export interface OptResult {
  results: OptRow[];
  n_combos: number;
  n_valid: number;
  min_trades: number;
  criterion: Criterion;
  elapsed: number;
  is_range: Range;
  oos_range: Range;
  oos_days: number;
  short_oos: boolean;
  buy_hold_oos: number;
  param_specs: Record<string, ParamSpec>;
  days_effective: number;
  from_cache: boolean;
  market?: Market;
  effective?: MarketEffective;
}

export interface LbPlanItem { name: string; desc: string; derivative: boolean; ready: boolean; reason: string }
export interface LbPlan { symbol: string; tf: string; klines: boolean; derivs: boolean; strategies: LbPlanItem[] }

export interface LbBody {
  symbol: string;
  tf: string;
  strategy: string;
  days: number;
  fee: number;
  slippage: number;
  leverage: number;
  market: Market;
  long_only: boolean;
  variants: boolean;
  force?: boolean;
}

export interface LbItem {
  from_cache?: boolean;
  data_last?: string | null;
  computed_at?: string;
  strategy: string;
  desc: string;
  derivative: boolean;
  funding_included: boolean;
  result: OptRow | null;
  reason?: string;
  variant?: string;
  overlays?: Overlays;
  days_effective?: number;
  n_combos?: number;
  elapsed?: number;
  min_trades?: number;
  is_range?: Range;
  oos_range?: Range;
  oos_days?: number;
  short_oos?: boolean;
  buy_hold_oos?: number;
  market?: Market;
  effective?: MarketEffective;
}

export interface DataStatus {
  symbol: string;
  tf: string;
  exists: boolean;
  now: string;
  step_min: number;
  estimate: FetchEstimate;
  last: string | null;
  first: string | null;
  bars: number;
  behind_bars: number | null;
  expected_last: string | null;
  level: "ok" | "warn" | "bad";
  derivs: { funding: string | null; oi: string | null; lsr: string | null; granularity: string };
}

export interface UpdateResult {
  added: number;
  status: DataStatus;
  before_last: string | null;
  warning: string | null;
  /** Сколько устаревших записей кэша результатов удалено после обновления. */
  purged: number;
}

export interface LbCached {
  items: LbItem[];
  missing: string[];
  plan: LbPlan;
  days_effective: number;
  data_last: string | null;
  fingerprint: string;
}

// ---------------------------------------------------------------- GET /api/stats — статистика РЕАЛЬНОЙ торговли бота
// (src/tradebot/web/stats.py; определения метрик — в поле `definitions` ответа). Единицы: *_pct — проценты (1.5 = 1.5 %),
// время — unix-секунды UTC, деньги — USDT. NaN/inf в JSON не бывает (null). Эндпоинта может не быть (404), пока владелец не
// перезапустил бота с новым бэкендом.

export type StatsRange = "1h" | "1d" | "3d" | "7d" | "30d" | "90d" | "180d" | "365d" | "730d" | "1095d" | "all";

export type StatsWarningCode =
  | "starts_before_data" | "few_trades" | "funding_stale" | "candles_missing" | "position_mismatch"
  | "reconstructed_trades" | "equity_gaps" | "bad_fills";

/** Машинный код + русский текст; дополнительные поля зависят от кода (count, trades, total_s, …). */
export interface StatsWarning {
  code: StatsWarningCode;
  level: "warn" | "info";
  text: string;
  count?: number;
  trades?: number;
  total_s?: number;
  eq_start_ts?: number;
  funding_coverage_until?: Num;
  candles_until?: Num;
  error?: string | null;
  replayed?: StatsPositionSide | null;
  actual?: StatsPositionSide | null;
}

export interface StatsPositionSide { side: "long" | "short"; qty: number }

export interface StatsWindow {
  start: number;
  /** Начало периода, за который реально есть данные equity (= start, если данные покрывают окно). */
  effective_start: number;
  end: number;
  start_date: string;
  end_date: string;
  length_s: number;
}

export interface StatsConfig {
  symbol: string;
  /** Оценочная ставка тейкера (0.001 = 0.1 %). */
  taker_fee_est: number;
  leverage: number;
  stop_pct: number;
}

export interface StatsData {
  first_trade_ts: number;
  last_trade_ts: number;
  first_equity_ts: number | null;
  /** Последний расчёт фандинга в локальном parquet; после него фандинг неизвестен (считается 0). */
  funding_coverage_until: Num;
  candles_until: Num;
  fills: number;
  round_trips: number;
}

export interface StatsDay { date: string; ret_pct: Num }

export interface StatsEquity {
  eq_start: Num;
  eq_end: Num;
  eq_start_ts: number;
  eq_end_ts: number;
  starts_before_data: boolean;
  points: number;
  /** Δ кошелька за окно, USDT. */
  delta: Num;
  /** Заголовочный «ROI счёта»: Δ кошелька за окно / кошелёк на начале окна, % (= eq_end / eq_start - 1; вводы/выводы невидимы). */
  wallet_return_pct: Num;
  /** Максимальная просадка кошелька в окне, % (<= 0). */
  max_drawdown_pct: Num;
  max_drawdown_peak_ts: number;
  max_drawdown_trough_ts: number;
  daily_points: number;
  volatility_pct: Num;
  sharpe: Num;
  sortino: Num;
  calmar: Num;
  best_day: StatsDay | null;
  worst_day: StatsDay | null;
  gaps: { count: number; total_s: number };
}

export interface StatsTradeRef { n: number; net: Num; ret_pct: Num; exit_ts: number }

export interface StatsKpis {
  trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate_pct: Num;
  avg_win: Num;
  avg_loss: Num;
  payoff_ratio: Num;
  profit_factor: Num;
  expectancy_usdt: Num;
  expectancy_pct: Num;
  best_trade: StatsTradeRef | null;
  worst_trade: StatsTradeRef | null;
  hold_mean_s: Num;
  hold_median_s: Num;
  max_win_streak: number;
  max_loss_streak: number;
  net_pnl: Num;
  /** Σ Net прибыльных сделок окна, USDT. */
  profit_sum_usdt: Num;
  /** Σ Net убыточных сделок окна, USDT (≤ 0). profit_sum_usdt + loss_sum_usdt = net_pnl. */
  loss_sum_usdt: Num;
  gross_pnl: Num;
  gross_profit: Num;
  fees_total: Num;
  funding_total: Num;
  fees_pct_of_gross_profit: Num;
  turnover: Num;
  time_in_market_pct: Num;
  buy_hold_pct: Num;
}

export interface StatsOpenPosition {
  side: "long" | "short";
  qty: number;
  entry: Num;
  mark: Num;
  upnl: Num;
  notional: Num;
  /** null при position_mismatch (время открытия по журналу неизвестно). */
  opened_ts: number | null;
  fees_paid_est: Num;
  funding_est: Num;
  funding_complete: boolean;
  net_est: Num;
  heartbeat: string | null;
}

export interface StatsPositionMismatch { replayed: StatsPositionSide | null; actual: StatsPositionSide | null }

export interface StatsReconciliation {
  wallet_delta: Num;
  bot_pnl_est: Num;
  /** Δкошелька - оценка P/L бота; в основном внебот-движение кошелька и ошибка оценок. null — см. reason. */
  unexplained: Num;
  reason: "position_mismatch" | "no_start_price" | "no_start_trade" | null;
  flat_at_start: boolean | null;
}

export interface StatsCmpRow { cur: Num; prev: Num; delta: Num }

export interface StatsComparison {
  available: boolean;
  reason?: "range_too_short" | "all_range" | "no_previous_data";
  prev_start?: number;
  prev_end?: number;
  kpis?: Record<string, StatsCmpRow>;
  equity?: Record<string, StatsCmpRow>;
}

export interface StatsEquitySeries { t: number[]; v: Num[]; lo: Num[]; hi: Num[] }
export interface StatsDrawdownSeries { t: number[]; dd_pct: Num[] }
export interface StatsPnlBucket { t: number; n: number; wins: number; net: Num }
export interface StatsCalendarCell { date: string; net: Num; trades: number }
export interface StatsHistogram { edges: Num[]; counts: number[] }

export interface StatsSeries {
  /** Не более 600 точек: последняя точка бакета + min/max за бакет. */
  equity: StatsEquitySeries;
  drawdown: StatsDrawdownSeries;
  pnl_buckets: { kind: "5min" | "hour" | "day" | "week" | "month"; rows: StatsPnlBucket[] };
  calendar: StatsCalendarCell[];
  daily_returns: StatsDay[];
  /** Гистограмма доходностей сделок, %. */
  histogram: StatsHistogram;
}

export interface StatsGroupRow { key: string; count: number; win_rate_pct: Num; net: Num; avg_net: Num }

export interface StatsBreakdown { by_reason: StatsGroupRow[]; by_side: StatsGroupRow[] }

export interface StatsFill {
  /** id в trades; null у синтетического закрытия. */
  id: number | null;
  ts: number;
  side: "buy" | "sell";
  qty: number;
  price: number;
  reason: string | null;
  fee: Num;
  /** fill, переворачивающий позицию, разделён между двумя сделками (qty — доля этой сделки). */
  split: boolean;
  synthetic: boolean;
  basis?: "stop_level" | "candle_close" | "candle_open" | "entry";
}

export interface StatsTradeRow {
  n: number;
  entry_ts: number;
  exit_ts: number;
  side: "long" | "short";
  qty: Num;
  entry_price: Num;
  exit_price: Num;
  entry_notional: Num;
  gross: Num;
  fees: Num;
  funding: Num;
  /** false — часть расчётов фандинга вне локальных данных (принята за 0). */
  funding_complete: boolean;
  /** true — итог взят из записи Bybit closed-pnl (то же число, что в Telegram); false — оценка бота (комиссия × ставка, фандинг по локальным данным). */
  exact: boolean;
  net: Num;
  ret_pct: Num;
  ret_margin_pct: Num;
  /** Кошелёк счёта на входе (последняя точка equity не позже первого fill и не старше суток); null — точки нет. */
  equity_at_entry: Num;
  /** Результат сделки в % счёта = net / equity_at_entry × 100; null без кошелька на входе. Проценты сделок не складываются. */
  ret_account_pct: Num;
  hold_s: number;
  reason: string;
  reason_group: string;
  reconstructed: boolean;
  fills: StatsFill[];
}

export interface StatsTrades { total: number; truncated: boolean; rows: StatsTradeRow[] }

/** available:false -> заполнены только reason/message/range/computed_at/definitions/warnings. */
export interface StatsResponse {
  available: boolean;
  /** Машинный код недоступности: no_db | no_trades | no_equity | db_error | error. */
  reason?: string;
  message?: string;
  range: StatsRange;
  computed_at: number;
  window?: StatsWindow;
  config?: StatsConfig;
  data?: StatsData;
  equity?: StatsEquity;
  kpis?: StatsKpis;
  open_position?: StatsOpenPosition | null;
  position_mismatch?: StatsPositionMismatch | null;
  reconciliation?: StatsReconciliation;
  comparison?: StatsComparison;
  series?: StatsSeries;
  breakdown?: StatsBreakdown;
  trades?: StatsTrades;
  warnings: StatsWarning[];
  /** Подсказки по метрикам (русский текст) для тултипов. */
  definitions: Record<string, string>;
}
