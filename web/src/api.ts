import type {
  BacktestBody, BacktestResult, Curve, DataStatus, LbCached, UpdateResult, LbBody, LbItem, LbPlan, LiveSnapshot, Meta, OptBody, OptResult, PortfolioBody, ProposalResult, StatsRange, StatsResponse, SweepBody, SweepResult, WfRow, WfWindow,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
  /** Ошибки, которые не нужно показывать пользователю (запрос вытеснен/отменён). */
  get silent(): boolean {
    return this.code === "superseded" || this.code === "cancelled" || this.code === "aborted";
  }
}

/** Любое исключение -> ApiError (единая точка вместо дублей `e instanceof ApiError ? e : new ApiError(...)`). */
export function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "internal", e instanceof Error ? e.message : String(e));
}

/** Страховка от «вечных» запросов: обычные (не загрузка данных) вызовы обрываются через 120 с. */
export const REQUEST_TIMEOUT_MS = 120_000;

interface ReqOpts { timeout?: boolean }

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal, opts: ReqOpts = {}): Promise<T> {
  let res: Response;
  const guard = opts.timeout === false ? null : AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const sig = signal && guard ? AbortSignal.any([signal, guard]) : (signal ?? guard ?? undefined);
  try {
    res = await fetch(path, {
      ...init,
      signal: sig,
      headers: init.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch (e) {
    const name = (e as Error).name;
    if (guard?.aborted && !signal?.aborted) throw new ApiError(0, "timeout", `нет ответа дольше ${REQUEST_TIMEOUT_MS / 1000} с`);
    if (name === "AbortError" || name === "TimeoutError") throw new ApiError(0, "aborted", "отменено");
    throw new ApiError(0, "network", "сервер недоступен");
  }
  if (!res.ok) {
    let code = "http", msg = `HTTP ${res.status}`, extra: Record<string, unknown> = {};
    try {
      const j = await res.json();
      const d = j.detail;
      if (d && typeof d === "object" && !Array.isArray(d)) {
        const { code: c, message, ...rest } = d as Record<string, unknown>;
        code = String(c ?? code);
        msg = String(message ?? msg);
        extra = rest;
      } else if (Array.isArray(d)) {
        code = "validation";
        msg = d.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ");
      } else if (typeof d === "string") msg = d;
    } catch { /* не JSON */ }
    throw new ApiError(res.status, code, msg, extra);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown, signal?: AbortSignal, opts?: ReqOpts) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) }, signal, opts);

const qs = (o: Record<string, string | number>) =>
  Object.entries(o).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

/** Хук: вызывается после ЛЮБОЙ успешной загрузки/обновления данных (его ставит meta.ts: перечитать /api/meta и оповестить вкладки). */
export const hooks = { afterDataChange: async (): Promise<void> => {} };

// ---------- счётчик выполняющихся загрузок данных (fetch/update): для глобального индикатора и защиты баннера «скачать»
let dataJobCount = 0;
const jobSubs = new Set<() => void>();
export const dataJobsActive = (): boolean => dataJobCount > 0;
export function onDataJobs(fn: () => void): () => void {
  jobSubs.add(fn);
  return () => { jobSubs.delete(fn); };
}
async function trackJob<T>(fn: () => Promise<T>): Promise<T> {
  dataJobCount++;
  jobSubs.forEach((f) => f());
  try {
    return await fn();
  } finally {
    dataJobCount--;
    jobSubs.forEach((f) => f());
  }
}

export const api = {
  /** Первая загрузка идёт через обычный кэш/ETag (304); после загрузки данных meta.ts просит `fresh: true` — мимо кэша. */
  meta: (signal?: AbortSignal, fresh = false) => request<Meta>("/api/meta", fresh ? { cache: "no-cache" } : {}, signal),
  backtest: (b: BacktestBody, s?: AbortSignal) => post<BacktestResult>("/api/backtest", b, s),
  optimize: (b: OptBody, s?: AbortSignal) => post<OptResult>("/api/optimize", b, s),
  lbPlan: (symbol: string, tf: string, s?: AbortSignal) => request<LbPlan>(`/api/leaderboard/plan?${qs({ symbol, tf })}`, { cache: "no-store" }, s),
  lbItem: (b: LbBody, s?: AbortSignal) => post<LbItem>("/api/leaderboard/item", b, s),
  lbCached: (b: Omit<LbBody, "strategy" | "force">, s?: AbortSignal) => post<LbCached>("/api/leaderboard/cached", b, s),
  sweep: (b: SweepBody, s?: AbortSignal) => post<SweepResult>("/api/sweep", b, s),
  portfolio: (b: PortfolioBody, s?: AbortSignal) => post<BacktestResult>("/api/portfolio", b, s),
  wf: (lev: number, symbol: string, s?: AbortSignal) => request<WfRow[]>(`/api/walkforward?${qs({ lev, symbol })}`, {}, s),
  wfWindows: (lev: number, symbol: string, s?: AbortSignal) => request<WfWindow[]>(`/api/walkforward/windows?${qs({ lev, symbol })}`, {}, s),
  wfCurve: (lev: number, symbol: string, tf: string, strategy: string, variant: string, s?: AbortSignal) =>
    request<Curve>(`/api/walkforward/curve?${qs({ lev, symbol, tf, strategy, variant })}`, {}, s),
  /** Статистика реальной торговли бота (сервер кэширует ответ на минуту). 404 — бот работает на старой версии бэкенда. */
  stats: (range: StatsRange, s?: AbortSignal) => request<StatsResponse>(`/api/stats?${qs({ range })}`, { cache: "no-store" }, s),
  live: (s?: AbortSignal) => request<LiveSnapshot>("/api/live", { cache: "no-store" }, s),
  /** «В бота»: текст «для бота» уходит в Telegram-карточку; вес и подтверждение выбирает владелец там. 422 — текст отклонён, 409 — бот не запускался, 429 — слишком много неподтверждённых. */
  proposeToBot: (text: string, s?: AbortSignal) => post<ProposalResult>("/api/bot/proposals", { text }, s),
  dataStatus: (symbol: string, tf: string, s?: AbortSignal) => request<DataStatus>(`/api/data/status?${qs({ symbol, tf })}`, { cache: "no-store" }, s),
  // загрузка может идти минутами — без 120-секундной страховки
  dataUpdate: (symbol: string, tf: string) => trackJob(async () => {
    const r = await post<UpdateResult>("/api/data/update", { symbol, tf }, undefined, { timeout: false });
    await hooks.afterDataChange();
    return r;
  }),
  fetchData: (symbol: string, tf: string) => trackJob(async () => {
    const r = await post<{ bars: number }>("/api/data/fetch", { symbol, tf }, undefined, { timeout: false });
    await hooks.afterDataChange();
    return r;
  }),
};
