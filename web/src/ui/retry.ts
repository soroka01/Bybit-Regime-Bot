// Повтор запроса при «сервер занят / вытеснен» с ограничением по числу попыток и по суммарному времени ожидания.
// Каждая попытка на сервере может ждать очередь до ~20 с, поэтому без предела по времени цикл мог висеть минутами.
import { asApiError, type ApiError } from "../api";

interface BusyRetryOpts {
  /** Максимум попыток (по умолчанию 4). */
  attempts?: number;
  /** Предел суммарного времени от первой попытки, мс (по умолчанию 60 000). */
  capMs?: number;
  /** Пауза перед повтором после попытки k (1-based), мс. */
  backoff?(k: number): number;
  /** Вызывается перед ожиданием повтора: следующая попытка — k+1 из n. waitedS — серверное время ожидания из тела 429, если есть. */
  onWait?(next: number, n: number, err: ApiError, waitedS?: number): void;
  signal?: AbortSignal;
  now?(): number;
  sleep?(ms: number, signal?: AbortSignal): Promise<void>;
}

type BusyResult<T> = { ok: true; value: T } | { ok: false; reason: "busy"; attempts: number; error: ApiError };

export const isBusyCode = (code: string) => code === "busy" || code === "superseded";

/** Пауза, прерываемая сигналом (иначе «Остановить» ждало бы конца паузы). */
export const abortableSleep = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const t = setTimeout(done, ms);
    function done() { clearTimeout(t); signal?.removeEventListener("abort", done); resolve(); }
    signal?.addEventListener("abort", done, { once: true });
  });

/**
 * Выполняет fn; на busy/superseded ждёт и повторяет. Любая другая ошибка пробрасывается как есть.
 * Если попытки или время кончились — {ok:false, reason:"busy"} (вызывающий помечает стратегию «сервер занят» и идёт дальше).
 */
export async function withBusyRetry<T>(fn: () => Promise<T>, o: BusyRetryOpts = {}): Promise<BusyResult<T>> {
  const n = o.attempts ?? 4;
  const capMs = o.capMs ?? 60_000;
  const backoff = o.backoff ?? ((k: number) => 1200 * k);
  const now = o.now ?? Date.now;
  const sleep = o.sleep ?? abortableSleep;
  const t0 = now();
  let last: ApiError | null = null;
  for (let k = 1; k <= n; k++) {
    try {
      return { ok: true, value: await fn() };
    } catch (e) {
      const err = asApiError(e);
      if (!isBusyCode(err.code)) throw err;
      last = err;
    }
    if (o.signal?.aborted) break;
    const wait = backoff(k);
    if (k >= n || now() - t0 + wait >= capMs) break;
    const w = last.extra["waited_s"];
    o.onWait?.(k + 1, n, last, typeof w === "number" ? w : undefined);
    await sleep(wait, o.signal);
    if (o.signal?.aborted) break;
  }
  return { ok: false, reason: "busy", attempts: n, error: last! };
}
