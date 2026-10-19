// Среда бота. Рабочая среда (mainnet без paper) — норма, её на сайте не рекламируем: владелец не пользуется testnet/paper,
// поэтому заметный бейдж появляется ТОЛЬКО при testnet или paper (скорее всего, ошибка конфигурации). Тесты: tests/envFlag.test.ts.
import { badge } from "./kit";

export interface EnvFlag { text: string; title: string }

/** null — показывать нечего (mainnet/live без paper, а также неизвестная/пустая среда); иначе текст и подсказка предупреждения. */
export function envFlag(env: string | null | undefined, paper?: boolean | null): EnvFlag | null {
  if (paper) return { text: "PAPER · без ордеров", title: "Бумажный режим: ордера на биржу не отправляются. Если вы ждали реальной торговли — проверьте настройки бота." };
  if ((env ?? "").trim().toLowerCase() === "testnet") return { text: "TESTNET · не реальные деньги", title: "Бот работает в тестовой сети Bybit: деньги ненастоящие. Если вы ждали реальной торговли — проверьте настройки бота." };
  return null;
}

/** Заметный бейдж-предупреждение или null. */
export function envBadge(env: string | null | undefined, paper?: boolean | null): HTMLElement | null {
  const f = envFlag(env, paper);
  return f ? badge(f.text, "warn", { glyph: "!", title: f.title }) : null;
}
