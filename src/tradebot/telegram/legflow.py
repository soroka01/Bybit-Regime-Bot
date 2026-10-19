"""Добавление и удаление стратегий в Telegram: черновики из присланного текста / с сайта, проверка окна, выбор веса, удаление.
Миксин для Controller (handlers.py): использует его хранилище, раннер, подтверждения (_arm/_consume) и вёрстку (head/compose).

Черновик — строка таблицы proposals (new -> announced -> added | rejected | expired); решение только здесь, с подтверждением на
mainnet. Сайт ничего не меняет в торговле сам: он лишь кладёт предложение в очередь."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import math

from tradebot.core.legspec import draft_payload, leg_from_payload, leg_label, params_text, parse_leg_text, validate_leg
from tradebot.core.portfolio import Leg
from tradebot.core.strategies import STRATEGIES
from tradebot.telegram import keyboards as kb
from tradebot.telegram import texts as tx
from tradebot.telegram.panel import Screen
from tradebot.telegram.texts import code, esc, it
from tradebot.trading.legcheck import CheckError, check_leg
from tradebot.trading.market_feed import TF_DELTA
from tradebot.trading.state import MAX_LEGS, MAX_OPEN_PROPOSALS

log = logging.getLogger("tg.legs")
WEIGHT_PRESETS = (10, 25, 50, 75, 100)
ERR_GONE = "предложение уже обработано или устарело"


def p0(x: float) -> str:
    """Доля -> целые проценты: 0.5 -> «50%»."""
    return f"{round(x * 100)}%"


def is_active(w: float, en: bool) -> bool:
    return en and w > 0


class LegFlow:
    """Миксин: нужны self.st, self.runner, self.env, self.pending, self._arm/_consume, self.head/compose, self._target, self._leg_line."""

    # ---------- общее ----------
    def _init_legflow(self) -> None:
        self.rm_target: dict[str, str] = {}      # ключ подтверждения удаления -> ключ ноги (защита от сдвига индексов)

    def _catalog_changed(self) -> None:
        """Индексы ног сдвинулись: подтверждения, взведённые до изменения, уже ссылаются не на те ноги."""
        self.pending.clear()
        self.rm_target.clear()

    @staticmethod
    def _desc(leg: Leg) -> str:
        return tx.STRATEGY_DESC.get(leg.strategy) or (STRATEGIES[leg.strategy].desc if leg.strategy in STRATEGIES else "")

    def _stale_note(self, leg: Leg, chk: dict) -> str | None:
        """Локальный кэш свечей отстаёт (например, 4h при работающей только 6h-ноге): превью сигнала/цели тогда приблизительное."""
        d = tx.parse_iso(chk.get("last_bar"))
        if d is None or leg.tf not in TF_DELTA:
            return None
        step, now = TF_DELTA[leg.tf].to_pytimedelta(), self.clock()
        if now - (d + step) <= 2 * step:
            return None
        return (f"⚠️ Локальные данные {esc(leg.tf)} отстают: последняя свеча {esc(tx.local(d, self.tz, now))} ({esc(tx.age(d, now))}). "
                f"Сигнал и цель в карточке приблизительные — обновите данные на сайте; бот в работе берёт свежие с биржи.")

    def _latch_line(self, before: float | None, after: float | None) -> str | None:
        """Защёлка снимается при любой смене цели — бот может сразу войти в рынок: об этом надо сказать до подтверждения."""
        if not self.st.get("latch") or self.paper:
            return None
        if before is not None and after is not None and abs(before - after) < 1e-9:
            return None
        return "🔒 Сейчас включена защёлка: при смене цели она снимется, и бот может сразу войти в рынок."

    # ---------- создание черновиков ----------
    def propose_from_text(self, text: str) -> tuple[list[int], list[str]]:
        """Текст с сайта -> id черновиков и список ошибок (по ногам). ValueError — если вообще нечего разбирать."""
        drafts = parse_leg_text(text)
        ids, errors = [], []
        room = MAX_OPEN_PROPOSALS - self.st.open_proposals()
        for d in drafts:
            try:
                leg = validate_leg(d.leg)
            except ValueError as e:
                errors.append(f"{leg_label(d.leg)}: {e}")
                continue
            if len(ids) >= room:
                errors.append(f"{leg_label(leg)}: слишком много ожидающих предложений (максимум {MAX_OPEN_PROPOSALS}) — разберите их")
                continue
            ids.append(self.st.add_proposal("text", draft_payload(dataclasses.replace(d, leg=leg))))
        return ids, errors

    async def ensure_check(self, pid: int) -> dict | None:
        """Проверка окна/сигнала для черновика (в потоке: pandas); результат кладётся в payload. Ошибки «нет данных» не кэшируются."""
        row = self.st.proposal(pid)
        if not row:
            return None
        payload = row["payload"]
        chk = payload.get("check")
        if chk is not None and not chk.get("error"):
            return payload
        leg = leg_from_payload(payload)
        try:
            chk = dataclasses.asdict(await asyncio.to_thread(check_leg, leg))
        except CheckError as e:
            chk = {"ok": False, "error": True, "msg": str(e)}
        except Exception as e:  # noqa: BLE001  проверка не должна ронять бота
            log.exception("legcheck failed")
            chk = {"ok": False, "error": True, "msg": f"ошибка проверки: {type(e).__name__}"}
        payload["check"] = chk
        self.st.set_proposal(pid, payload=payload)
        return payload

    # ---------- экраны ----------
    def _pending_rows(self) -> list[tuple[int, str]]:
        out = []
        for status in ("new", "announced"):
            for r in self.st.proposals(status):
                d = r["payload"].get("leg", {})
                out.append((r["id"], f"{d.get('strategy', '?')} · {d.get('tf', '?')}"))
        return sorted(out)

    def add_screen(self, error: str | None = None, toast: str | None = None) -> Screen:
        self.st.expire_proposals()
        how = ["1️⃣ На сайте нажмите «Копировать для бота» (Лаборатория, Рейтинг, Портфель) и отправьте текст сюда сообщением.",
               "2️⃣ Или нажмите «В бота» рядом — предложение придёт сюда само.",
               "3️⃣ Бот проверит стратегию на истории и покажет карточку; вес выбираете вы."]
        fits = it(f"Подходит: BTCUSDT-перп, таймфреймы 1h · 4h · 6h, сигнал -1/0/+1. Всего стратегий — не больше {MAX_LEGS}.")
        secs: list = [["➕ <b>Новая стратегия</b>"], how, fits]
        if error:
            secs.append([f"⚠️ {esc(tx.trunc(error, 600))}"])
        pend = self._pending_rows()
        if pend:
            secs.append(["📥 <b>Ждут решения</b> " + it("(нажмите, чтобы открыть)")])
        return Screen(self.compose(self.head("Стратегии", "Добавить"), *secs), kb.add_menu(pend), toast, alert=bool(error))

    def checking_screen(self, n: int) -> Screen:
        return Screen(self.compose(self.head("Стратегии", "Добавить"), [f"⏳ Проверяю стратегию на истории… ({n})", it("Это занимает несколько секунд.")]),
                      kb.add_menu([]))

    def _weights_after(self, leg: Leg, pct: int, eq: bool) -> tuple[float, dict[str, float]]:
        """Вес новой ноги и новые веса остальных для варианта «pct%» или «поровну»."""
        if not eq:
            return pct / 100, {}
        act = [l for l, w, en in self.st.leg_cfg() if l.key != leg.key and is_active(w, en)]
        each = math.floor(100 / (len(act) + 1) + 1e-9) / 100
        return each, {l.key: each for l in act}

    async def draft_screen(self, pid: int, toast: str | None = None) -> Screen:
        row = self.st.proposal(pid)
        if not row or row["status"] not in ("new", "announced"):
            raise ValueError(ERR_GONE)
        payload = await self.ensure_check(pid)
        chk = payload.get("check") or {}
        leg = leg_from_payload(payload)
        cfg = self.st.leg_cfg()
        cur = next(((l, w, en) for l, w, en in cfg if l.key == leg.key), None)
        others_w = sum(w for l, w, en in cfg if l.key != leg.key and en)
        cap = max(0, math.floor((1.0 - others_w) * 100 + 1e-6))
        n_act = sum(1 for l, w, en in cfg if l.key != leg.key and is_active(w, en))
        mode = "лонг" if leg.long_only else "лонг и шорт"
        title = ("♻️ Заменит параметры" if cur else "🧩 Новая стратегия")
        L = [f"{title}: <b>{esc(leg.strategy)}</b> · {esc(leg.tf)}", f"    {code(params_text(leg.params))} · {mode}"]
        secs: list = [L]
        if (desc := self._desc(leg)):
            secs.append(tx.bq(esc(desc)))
        info: list[str] = []
        if payload.get("result"):
            lev = payload.get("bt_leverage")
            info.append(f"📈 На сайте: {esc(payload['result'])}" + (it(f" · плечо {lev:g}x") if lev else ""))
        if chk.get("ok"):
            info.append(f"✅ {esc(chk['msg'])}")
            sg = chk.get("signal")
            d = tx.parse_iso(chk.get("last_bar"))
            at = f" по {esc(tx.local(d + TF_DELTA[leg.tf].to_pytimedelta(), self.tz, self.clock()))}" if d is not None and leg.tf in TF_DELTA else ""
            info.append(f"📡 Сигнал{at}: {tx.sig_icon(sg)} {tx.sig_word(sg)}")
        elif chk:
            info.append(f"❌ {esc(chk.get('msg', 'проверка не пройдена'))}")
        else:
            info.append("❌ проверка не выполнена")
        secs.append(info)
        # заметки сайта про плечо повторяют строку ниже (она точнее: берёт плечо бота из настроек)
        warn = [f"⚠️ {esc(n)}" for n in payload.get("notes", []) if "недостижимо в боте" not in n and not n.lower().startswith("плечо")]
        lev_bot = self.st.settings()["leverage"]
        lev_bt = payload.get("bt_leverage")
        if lev_bt and abs(lev_bt - lev_bot) > 1e-9:
            warn.append(f"⚠️ В бэктесте плечо {lev_bt:g}x, у бота {lev_bot:g}x — доход и просадка будут другими")
        if (stale := self._stale_note(leg, chk)):
            warn.append(stale)
        if chk.get("deriv"):
            warn.append("⚠️ Нужны данные OI/фандинга/LSR: если биржа их не отдаст, цель всего портфеля останется прежней (бот напишет об ошибке)")
        if cur:
            same = cur[0].params == leg.params and cur[0].long_only == leg.long_only
            warn.append(it(f"Такая стратегия уже есть (вес {p0(cur[1])}{', параметры те же' if same else ''}): "
                           f"новая запись заменит её параметры, ключ стратегии — «название · таймфрейм» — один."))
        secs.append(warn)
        can_add = bool(chk.get("ok"))
        if can_add:
            secs.append([f"📏 Свободно {code(f'{cap}%')} " + it(f"(у других стратегий {p0(others_w)})")] +
                        ([it("Места нет: выберите «Поровну со всеми» или добавьте без веса и уменьшите вес другой.")] if cap < WEIGHT_PRESETS[0] else []))
        cur_pct = round(cur[1] * 100) if cur else None
        presets = sorted({v for v in WEIGHT_PRESETS if v <= cap} | ({cur_pct} if cur_pct else set()))
        menu = kb.draft_menu(pid, presets, equal=n_act >= 1, can_add=can_add, retry=bool(chk.get("error")), current=cur_pct,
                             exists=cur is not None)
        return Screen(self.compose(self.head("Стратегии", "Новая"), *secs), menu, toast)

    # ---------- применение ----------
    def _after_lines(self, leg: Leg, weight: float, others: dict[str, float]) -> list[tuple[Leg, float, bool]]:
        cfg = [(l, others.get(l.key, w), en) for l, w, en in self.st.leg_cfg() if l.key != leg.key]
        return cfg + [(leg, weight, True)]

    def _add_confirm_lines(self, p: tuple[str, ...]) -> list[str]:
        pid = int(p[1])
        row = self.st.proposal(pid)
        if not row or row["status"] not in ("new", "announced"):
            raise ValueError(ERR_GONE)
        payload = row["payload"]
        chk = payload.get("check") or {}
        if not chk.get("ok"):
            raise ValueError("проверка не пройдена — добавить нельзя")
        leg = leg_from_payload(payload)
        weight, others = self._weights_after(leg, int(p[3]) if p[2] == "w" else 0, p[2] == "eq")
        cfg = self.st.leg_cfg()
        before = {l.key: (w, en) for l, w, en in cfg}
        after = self._after_lines(leg, weight, others)
        old = before.get(leg.key)
        L = [f"<b>{'Заменить параметры стратегии?' if old else 'Добавить стратегию?'}</b>",
             f"{kb.leg_icon(True, weight)} {esc(leg.strategy)} · {esc(leg.tf)}: <b>вес {p0(old[0]) if old else '0%'} → {p0(weight)}</b>"]
        for l, w, en in after:
            if l.key in others and abs(w - before[l.key][0]) > 1e-9:
                L.append(f"{kb.leg_icon(en, w)} {esc(l.strategy)} · {esc(l.tf)}: вес {p0(before[l.key][0])} → <b>{p0(w)}</b>")
        if old and not old[1]:
            L.append("• Стратегия сейчас <b>выключена</b> — после замены будет <b>включена</b>")
        L.append(f"• Сумма весов <b>{p0(sum(w for _, w, en in cfg if en))} → {p0(sum(w for _, w, en in after if en))}</b>")
        g0, g1 = self._target(cfg), self._target(after, {leg.key: chk.get("signal")})
        L.append(f"• Цель сейчас <b>{tx.sn(g0)} → {tx.sn(g1)}</b>" if g0 is not None and g1 is not None else "• Цель: " + it("пересчитается"))
        if (latch := self._latch_line(g0, g1)):
            L.append(latch)
        L.append(it("Позиция пересчитается при ближайшей сверке."))
        return L

    async def _apply_add(self, pid: int, pct: int, eq: bool) -> Screen:
        row = self.st.proposal(pid)
        if not row or row["status"] not in ("new", "announced"):
            raise ValueError(ERR_GONE)
        payload = await self.ensure_check(pid)                # карточку обычно уже открывали; прямой тап по кнопке тоже безопасен
        if not (payload.get("check") or {}).get("ok"):
            raise ValueError("проверка не пройдена — добавить нельзя")
        leg = validate_leg(leg_from_payload(payload))          # ещё раз: строку мог записать другой процесс/версия
        weight, others = self._weights_after(leg, pct, eq)
        if not self.st.close_proposal(pid, "added"):          # атомарно: второй тап по тому же предложению не пройдёт
            raise ValueError(ERR_GONE)
        try:
            replaced = self.st.add_leg(leg, weight, others)
        except ValueError:
            self.st.set_proposal(pid, row["status"])          # не потеряно: можно выбрать другой вес
            raise
        self.st.add_event("info", f"{'Заменены параметры' if replaced else 'Добавлена стратегия'} {leg_label(leg)} (вес {p0(weight)})")
        self._catalog_changed()
        self.runner.trigger()
        i = next(k for k, (l, _, _) in enumerate(self.st.leg_cfg()) if l.key == leg.key)
        return await self.leg_screen(i, "Параметры заменены" if replaced else "Стратегия добавлена; пересчёт цели запущен")

    async def _dispatch_ad(self, p: tuple[str, ...]) -> Screen:
        if len(p) == 1:
            return self.add_screen()
        pid = int(p[1])
        if len(p) == 2:
            return await self.draft_screen(pid)
        if p[2] == "x":
            if not self.st.close_proposal(pid, "rejected"):
                raise ValueError(ERR_GONE)
            return self.add_screen(toast="Предложение отклонено")
        return await self._apply_add(pid, int(p[3]) if p[2] == "w" else 0, p[2] == "eq")

    def _ad_needs_confirm(self, p: tuple[str, ...]) -> bool:
        """На mainnet подтверждение нужно всегда, кроме добавления НОВОЙ стратегии без веса (0%): она в торговлю не попадает.
        Замена параметров существующей ноги и «Поровну» меняют цель, поэтому подтверждаются."""
        if self.env != "mainnet" or len(p) < 3 or p[-1] == "y":
            return False
        if p[2] == "eq":
            return True
        row = self.st.proposal(int(p[1]))
        try:
            replace = bool(row) and leg_from_payload(row["payload"]).key in {l.key for l in self.st.catalog()}
        except (KeyError, TypeError, ValueError):
            replace = False
        return p[2] == "w" and (int(p[3]) > 0 or replace)

    # ---------- удаление ----------
    @staticmethod
    def _is_rm(p: tuple[str, ...]) -> bool:
        return p[0] == "lg" and len(p) >= 3 and p[2] == "rm"

    def _rm_lines(self, i: int) -> list[str]:
        cfg = self.st.leg_cfg()
        leg, w, en = cfg[i]
        after = [(l, ww, e) for j, (l, ww, e) in enumerate(cfg) if j != i]
        active_before = [x for x in cfg if is_active(x[1], x[2])]
        t0, t1 = sum(ww for _, ww, e in cfg if e), sum(ww for _, ww, e in after if e)
        g0, g1 = self._target(cfg), self._target(after)
        L = ["<b>Удалить стратегию?</b>", f"🗑 {esc(leg.strategy)} · {esc(leg.tf)}: " + (f"<b>вес {p0(w)} → удалена</b>" if en else "<b>удалена</b>"),
             f"• Сумма весов <b>{p0(t0)} → {p0(t1)}</b>"]
        L.append(f"• Цель сейчас <b>{tx.sn(g0)} → {tx.sn(g1)}</b>" if g0 is not None and g1 is not None else "• Цель: " + it("пересчитается"))
        if is_active(w, en) and len(active_before) == 1:
            L.append("⚠️ Это единственная работающая стратегия: цель станет 0, открытая позиция будет закрыта.")
        if (latch := self._latch_line(g0, g1)):
            L.append(latch)
        L.append(it("Параметры можно вернуть, добавив стратегию снова с сайта."))
        return L

    async def _handle_rm(self, p: tuple[str, ...]) -> Screen:
        i = self._leg_idx(p[1])
        leg = self.st.leg_cfg()[i][0]
        base = f"lg:{i}:rm"
        if p[-1] == "y":
            if not self._consume(base):
                return self.home("Подтверждение устарело")
            if self.rm_target.pop(base, None) != leg.key:
                raise ValueError("список стратегий изменился — откройте стратегию заново")
            self.st.remove_leg(leg.key)
            self.st.add_event("info", f"Удалена стратегия {leg_label(leg)}")
            self._catalog_changed()
            self.runner.trigger()
            return await self.legs_screen(f"Удалена: {leg_label(leg)}")
        text = self.compose(self.head("Подтверждение"), self._rm_lines(i), it(f"(действует {self.confirm_ttl:.0f} с)"))
        screen = Screen(text, kb.confirm(base + ":y", f"lg:{i}"))
        self._arm(base)                                            # сначала проверка/текст, потом «взвод» подтверждения
        self.rm_target[base] = leg.key
        return screen

    # ---------- уведомление о предложении с сайта ----------
    def notice(self, pid: int) -> tuple[str, object] | None:
        row = self.st.proposal(pid)
        if not row:
            return None
        payload = row["payload"]
        d, chk = payload.get("leg", {}), payload.get("check") or {}
        ok = chk.get("ok")
        lines = ["🧩 <b>Предложение с сайта</b>",
                 f"<b>{esc(d.get('strategy'))} · {esc(d.get('tf'))}</b> — {code(params_text(d.get('params', {})))}",
                 (f"✅ окно проверено · сигнал {tx.sig_icon(chk.get('signal'))} {tx.sig_word(chk.get('signal'))}" if ok
                  else f"❌ {esc(tx.trunc(chk.get('msg', 'проверка не пройдена'), 300))}")]
        return "\n".join(lines), kb.notice_menu(pid)
