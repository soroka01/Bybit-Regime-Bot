// Баннер ошибки с понятным текстом и действиями (повтор / явная загрузка данных).
import { api, ApiError, asApiError, dataJobsActive } from "../api";
import { errorTitle } from "../apiErrors";
import { t } from "../i18n";
import type { FetchEstimate } from "../types";
import { h } from "./dom";

const estimateText = (e: FetchEstimate): string =>
  t("common.banner.estimate", { requests: e.requests, klines: e.klines_requests, derivs: e.derivs_requests, minutes: e.minutes });

export function errorBanner(err: ApiError, onRetry: () => void): HTMLElement {
  const box = h("div", { class: "banner error", role: "alert", "data-code": err.code });
  // err.message уже на языке интерфейса для известных кодов (api.ts / apiErrors.ts); для прочих — текст сервера как есть
  box.append(h("strong", { text: errorTitle(err.code) }), h("span", { text: " " + err.message }));
  const actions = h("div", { class: "banner-actions" });
  if (err.code === "data_missing" && typeof err.extra.symbol === "string" && typeof err.extra.tf === "string") {
    const sym = err.extra.symbol, tf = err.extra.tf;
    const btn = h("button", { class: "btn primary", type: "button", text: t("common.banner.download", { symbol: sym, tf }) });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = t("common.banner.downloading");
      try {
        await api.fetchData(sym, tf);
        onRetry();
      } catch (e) {
        btn.disabled = false;
        btn.textContent = t("common.banner.download_failed", { message: (e as Error).message });
      }
    });
    actions.append(btn);
  } else {
    actions.append(h("button", { class: "btn", type: "button", text: t("common.retry"), onClick: onRetry }));
  }
  const est = err.extra.estimate as FetchEstimate | null | undefined;
  if (err.code === "data_missing" && est) box.append(h("p", { class: "muted small", text: t("common.banner.estimate_line", { days: est.days, estimate: estimateText(est) }) }));
  box.append(actions);
  return box;
}

/**
 * Баннер вместо вкладки, которая упала при создании или показе. reset очищает только сохранённые настройки этой вкладки
 * (частая причина падения — повреждённый localStorage), retry пробует собрать вкладку заново.
 */
export function crashBanner(title: string, message: string, a: { retry(): void; reset(): void }): HTMLElement {
  return h("div", { class: "banner error tab-crash-banner", role: "alert", "data-code": "tab_crash" },
    h("strong", { text: t("common.crash.title", { title }) + " " }),
    h("span", { text: message }),
    h("p", { class: "muted small", text: t("common.crash.hint") }),
    h("div", { class: "banner-actions" },
      h("button", { class: "btn", type: "button", text: t("common.retry"), onClick: a.retry }),
      h("button", { class: "btn primary", type: "button", "data-action": "reset", text: t("common.crash.reset"), onClick: a.reset })));
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
