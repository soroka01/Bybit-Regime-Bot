// Типы словарей. Сообщение — строка с подстановками {имя}, набор множественных форм (выбор по params.n) или список строк (месяцы, дни недели).
export interface Plural { other: string; zero?: string; one?: string; two?: string; few?: string; many?: string }
export type Msg = string | Plural | readonly string[];
