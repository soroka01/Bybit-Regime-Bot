/// <reference types="vite/client" />
// Охрана локализации: в коде интерфейса (src/**/*.ts кроме словарей i18n/messages) не должно быть русских литералов, кроме осознанных исключений.
// Новый русский текст в коде — это непереведённая строка: её место в словаре (src/i18n/messages/<пространство>.ts, ru и en).
// ALLOWED — файл -> максимум строк с кириллицей вне комментариев и причина. Рост числа строк в файле из списка тоже ловится: проверьте, что новая строка
// действительно протокол/данные, а не текст интерфейса, и только тогда поднимите предел.
import { describe, expect, it } from "vitest";

const sources = import.meta.glob("../src/**/*.ts", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

const CYR = /[А-Яа-яЁё]/;

/** Строки кода без комментариев (блочные /* *​/ и построчные //, кроме «://» в адресах). */
function codeLines(src: string): string[] {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").split(/\r?\n/).map((l) => l.replace(/(?<!:)\/\/.*$/, ""));
}

const ALLOWED: Record<string, [number, string]> = {
  "src/apiErrors.ts": [12, "русские тексты ответов сервера, по которым подбирается перевод (сервер отвечает на языке APP_LANGUAGE)"],
  "src/charts/strips.ts": [1, "сравнение с русской подписью строки «Итого» (подпись строится локально, рядом есть «Total»)"],
  "src/i18n/index.ts": [1, "сообщение разработчику в console.warn о ненайденном ключе"],
  "src/i18n/storedText.ts": [16, "русские значения, которые бот сохраняет в БД (trades.reason, kill_reason): по ним подбирается перевод"],
  "src/leverage.ts": [1, "botLimitNoteRu: русская заметка в тексте для русского Telegram-бота"],
  "src/market.ts": [1, "marketLabel: «Спот/Перп» в протоколе для бота (core/legspec.py)"],
  "src/tabs/lbCard.ts": [3, "тексты причин сервера для старого сервера без reason_code"],
  "src/tabs/lbState.ts": [1, "сообщение разработчику в console.warn"],
  "src/tabs/leaderboard.ts": [1, "result в предложении для бота: протокол Telegram"],
  "src/tabs/liveCalc.ts": [1, "isNoiseEvent: совпадение с сохранённым русским текстом события"],
  "src/tabs/portfolio.ts": [4, "текст «для бота»: протокол core/legspec.py"],
  "src/tabs/statsView.ts": [1, "r.key.includes(«реконструкция»): логика по сохранённому русскому значению"],
  "src/tabs/tradesLogic.ts": [1, "диапазон а-я в имени CSV-файла"],
  "src/ui/clipboard.ts": [8, "текст «для бота»: протокол core/legspec.py"],
  "src/ui/palette.ts": [8, "ключевые слова поиска: русские запросы находят команды и в английском интерфейсе"],
  "src/ui/paletteCore.ts": [1, "замена ё на е при поиске"],
  "src/ui/strategySearch.ts": [2, "замена ё на е и русские слова поиска"],
};

describe("русские литералы в коде интерфейса", () => {
  const rows = Object.entries(sources)
    .map(([path, src]) => [path.replace("../", ""), codeLines(src).filter((l) => CYR.test(l)).length] as const)
    .filter(([path]) => !path.startsWith("src/i18n/messages/"));

  it("исходники найдены", () => expect(rows.length).toBeGreaterThan(60));

  it("вне списка исключений русских строк нет", () => {
    const stray = rows.filter(([p, n]) => n > 0 && !(p in ALLOWED)).map(([p, n]) => `${p}: ${n}`);
    expect(stray, "непереведённые строки: перенесите их в словарь").toEqual([]);
  });

  it("в файлах из списка исключений строк не больше разрешённого", () => {
    const over = rows.filter(([p, n]) => p in ALLOWED && n > ALLOWED[p]![0]).map(([p, n]) => `${p}: ${n} > ${ALLOWED[p]![0]} (${ALLOWED[p]![1]})`);
    expect(over).toEqual([]);
  });

  it("список исключений не содержит лишнего (файл исчез или уже без русских строк)", () => {
    const have = new Map(rows);
    const gone = Object.keys(ALLOWED).filter((p) => !have.has(p) || have.get(p) === 0);
    expect(gone).toEqual([]);
  });
});
