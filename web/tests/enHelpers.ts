// Помощники для тестов английского интерфейса. Основной набор тестов идёт на ru (tests/setup.ts); английский проверяется отдельно:
// модули с константами, вычисляемыми при импорте (заголовки, словари вердиктов), нужно загружать ПОСЛЕ смены языка.
//
//   const { importEn } = await import("./enHelpers");
//   const { createLive } = await importEn(() => import("../src/tabs/live"));
//
// importEn сбрасывает кэш модулей, ставит en и только потом вызывает загрузчик, поэтому весь граф модулей (включая i18n) создаётся заново на en.
import { vi } from "vitest";

export async function importEn<T>(loader: () => Promise<T>): Promise<T> {
  vi.resetModules();
  const i18n = await import("../src/i18n");
  i18n.__setLang("en");
  return loader();
}

const CYR = /[А-Яа-яЁё]/;
const ATTRS = ["title", "aria-label", "data-tip", "placeholder", "alt", "aria-description", "data-l"];

/**
 * Все фрагменты с кириллицей внутри элемента: текстовые узлы и значимые атрибуты (title, aria-label, data-tip, placeholder, alt).
 * allow — выражения для разрешённых исключений (например, русские данные, пришедшие с сервера и показываемые как есть).
 */
export function cyrillicIn(root: Element, allow: RegExp[] = []): string[] {
  const out: string[] = [];
  const ok = (s: string) => allow.some((r) => r.test(s));
  const visit = (el: Element) => {
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (v && CYR.test(v) && !ok(v)) out.push(`[${a}] ${v}`);
    }
    for (const n of Array.from(el.childNodes)) {
      if (n.nodeType === 3) {
        const s = (n.textContent ?? "").trim();
        if (s && CYR.test(s) && !ok(s)) out.push(s);
      } else if (n.nodeType === 1) visit(n as Element);
    }
  };
  visit(root);
  return out;
}
