// Баннер ошибки с понятным текстом и действиями (повтор / явная загрузка данных).
import { api, ApiError, asApiError, dataJobsActive } from "../api";
import type { FetchEstimate } from "../types";
import { h } from "./dom";

const estimateText = (e: FetchEstimate): string =>
  `≈ ${e.requests} запросов к Bybit (свечи ${e.klines_requests} + деривативы ${e.derivs_requests}), до ${e.minutes} мин`;

export function errorBanner(err: ApiError, onRetry: () => void): HTMLElement {
  const box = h("div", { class: "banner error", role: "alert", "data-code": err.code });
  const title: Record<string, string> = {
    data_missing: "Нет локальных данных",
    timeout: "Расчёт занял слишком много времени",
    busy: "Сервер занят",
    network: "Сервер недоступен",
    no_results: "Нет результатов",
  };
  box.append(h("strong", { text: title[err.code] ?? "Ошибка" }), h("span", { text: " " + err.message }));
  const actions = h("div", { class: "banner-actions" });
  if (err.code === "data_missing" && typeof err.extra.symbol === "string" && typeof err.extra.tf === "string") {
    const sym = err.extra.symbol, tf = err.extra.tf;
    const btn = h("button", { class: "btn primary", type: "button", text: `Скачать ${sym} ${tf} с Bybit` });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Загрузка… (может занять минуту)";
      try {
        await api.fetchData(sym, tf);
        onRetry();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = `Ошибка: ${(e as Error).message}. Повторить загрузку`;
      }
    });
    actions.append(btn);
  } else {
    actions.append(h("button", { class: "btn", type: "button", text: "Повторить", onClick: onRetry }));
  }
  const est = err.extra.estimate as FetchEstimate | null | undefined;
  if (err.code === "data_missing" && est) box.append(h("p", { class: "muted small", text: `Загрузка ${est.days} дн.: ${estimateText(est)}.` }));
  box.append(actions);
  return box;
}

/**
 * Баннер вместо вкладки, которая упала при создании или показе. reset очищает только сохранённые настройки этой вкладки
 * (частая причина падения — повреждённый localStorage), retry пробует собрать вкладку заново.
 */
export function crashBanner(title: string, message: string, a: { retry(): void; reset(): void }): HTMLElement {
  return h("div", { class: "banner error tab-crash-banner", role: "alert", "data-code": "tab_crash" },
    h("strong", { text: `Вкладка «${title}» не открылась. ` }),
    h("span", { text: message }),
    h("p", { class: "muted small", text: "Чаще всего причина — повреждённые сохранённые настройки этой вкладки. Остальные вкладки работают." }),
    h("div", { class: "banner-actions" },
      h("button", { class: "btn", type: "button", text: "Повторить", onClick: a.retry }),
      h("button", { class: "btn primary", type: "button", "data-action": "reset", text: "Сбросить сохранённые настройки вкладки", onClick: a.reset })));
}

/**
 * Показывает ошибку в слоте баннера. Пока идёт загрузка данных (fetch/update) и в слоте уже стоит баннер data_missing
 * с кнопкой скачивания, «сервер занят»/«вытеснен» его не заменяют — иначе пропадает кнопка идущей загрузки.
 */
export function showError(slot: HTMLElement, err: ApiError | unknown, onRetry: () => void): void {
  const e = asApiError(err);
  if (dataJobsActive() && (e.code === "busy" || e.code === "superseded") && slot.querySelector("[data-code=data_missing]")) return;
  slot.replaceChildren(errorBanner(e, onRetry));
}
