// Поиск по списку стратегий (комбобокс выбора): все слова запроса обязательны; имя важнее описания, начало слова важнее подстроки.
export interface StratLike { name: string; desc: string; derivative: boolean }

const norm = (s: string): string => s.toLowerCase().replace(/ё/g, "е");
const KEYWORDS = "деривативы дериватив derivative";

function score(s: StratLike, words: string[]): number {
  const name = norm(s.name), desc = norm(s.desc), extra = s.derivative ? KEYWORDS : "";
  let total = 0;
  for (const w of words) {
    let best = 0;
    if (name.startsWith(w)) best = 100;
    else if (name.split(/[\s\-_/()]+/).some((t) => t.startsWith(w))) best = 80;
    else if (name.includes(w)) best = 60;
    else if (desc.split(/[\s\-_/(),.]+/).some((t) => t.startsWith(w))) best = 30;
    else if (desc.includes(w) || extra.includes(w)) best = 15;
    if (!best) return 0;
    total += best;
  }
  return total;
}

/** Пустой запрос — исходный порядок. Иначе — подходящие по убыванию релевантности (при равенстве — исходный порядок). */
export function filterStrategies<T extends StratLike>(list: readonly T[], query: string): T[] {
  const words = norm(query).split(/\s+/).filter(Boolean);
  if (!words.length) return [...list];
  return list.map((s, i) => ({ s, i, sc: score(s, words) })).filter((x) => x.sc > 0).sort((a, b) => b.sc - a.sc || a.i - b.i).map((x) => x.s);
}
