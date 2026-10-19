// Командная палитра: чистая логика поиска и сортировки (без DOM; тесты: tests/palette.test.ts).

export interface Command {
  id: string;
  title: string;
  /** Вторая строка (необязательно). */
  hint?: string;
  /** Раздел: «Вкладки», «Тема», «Стратегии», «Действия». */
  group: string;
  /** Дополнительные слова для поиска (синонимы, латиница). */
  keywords?: string;
  icon?: string;
  /** Подпись горячей клавиши справа. */
  keys?: string[];
  run(): void | Promise<void>;
}

/** Нижний регистр, ё -> е, лишние пробелы убраны. */
export const norm = (s: string): string => s.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();

/** Все символы запроса встречаются в тексте по порядку (нестрогое совпадение: «лбр» ~ «Лаборатория»). */
export function isSubsequence(q: string, text: string): boolean {
  let i = 0;
  for (let j = 0; j < text.length && i < q.length; j++) if (text[j] === q[i]) i++;
  return i === q.length;
}

/** Оценка одного слова запроса относительно команды: 0 — не подходит. Начало слова в названии > подстрока в названии > ключевые слова/раздел > нестрогое. */
function tokenScore(tok: string, title: string, rest: string): number {
  if (title.startsWith(tok)) return 100;
  if (title.split(/[\s·:/()+-]+/).some((w) => w.startsWith(tok))) return 80;
  if (title.includes(tok)) return 60;
  if (rest.includes(tok)) return 35;
  return tok.length >= 3 && isSubsequence(tok, title) ? 12 : 0;
}

/** Суммарная оценка команды по запросу; 0 — хотя бы одно слово запроса не нашлось. */
export function scoreCommand(c: Command, query: string): number {
  const q = norm(query);
  if (!q) return 1;
  const title = norm(c.title), rest = norm(`${c.hint ?? ""} ${c.group} ${c.keywords ?? ""}`);
  let total = 0;
  for (const tok of q.split(" ")) {
    const s = tokenScore(tok, title, rest);
    if (!s) return 0;
    total += s;
  }
  return total;
}

/**
 * Отбор и порядок. Пустой запрос: недавно выбранные (самые свежие первыми), затем остальные в исходном порядке.
 * Непустой: по убыванию оценки; при равенстве — недавние выше, затем исходный порядок (сортировка стабильна).
 */
export function filterCommands(cmds: readonly Command[], query: string, recent: readonly string[] = []): Command[] {
  const rank = (id: string) => { const i = recent.indexOf(id); return i < 0 ? Infinity : i; };
  const scored = cmds.map((c, i) => ({ c, i, s: scoreCommand(c, query) })).filter((x) => x.s > 0);
  scored.sort((a, b) => (norm(query) ? b.s - a.s : 0) || rank(a.c.id) - rank(b.c.id) || a.i - b.i);
  return scored.map((x) => x.c);
}

/** Запоминает выбор: id в начало, без повторов, не больше max. */
export function pushRecent(recent: readonly string[], id: string, max = 6): string[] {
  return [id, ...recent.filter((x) => x !== id)].slice(0, max);
}
