// Сборка словарей из пространств имён. Каждое пространство — свой файл со своим владельцем; ключи не должны пересекаться (проверяет tests/i18n.test.ts).
import type { Msg } from "./types";
import * as common from "./common";
import * as server from "./server";
import * as strategies from "./strategies";
import * as lab from "./lab";
import * as portfolio from "./portfolio";
import * as leaderboard from "./leaderboard";
import * as live from "./live";
import * as stats from "./stats";
import * as charts from "./charts";
import * as definitions from "./definitions";
import * as stored from "./stored";

export type { Msg, Plural } from "./types";

const SPACES = [common, server, strategies, lab, portfolio, leaderboard, live, stats, charts, definitions, stored];

/** Все ключи интерфейса: компилятор проверяет, что t("...") получает существующий ключ. */
export type Dict = typeof common.en & typeof server.en & typeof strategies.en & typeof lab.en & typeof portfolio.en & typeof leaderboard.en & typeof live.en & typeof stats.en & typeof charts.en & typeof definitions.en & typeof stored.en;
export type MsgKey = keyof Dict & string;

const merge = (pick: (s: (typeof SPACES)[number]) => Record<string, Msg>): Record<string, Msg> => Object.assign({}, ...SPACES.map(pick));
export const EN: Record<string, Msg> = merge((s) => s.en);
export const RU: Record<string, Msg> = merge((s) => s.ru);
/** Для теста пересечения ключей и их префиксов. */
export const SPACE_KEYS: Record<string, string[]> = { common: Object.keys(common.en), server: Object.keys(server.en), strategies: Object.keys(strategies.en), lab: Object.keys(lab.en), portfolio: Object.keys(portfolio.en), leaderboard: Object.keys(leaderboard.en), live: Object.keys(live.en), stats: Object.keys(stats.en), charts: Object.keys(charts.en), definitions: Object.keys(definitions.en), stored: Object.keys(stored.en) };
