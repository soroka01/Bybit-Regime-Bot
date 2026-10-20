// Дебаунс + отмена устаревших запросов («последний побеждает») + повтор при «сервер занят / запрос вытеснен» с ограничением по времени.
import { asApiError, type ApiError } from "../api";
import { isBusyCode } from "./retry";

interface RunnerOpts<B, R> {
  delay: number;
  run(body: B, signal: AbortSignal): Promise<R>;
  onStart(): void;
  onData(r: R, body: B): void;
  onError(e: ApiError, body: B): void;
  /** Сервер занят или вытеснил запрос другим: ожидаем и повторим (попытка k из max). */
  onRetry?(k: number, max: number, body: B): void;
  /** Максимум повторов при busy (по умолчанию 3). */
  maxRetries?: number;
  /** Предел суммарного времени ожидания повторов, мс (по умолчанию 60 000). */
  capMs?: number;
  /** Пауза перед повтором номер k (1-based), мс. */
  backoff?(k: number): number;
}

export function createRunner<B, R>(o: RunnerOpts<B, R>) {
  const maxRetries = o.maxRetries ?? 3;
  const capMs = o.capMs ?? 60_000;
  const backoff = o.backoff ?? ((k: number) => 800 * k);
  let timer = 0;
  let ctl: AbortController | null = null;
  let seq = 0;
  let retries = 0;
  let t0 = 0;

  async function exec(body: B) {
    ctl?.abort();
    const c = (ctl = new AbortController());
    const my = ++seq;
    o.onStart();
    try {
      const r = await o.run(body, c.signal);
      if (my !== seq) return;
      retries = 0;
      o.onData(r, body);
    } catch (e) {
      if (my !== seq) return;
      const err = asApiError(e);
      if (isBusyCode(err.code) && retries < maxRetries) {   // сервер занят / вытеснил запрос (superseded — наш запрос последний, значит, вытеснил чужой) — повторим чуть позже
        const wait = backoff(retries + 1);
        if (Date.now() - t0 + wait < capMs) {
          retries++;
          o.onRetry?.(retries, maxRetries, body);
          timer = window.setTimeout(() => void exec(body), wait);
          return;
        }
      }
      // superseded относится к «тихим» кодам, но здесь наш запрос — актуальный (иначе сработал бы my !== seq): тихо молчать нельзя, «Считаю…» зависло бы
      if (err.silent && err.code !== "superseded") return;
      o.onError(err, body);
    }
  }

  const begin = () => { retries = 0; t0 = Date.now(); };

  return {
    /** Запланировать расчёт после паузы в изменениях. */
    schedule(body: B) {
      clearTimeout(timer);
      begin();
      ctl?.abort();
      ++seq;
      o.onStart();
      timer = window.setTimeout(() => void exec(body), o.delay);
    },
    now(body: B) {
      clearTimeout(timer);
      begin();
      return exec(body);
    },
    cancel() {
      clearTimeout(timer);
      ctl?.abort();
      ++seq;
    },
  };
}
