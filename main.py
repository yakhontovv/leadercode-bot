"""
PAEI-бот «Код руководителя» - слой 1 «Один день» + карта команды.
YES-GROUP. По модели Ицхака Адизеса (вопросы оригинальные).

Флоу: /start (source: atlas|site|ads|inv_<id>) -> симуляция дня (8 сцен,
в 2/3/6 второй тап "точно не сделаю") -> предварительный код + инструкция
к себе + прогноз -> "Собрать карту команды" (ссылка + готовый текст) или
"Ответить за топа" (proxy). /team - карта: закрыто топами / только вами.

Скоринг: выбор +2, "точно не" -1. Пороги (симуляция seed 42, ВРЕМЕННЫЕ до
100+ живых): пара при отрыве <=4, "-" при <= -3, флэт при max-min <= 2.

Данные: sqlite на persistent volume (DB_PATH). Все попытки хранятся.
Админу: каждый /start и результат. Бэкап-дамп БД админу раз в сутки.
Пинг владельцу через 24ч, если ссылка взята, а топы не прошли.
"""

import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

TOKEN = os.environ["TELEGRAM_TOKEN"]
ADMIN = os.environ.get("ADMIN_CHAT_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/paei.db")
REVIEW_URL = os.environ.get("REVIEW_CALL_URL", "https://t.me/yakhontovv").strip()
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/yesequity").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("paei")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
BOT_USERNAME = ""

# ---------- Контент v0.10 ----------
CODES = ["P", "A", "E", "I"]
NAMES = {"P": "Производитель", "A": "Администратор", "E": "Предприниматель", "I": "Интегратор"}

# (время, текст сцены, {код: реплика}, двойной_тап)
SCENES = [
    ("9:00", "Вы в офисе. Первая встреча в 11:00 - впереди свободное окно на два часа. Что сделаете на самом деле?",
     {"P": "«Наконец-то поработаю: разгребу то, что горит»",
      "A": "«Сяду в цифры - сведу хвосты и план недели»",
      "E": "«Покручу идею, которая всю неделю в голове»",
      "I": "«Пройдусь по людям - услышать, чем дышат»"}, False),
    ("10:40", "Сообщение в чате: команда завалила срок по важному проекту. Первая реакция - честно:",
     {"P": "«Дайте сюда, сам доделаю. Быстрее, чем объяснять»",
      "A": "«Почему мы узнали об этом в последний день?»",
      "E": "«Стоп, а этот срок вообще ещё нужен?»",
      "I": "«Соберёмся и разберём, что между нами сломалось»"}, True),
    ("11:30", "Планёрка. Два самых сильных человека в команде схлестнулись - на повышенных.",
     {"P": "«Всё, время вышло: решение за мной, работаем»",
      "A": "«Давайте по фактам: кто за это отвечает?»",
      "E": "«Оба решения - вчерашние. Давайте переосмыслим саму задачу»",
      "I": "«Дело не в задаче - между вами что-то не так. Давайте об этом»"}, True),
    ("13:30", "Обед с партнёром. Он приносит большой, но сырой проект.",
     {"P": "«Вижу, как дожать до денег, - захожу»",
      "A": "«Сначала считаем: риски, ресурсы, план»",
      "E": "«Сырой - значит, правила ещё не написаны. Интересно»",
      "I": "«Смотря с кем делать. С правильными людьми - да»"}, False),
    ("15:00", "Собеседование: сильный финалист на ключевую роль. Какой довод для вас решающий?",
     {"P": "«Показал результаты - видно, что сделал руками»",
      "A": "«Системный человек: будет держать свою зону в порядке»",
      "E": "«Мыслит шире роли - видит то, чего ещё нет»",
      "I": "«За ним пойдут - ему будут верить»"}, False),
    ("17:30", "Ловите себя на мысли: нагрузка выросла вдвое, вы перестали успевать. Как выкручиваетесь - честно?",
     {"P": "«Поднажму сам - но срежу всё лишнее»",
      "A": "«Строю структуру: роли, процессы, отчёты»",
      "E": "«Ломаю саму модель работы - так дальше нельзя»",
      "I": "«Доверю людям больше - отдам куски, которые держал сам»"}, True),
    ("19:00", "Завтра - командировка на неделю почти без связи. Какая мысль догоняет?",
     {"P": "«Встанут ключевые задачи - кто без меня дожмёт?»",
      "A": "«Лишь бы не накосячили с деньгами и сроками»",
      "E": "«Неделя мимо - идеи опять будут стоять»",
      "I": "«Как они там друг с другом - не переругаются?»"}, False),
    ("21:30", "Вечером рассказываете близкому человеку про день. О чём - с удовольствием?",
     {"P": "«Дожал то, во что уже никто не верил»",
      "A": "«День прошёл ровно, по плану - ни одного пожара»",
      "E": "«Придумал ход - завтра попробуем»",
      "I": "«Ребята сами решили сложное, без меня»"}, False),
]

MANAGE = {
    "P": "Двигатель результата. Вы там, где надо дожать: сделка, продукт, кризис. Команда верит вам, потому что вы отвечаете делом.",
    "A": "Хранитель порядка. Цифры, процессы, риски - при вас бизнес работает предсказуемо, без пожаров. Вопрос «как правильно» - к вам.",
    "E": "Генератор будущего. Вы видите то, чего ещё нет, и заводите людей энергией старта. Вы меняете правила игры, а не играете по чужим.",
    "I": "Собиратель команды. Вы чувствуете людей, гасите конфликты до взрыва, вокруг вас хотят работать. Ваша валюта - доверие.",
}
LOWTXT = {
    "P": "Дожим до результата: решения повисают недоделанными. Закройте код Производителя (P) - усильте своего человека, который доводит до денег, или найдите такого.",
    "A": "Система: рост держится на героизме, а не на процессах. Закройте код Администратора (A) - усильте своего человека порядка или найдите такого.",
    "E": "Новое: бизнес едет по накатанной, прорывы откладываются. E добирают осторожно - партнёром или советником, не наёмным топом.",
    "I": "Люди: команда - набор исполнителей, а не организм. Закройте код Интегратора (I) - усильте того, кто уже держит отношения, или найдите такого.",
}
INSTR = {
    "P": "Приносите мне решение, не проблему. Коротко и по делу. Обещали - сделайте. Хотите убедить - покажите результат, не презентацию.",
    "A": "Сначала цифры и факты, потом выводы. Предупреждайте заранее, а не в последний день. Меняете правила - объясните зачем.",
    "E": "Приносите идеи сырыми - дошлифуем вместе. Не убивайте предложение словом «нереально». Напоминайте о сроках - я умею увлечься.",
    "I": "Говорите прямо - выдержу и услышу. Не играйте в политику - это единственное, что рушит моё доверие. Тяжело - скажите раньше, чем сгорите.",
}
FORECAST = {
    "A": "Вырастет хаос: деньги начнут теряться в процессах, которых нет. Первый симптом - никто не может назвать точную маржу.",
    "P": "Решения будут приниматься - и не доезжать до результата. Первый симптом - стратегия обновляется, выручка нет.",
    "E": "Бизнес упрётся в потолок текущей модели. Первый симптом - растёте медленнее рынка.",
    "I": "Команда начнёт тихо расходиться. Первый симптом - лучшие уходят «без причины».",
}
LEGEND = "Заглавная - сильный код · строчная - средний · «-» - провал"
DISC_SOFT = "Код - это стиль, не оценка способностей."
DISC_FULL = "Код - стиль управления, а не способности. Не основание для кадровых решений."
NOT_CEILING = "Код - это сегодняшний стиль, не потолок: стили двигаются вместе с контекстом."
SIGN = "По модели Ицхака Адизеса · YES•GROUP"

# ---------- БД ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY, username TEXT, name TEXT,
            first_source TEXT, invited_by INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS links(
            owner_id INTEGER, top_id INTEGER, created_at TEXT,
            UNIQUE(owner_id, top_id));
        CREATE TABLE IF NOT EXISTS results(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, owner_id INTEGER, subject TEXT,
            subject_name TEXT, p INT, a INT, e INT, i INT,
            profile TEXT, source TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS invites(
            owner_id INTEGER PRIMARY KEY, issued_at TEXT, reminded INT DEFAULT 0);
        """)

def upsert_user(m: Message, source, invited_by=None):
    with db() as c:
        row = c.execute("SELECT tg_id FROM users WHERE tg_id=?", (m.from_user.id,)).fetchone()
        if row is None:
            c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                      (m.from_user.id, m.from_user.username or "",
                       m.from_user.full_name, source, invited_by,
                       datetime.now(timezone.utc).isoformat()))
        elif invited_by:
            c.execute("INSERT OR IGNORE INTO links VALUES(?,?,?)",
                      (invited_by, m.from_user.id, datetime.now(timezone.utc).isoformat()))
    if invited_by:
        with db() as c:
            c.execute("INSERT OR IGNORE INTO links VALUES(?,?,?)",
                      (invited_by, m.from_user.id, datetime.now(timezone.utc).isoformat()))

def user_source(uid):
    with db() as c:
        r = c.execute("SELECT first_source, invited_by FROM users WHERE tg_id=?", (uid,)).fetchone()
    if not r:
        return "site"
    if r["first_source"] and not r["first_source"].startswith("inv"):
        return r["first_source"]
    if r["invited_by"]:
        return user_source(r["invited_by"])
    return "site"

# ---------- Скоринг ----------
def notation(s):
    srt = sorted(CODES, key=lambda c: -s[c])
    dom = [srt[0]]
    if s[srt[0]] - s[srt[1]] <= 4:
        dom.append(srt[1])
    vals = [s[c] for c in CODES]
    flat = max(vals) - min(vals) <= 2
    prof = "".join(c if (c in dom and not flat) else ("-" if s[c] <= -3 else c.lower()) for c in CODES)
    return dom, flat, prof

def bars(s):
    out = []
    for c in CODES:
        n = max(0, min(7, round((s[c] + 3) / 19 * 7)))
        out.append(f"{c} {'▰' * n}{'▱' * (7 - n)} {s[c]:>3}  {NAMES[c]}")
    return "\n".join(out)

# ---------- Состояние ----------
ST = {}  # uid -> dict(mode, scene, stage, ans, order, subject, subject_name, owner)

def new_state(subject="self", subject_name="", owner=None):
    return {"mode": "quiz", "scene": 0, "stage": "most",
            "ans": [{"m": None, "l": None} for _ in SCENES],
            "order": [random.sample(CODES, 4) for _ in SCENES],
            "subject": subject, "subject_name": subject_name, "owner": owner}

# ---------- Рендер сцен ----------
def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def scene_text(st):
    i = st["scene"]
    t, txt, opts, dbl = SCENES[i]
    proxy = st["subject"] == "proxy"
    nm = escape(st["subject_name"])
    head = f"<b>{t}</b> · сцена {i + 1} из 8"
    if proxy:
        q = f"Как поступил бы {nm}?" if st["stage"] == "most" else f"А что {nm} точно НЕ сделает?"
    else:
        q = "Ваш ход:" if st["stage"] == "most" else "А что вы точно НЕ сделаете?"
    lines = []
    for n, code in enumerate(st["order"][i], 1):
        mark = " ✓" if st["ans"][i]["m"] == code and st["stage"] == "least" else ""
        lines.append(f"{n}. {opts[code]}{mark}")
    return f"{head}\n\n{txt}\n\n<b>{q}</b>\n\n" + "\n".join(lines)

def scene_kb(st):
    i = st["scene"]
    btns = []
    for n, code in enumerate(st["order"][i], 1):
        if st["stage"] == "least" and st["ans"][i]["m"] == code:
            continue
        btns.append(InlineKeyboardButton(text=str(n), callback_data=f"ans:{code}"))
    rows = [btns]
    if i > 0 or st["stage"] == "least":
        rows.append([InlineKeyboardButton(text="← Назад", callback_data="back")])
    return kb(rows)

async def show_scene(msg_or_cb, st, edit=True):
    text, markup = scene_text(st), scene_kb(st)
    if edit and not isinstance(msg_or_cb, Message):
        await msg_or_cb.message.edit_text(text, reply_markup=markup)
    else:
        m = msg_or_cb if isinstance(msg_or_cb, Message) else msg_or_cb.message
        await m.answer(text, reply_markup=markup)

# ---------- Результат ----------
def build_result(s, subject="self", subject_name="", source="site"):
    dom, flat, prof = notation(s)
    if flat:
        return (f"<b>Ровный профиль</b>\n\nТак почти не бывает у живого руководителя. "
                f"Обычно это значит, что ответы были «как правильно», а не «как есть». "
                f"Пройдите день ещё раз - вспоминая, как было на самом деле: /start\n\n{SIGN}"), prof, dom
    who = f"Код {escape(subject_name)} (оценка со стороны)" if subject == "proxy" else "Ваш код по одному дню - предварительный"
    parts = [f"<i>{who}</i>",
             f"<b>{prof}</b> - {' + '.join(NAMES[c] for c in dom)}",
             f"<pre>{bars(s)}</pre><i>{LEGEND}</i>"]
    if len(dom) > 1:
        parts.append("<i>Коды идут вплотную - оба ваши, порядок может меняться день ото дня.</i>")
    parts.append("<b>Как вы управляете</b>\n" + "\n\n".join(MANAGE[c] for c in dom))
    lows = [c for c in sorted(CODES, key=lambda c: s[c]) if c not in dom][:2]
    if lows and subject == "self":
        parts.append("<b>Что проседает и кого добирать</b>\n" + "\n\n".join(LOWTXT[c] for c in lows)
                     + f"\n\n<i>{NOT_CEILING}</i>")
        parts.append("<b>Инструкция к себе - перешлите команде</b>\n"
                     + "\n\n".join(f"{NAMES[c]}: {INSTR[c]}" for c in dom))
        lowest = min(CODES, key=lambda c: s[c])
        if lowest not in dom:
            parts.append(f"<b>У руководителей с таким профилем при росте ×2 чаще всего ломается</b>\n"
                         f"{FORECAST[lowest]}\n<i>Из практики 497 проектов YES•GROUP</i>")
    disc = DISC_SOFT if subject == "self" else DISC_FULL
    parts.append(f"<i>{disc} · {SIGN}</i>")
    return "\n\n".join(parts), prof, dom

def result_kb(source, invited):
    rows = [[InlineKeyboardButton(text="Собрать карту моей команды", callback_data="invite")],
            [InlineKeyboardButton(text="Ответить за топа (он не в Telegram)", callback_data="proxy")],
            [InlineKeyboardButton(text="Карта команды", callback_data="team")]]
    if source in ("site", "ads"):
        rows.append([InlineKeyboardButton(text="Разбор карты команды →", url=REVIEW_URL)])
        rows.append([InlineKeyboardButton(text="Канал о бизнесе как активе", url=CHANNEL_URL)])
    return kb(rows)

async def finish(cb: CallbackQuery, st):
    uid = cb.from_user.id
    s = {c: 0 for c in CODES}
    for a in st["ans"]:
        if a["m"]:
            s[a["m"]] += 2
        if a["l"]:
            s[a["l"]] -= 1
    source = user_source(uid)
    subject = st["subject"]
    owner = st["owner"] or uid
    text, prof, dom = build_result(s, subject, st["subject_name"], source)
    with db() as c:
        c.execute("INSERT INTO results(user_id,owner_id,subject,subject_name,p,a,e,i,profile,source,ts) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (uid, owner, subject, st["subject_name"], s["P"], s["A"], s["E"], s["I"],
                   prof, source, datetime.now(timezone.utc).isoformat()))
    ST.pop(uid, None)
    await cb.message.edit_text(text, reply_markup=result_kb(source, owner != uid))
    if source == "atlas" and subject == "self":
        await cb.message.answer("Для группы Атласа: соберите карту команды и принесите её на встречу 6 (13 июля). "
                                "Разбор по шагам пришлю вместе с картой.")
    # уведомить владельца, если это топ по приглашению
    with db() as c:
        u = c.execute("SELECT invited_by FROM users WHERE tg_id=?", (uid,)).fetchone()
    if u and u["invited_by"] and subject == "self" and u["invited_by"] != uid:
        try:
            await bot.send_message(u["invited_by"],
                f"В вашу карту добавлен код: <b>{escape(cb.from_user.full_name)}</b> - <b>{prof}</b>.\n"
                f"Карта: /team")
        except Exception:
            pass
    if ADMIN:
        try:
            await bot.send_message(ADMIN, f"PAEI результат: {escape(cb.from_user.full_name)} "
                                          f"(@{cb.from_user.username}) · {prof} · subject={subject} · src={source}")
        except Exception:
            pass

# ---------- Хендлеры ----------
@dp.message(CommandStart())
async def start(m: Message):
    payload = ""
    if m.text and " " in m.text:
        payload = m.text.split(" ", 1)[1].strip()
    invited_by = None
    source = payload if payload in ("atlas", "site", "ads") else "site"
    inviter_name = ""
    if payload.startswith("inv_"):
        try:
            invited_by = int(payload[4:])
        except ValueError:
            invited_by = None
        if invited_by:
            with db() as c:
                r = c.execute("SELECT name FROM users WHERE tg_id=?", (invited_by,)).fetchone()
            inviter_name = r["name"] if r else "коллега"
            source = "inv"
    upsert_user(m, source, invited_by)
    if ADMIN:
        try:
            await bot.send_message(ADMIN, f"PAEI /start: {escape(m.from_user.full_name)} "
                                          f"(@{m.from_user.username}) · src={payload or 'direct'}")
        except Exception:
            pass
    if m.from_user.id in ST and ST[m.from_user.id]["mode"] == "quiz":
        st = ST[m.from_user.id]
        await m.answer(f"У вас незаконченный день - сцена {st['scene'] + 1} из 8. Продолжить?",
                       reply_markup=kb([[InlineKeyboardButton(text="Продолжить", callback_data="resume"),
                                         InlineKeyboardButton(text="Начать заново", callback_data="go")]]))
        return
    if invited_by:
        nm = escape(inviter_name)
        await m.answer(
            f"<b>Код руководителя</b>\n\nВас пригласил(а) <b>{nm}</b> определить ваш управленческий код.\n\n"
            f"Ваш результат увидите вы и {nm} - кроме вас двоих, никто. Это не оценка вашей работы: "
            f"плохих кодов нет, сильная команда состоит из разных кодов.\n\n"
            f"Проживите один рабочий день - 8 ситуаций, около 3 минут. Отвечайте, как есть, а не как правильно.",
            reply_markup=kb([[InlineKeyboardButton(text="Начать день - 9:00", callback_data="go")]]))
    else:
        await m.answer(
            "<b>Код руководителя</b>\n\nКакой вы руководитель - и кого не хватает вашей команде.\n\n"
            "Плохих кодов нет - у каждого сильны 1-2 из четырёх. Проживите один рабочий день: "
            "8 ситуаций, около 3 минут. Отвечайте, как есть, а не как правильно.\n\n"
            f"<i>{SIGN}</i>",
            reply_markup=kb([[InlineKeyboardButton(text="Начать день - 9:00", callback_data="go")]]))

@dp.callback_query(F.data == "go")
async def go(cb: CallbackQuery):
    ST[cb.from_user.id] = new_state()
    await show_scene(cb, ST[cb.from_user.id])
    await cb.answer()

@dp.callback_query(F.data == "resume")
async def resume(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st:
        await cb.answer("Сессия истекла, начните заново: /start", show_alert=True)
        return
    await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data.startswith("ans:"))
async def answer(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st or st["mode"] != "quiz":
        await cb.answer("Начните заново: /start", show_alert=True)
        return
    code = cb.data.split(":")[1]
    i = st["scene"]
    a = st["ans"][i]
    dbl = SCENES[i][3]
    if st["stage"] == "most":
        a["m"] = code
        if dbl:
            st["stage"] = "least"
        else:
            st["scene"] += 1
    else:
        if code == a["m"]:
            await cb.answer()
            return
        a["l"] = code
        st["scene"] += 1
        st["stage"] = "most"
    if st["scene"] >= len(SCENES):
        await finish(cb, st)
    else:
        await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data == "back")
async def back(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st:
        await cb.answer()
        return
    if st["stage"] == "least":
        st["ans"][st["scene"]]["m"] = None
        st["stage"] = "most"
    elif st["scene"] > 0:
        st["scene"] -= 1
        prev = st["ans"][st["scene"]]
        prev["l"] = None
        if SCENES[st["scene"]][3]:
            st["stage"] = "least"
        else:
            prev["m"] = None
            st["stage"] = "most"
    await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data == "invite")
async def invite(cb: CallbackQuery):
    uid = cb.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=inv_{uid}"
    with db() as c:
        c.execute("INSERT OR REPLACE INTO invites(owner_id, issued_at, reminded) VALUES(?,?,0)",
                  (uid, datetime.now(timezone.utc).isoformat()))
    fwd = (f"Я прошёл «Код руководителя» - проживаешь один рабочий день (3 минуты) и видишь "
           f"свой управленческий код. Пройди тоже: тебе - твой код и инструкция к себе, "
           f"мне - карта команды. {link}")
    await cb.message.answer(
        f"Перешлите это сообщение своим ключевым людям (3-5 человек):")
    await cb.message.answer(fwd)
    await cb.message.answer("Каждый прошедший автоматически появится в вашей карте - я пришлю уведомление. "
                            "Карта: /team")
    await cb.answer()

@dp.callback_query(F.data == "proxy")
async def proxy_start(cb: CallbackQuery):
    ST[cb.from_user.id] = {"mode": "ask_name"}
    await cb.message.answer("Как зовут человека, за которого отвечаете? (имя и роль, например: «Мария, директор»)\n\n"
                            "Отвечайте за него честно - как он поступил бы, а не как вам хотелось бы. "
                            "В карте результат будет помечен «оценка со стороны».")
    await cb.answer()

@dp.message(F.text, ~F.text.startswith("/"))
async def text_input(m: Message):
    st = ST.get(m.from_user.id)
    if st and st.get("mode") == "ask_name":
        name = m.text.strip()[:60]
        ST[m.from_user.id] = new_state(subject="proxy", subject_name=name, owner=m.from_user.id)
        await m.answer(f"Отвечаем за <b>{escape(name)}</b>. Тот же день, 8 сцен - как поступил бы он.",
                       reply_markup=kb([[InlineKeyboardButton(text="Начать день - 9:00", callback_data="resume")]]))
        return
    await m.answer("Начать тест: /start · Карта команды: /team")

@dp.callback_query(F.data == "team")
async def team_cb(cb: CallbackQuery):
    await send_team(cb.from_user.id, cb.message)
    await cb.answer()

@dp.message(Command("team"))
async def team_cmd(m: Message):
    await send_team(m.from_user.id, m)

async def send_team(uid, msg):
    with db() as c:
        own = c.execute("SELECT * FROM results WHERE user_id=? AND subject='self' ORDER BY id DESC LIMIT 1",
                        (uid,)).fetchone()
        tops = c.execute("""
            SELECT r.*, u.name AS uname, u.username AS ulogin FROM results r
            JOIN links l ON l.top_id = r.user_id AND l.owner_id = ?
            JOIN users u ON u.tg_id = r.user_id
            WHERE r.subject='self' AND r.id IN
                (SELECT MAX(id) FROM results WHERE subject='self' GROUP BY user_id)
            ORDER BY r.ts""", (uid,)).fetchall()
        proxies = c.execute("""
            SELECT * FROM results WHERE owner_id=? AND subject='proxy' AND id IN
                (SELECT MAX(id) FROM results WHERE owner_id=? AND subject='proxy' GROUP BY subject_name)
            ORDER BY ts""", (uid, uid)).fetchall()
    if not own and not tops and not proxies:
        await msg.answer("Карта пока пустая. Сначала пройдите день сами: /start")
        return
    lines = []
    dom_by_tops = set()
    if own:
        lines.append(f"Вы: <b>{own['profile']}</b>")
    for r in tops:
        nm = escape(r["uname"] or "")
        login = f" (@{r['ulogin']})" if r["ulogin"] else ""
        lines.append(f"{nm}{login}: <b>{r['profile']}</b>")
        d, _, _ = notation({"P": r["p"], "A": r["a"], "E": r["e"], "I": r["i"]})
        dom_by_tops.update(d)
    for r in proxies:
        lines.append(f"{escape(r['subject_name'])}: <b>{r['profile']}</b> <i>(со стороны)</i>")
        d, _, _ = notation({"P": r["p"], "A": r["a"], "E": r["e"], "I": r["i"]})
        dom_by_tops.update(d)
    closed = " · ".join(sorted(dom_by_tops)) if dom_by_tops else "-"
    open_codes = [c for c in CODES if c not in dom_by_tops]
    own_dom = set()
    if own:
        own_dom, _, _ = notation({"P": own["p"], "A": own["a"], "E": own["e"], "I": own["i"]})
        own_dom = set(own_dom)
    only_owner = [c for c in open_codes if c in own_dom]
    fully_open = [c for c in open_codes if c not in own_dom]
    parts = ["<b>Карта команды</b>", "\n".join(lines),
             f"Закрыты командой: <b>{closed}</b>"]
    if only_owner:
        parts.append(f"Закрыты только вами: <b>{' · '.join(only_owner)}</b> - без вас команда "
                     f"эти коды не держит. Это и есть зона, где бизнес стоит на вас.")
    if fully_open:
        parts.append(f"Не закрыты никем: <b>{' · '.join(fully_open)}</b>")
    parts.append("<i>Карта показывает предпочтения людей, а не их ценность. Дырка в коде - задача "
                 "на найм или усиление, не приговор команде.</i>")
    parts.append(f"<i>Несколько бизнесов? В этой версии - карта одной ключевой команды.</i>")
    src = user_source(uid)
    if src == "atlas":
        parts.append("Принесите карту на встречу 6 (13.07) + одно решение по одной дыре.")
    parts.append(f"<i>{DISC_FULL}</i>")
    kb_rows = [[InlineKeyboardButton(text="Пригласить ещё", callback_data="invite")]]
    if src in ("site", "ads"):
        kb_rows.append([InlineKeyboardButton(text="Разбор карты команды →", url=REVIEW_URL)])
    await msg.answer("\n\n".join(parts), reply_markup=kb(kb_rows))

# ---------- Фоновые задачи ----------
async def reminders():
    while True:
        await asyncio.sleep(3600)
        try:
            now = datetime.now(timezone.utc)
            with db() as c:
                rows = c.execute("SELECT * FROM invites WHERE reminded=0").fetchall()
                for r in rows:
                    issued = datetime.fromisoformat(r["issued_at"])
                    if (now - issued).total_seconds() < 86400:
                        continue
                    got = c.execute("SELECT COUNT(*) n FROM links WHERE owner_id=?",
                                    (r["owner_id"],)).fetchone()["n"]
                    if got == 0:
                        try:
                            await bot.send_message(r["owner_id"],
                                "Карта команды ждёт: вчера вы взяли ссылку для топов, но пока никто не прошёл. "
                                "Перешлите приглашение 3-5 ключевым людям - карта соберётся сама. /team")
                        except Exception:
                            pass
                    c.execute("UPDATE invites SET reminded=1 WHERE owner_id=?", (r["owner_id"],))
        except Exception as e:
            log.error("reminders: %s", e)

async def backup():
    while True:
        await asyncio.sleep(86400)
        if not ADMIN:
            continue
        try:
            await bot.send_document(ADMIN, FSInputFile(DB_PATH),
                                    caption=f"PAEI бэкап {datetime.now(timezone.utc).date()}")
        except Exception as e:
            log.error("backup: %s", e)

# ---------- main ----------
async def main():
    global BOT_USERNAME
    init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    log.info("PAEI bot @%s started, db=%s", BOT_USERNAME, DB_PATH)
    asyncio.create_task(reminders())
    asyncio.create_task(backup())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
