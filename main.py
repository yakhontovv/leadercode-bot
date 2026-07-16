"""
PAEI-бот «Код руководителя / Leader Code» - слой 1 «Один день» + карта команды.
YES-GROUP. По модели Ицхака Адизеса (вопросы оригинальные). Двуязычный: RU/EN.

Флоу: /start (source: atlas|site|ads|inv_<id>) -> выбор языка -> вводный экран
-> симуляция дня (8 сцен, в 2/3/6 второй тап "точно не сделаю") -> предварительный
код + инструкция к себе + прогноз -> "Собрать карту команды" / "Ответить за топа".
/team - карта: закрыто топами / только вами.

Скоринг: выбор +2, "точно не" -1. Пороги (симуляция seed 42, ВРЕМЕННЫЕ до
100+ живых): пара при отрыве <=4, "-" при <= -3, флэт при max-min <= 2.

Данные: sqlite на persistent volume (DB_PATH). Язык хранится в users.lang.
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
LEADS_CHAT_ID = os.environ.get("LEADS_CHAT_ID", "-1003550581190").strip()
LEADS_TARGET = int(LEADS_CHAT_ID) if LEADS_CHAT_ID.lstrip("-").isdigit() else LEADS_CHAT_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("paei")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
BOT_USERNAME = ""

CODES = ["P", "A", "E", "I"]
LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

# ---------- Контент: двуязычный словарь ----------
# Каждая сцена: (время, текст, {код: реплика}, двойной_тап)
SCENES_RU = [
    ("9:00", "Вы в офисе. Первая встреча в 11:00 - впереди свободное окно на два часа. Что сделаете на самом деле?",
     {"P": "«Наконец-то поработаю: разгребу то, что горит»",
      "A": "«Сяду в цифры - сведу хвосты и план недели»",
      "E": "«Покручу идею, которая всю неделю в голове»",
      "I": "«Пройдусь по людям - услышать, чем дышат»"}, False),
    ("10:40", "Сообщение в чате: команда завалила срок по важному проекту. Первая реакция - честно:",
     {"P": "«Дайте сюда, сам доделаю. Быстрее, чем объяснять»",
      "A": "«Почему мы узнали об этом в последний день?»",
      "E": "«Стоп, а этот срок вообще еще нужен?»",
      "I": "«Соберемся и разберем, что между нами сломалось»"}, True),
    ("11:30", "Планерка. Два самых сильных человека в команде схлестнулись - на повышенных.",
     {"P": "«Все, время вышло: решение за мной, работаем»",
      "A": "«Давайте по фактам: кто за это отвечает?»",
      "E": "«Оба решения - вчерашние. Давайте переосмыслим саму задачу»",
      "I": "«Дело не в задаче - между вами что-то не так. Давайте об этом»"}, True),
    ("13:30", "Обед с партнером. Он приносит большой, но сырой проект.",
     {"P": "«Вижу, как дожать до денег, - захожу»",
      "A": "«Сначала считаем: риски, ресурсы, план»",
      "E": "«Сырой - значит, правила еще не написаны. Интересно»",
      "I": "«Смотря с кем делать. С правильными людьми - да»"}, False),
    ("15:00", "Собеседование: сильный финалист на ключевую роль. Какой довод для вас решающий?",
     {"P": "«Показал результаты - видно, что сделал руками»",
      "A": "«Системный человек: будет держать свою зону в порядке»",
      "E": "«Мыслит шире роли - видит то, чего еще нет»",
      "I": "«За ним пойдут - ему будут верить»"}, False),
    ("17:30", "Ловите себя на мысли: нагрузка выросла вдвое, вы перестали успевать. Как выкручиваетесь - честно?",
     {"P": "«Поднажму сам - но срежу все лишнее»",
      "A": "«Строю структуру: роли, процессы, отчеты»",
      "E": "«Ломаю саму модель работы - так дальше нельзя»",
      "I": "«Доверю людям больше - отдам куски, которые держал сам»"}, True),
    ("19:00", "Завтра - командировка на неделю почти без связи. Какая мысль догоняет?",
     {"P": "«Встанут ключевые задачи - кто без меня дожмет?»",
      "A": "«Лишь бы не накосячили с деньгами и сроками»",
      "E": "«Неделя мимо - идеи опять будут стоять»",
      "I": "«Как они там друг с другом - не переругаются?»"}, False),
    ("21:30", "Вечером рассказываете близкому человеку про день. О чем - с удовольствием?",
     {"P": "«Дожал то, во что уже никто не верил»",
      "A": "«День прошел ровно, по плану - ни одного пожара»",
      "E": "«Придумал ход - завтра попробуем»",
      "I": "«Ребята сами решили сложное, без меня»"}, False),
]

SCENES_EN = [
    ("9:00", "You're at the office. Your first meeting is at 11:00 - you've got a two-hour window. What do you actually do?",
     {"P": "“Get to work - knock out the most urgent stuff while it's quiet”",
      "A": "“Go through the numbers - tie up loose ends, plan the week”",
      "E": "“Think through the idea I can't let go of”",
      "I": "“Walk the floor - talk to people, hear how the team is doing”"}, False),
    ("10:40", "Message in the team chat: they've blown a major deadline. Gut reaction - be honest:",
     {"P": "“Hand it over, I'll finish it myself. Faster than explaining”",
      "A": "“Why are we only hearing about this on the last day?”",
      "E": "“Hold on - do we even still need this deadline?”",
      "I": "“Let's sit down and work out what broke down between us”"}, True),
    ("11:30", "Team meeting. Your two strongest people are going at each other - voices raised.",
     {"P": "“Time's up. I'll make the call - everyone back to work”",
      "A": "“Stick to the facts: whose responsibility is this?”",
      "E": "“Both solutions are yesterday's thinking. Let's rethink the problem itself”",
      "I": "“This isn't about the task - something's off between you two. Let's talk about that”"}, True),
    ("13:30", "Lunch with a partner. He pitches a big but half-baked project.",
     {"P": "“If I can see the path to the money, I'm in”",
      "A": "“Numbers first: risks, resources, a plan - then we talk”",
      "E": "“Half-baked means no rules yet. Now I'm interested”",
      "I": "“Depends who's doing it. With the right people - yes”"}, False),
    ("15:00", "Interview: a strong finalist for a key role. What seals the deal for you?",
     {"P": "“A track record - things they've actually built”",
      "A": "“A systems person: they'll keep their area in order”",
      "E": "“They think beyond the role - see what isn't there yet”",
      "I": "“People will follow them - and trust them”"}, False),
    ("17:30", "It hits you: your workload has doubled and you're not keeping up. How do you cope - honestly?",
     {"P": "“Push harder myself - and cut everything non-essential”",
      "A": "“Build structure: roles, processes, reporting”",
      "E": "“Blow up the whole operating model - this can't go on”",
      "I": "“Start trusting the team - hand over what I've been holding onto”"}, True),
    ("19:00", "Tomorrow you fly out for a week, mostly off the grid. Which thought nags at you?",
     {"P": "“Key deals will stall - who'll push them over the line?”",
      "A": "“Just don't let them mess up the money or the deadlines”",
      "E": "“A week lost - my ideas will just sit there. Again”",
      "I": "“How will they get along without me - will it blow up?”"}, False),
    ("21:30", "That evening, you tell someone close to you about your day. What do you bring up - with a smile?",
     {"P": "“Pulled off the thing nobody believed in”",
      "A": "“The day ran like clockwork - not a single fire”",
      "E": "“Came up with a play - we're trying it tomorrow”",
      "I": "“The team cracked a hard one without me. Loved that”"}, False),
]

TXT = {
    "ru": {
        "scenes": SCENES_RU,
        "names": {"P": "Производитель", "A": "Администратор", "E": "Предприниматель", "I": "Интегратор"},
        "manage": {
            "P": "Двигатель результата. Вы там, где надо дожать: сделка, продукт, кризис. Команда верит вам, потому что вы отвечаете делом.",
            "A": "Хранитель порядка. Цифры, процессы, риски - при вас бизнес работает предсказуемо, без пожаров. Вопрос «как правильно» - к вам.",
            "E": "Генератор будущего. Вы видите то, чего еще нет, и заводите людей энергией старта. Вы меняете правила игры, а не играете по чужим.",
            "I": "Собиратель команды. Вы чувствуете людей, гасите конфликты до взрыва, вокруг вас хотят работать. Ваша валюта - доверие.",
        },
        "lowtxt": {
            "P": "Дожим до результата: решения повисают недоделанными. Закройте код Производителя (P) - усильте своего человека, который доводит до денег, или найдите такого.",
            "A": "Система: рост держится на героизме, а не на процессах. Закройте код Администратора (A) - усильте своего человека порядка или найдите такого.",
            "E": "Новое: бизнес едет по накатанной, прорывы откладываются. E добирают осторожно - партнером или советником, не наемным топом.",
            "I": "Люди: команда - набор исполнителей, а не организм. Закройте код Интегратора (I) - усильте того, кто уже держит отношения, или найдите такого.",
        },
        "instr": {
            "P": "Приносите мне решение, не проблему. Коротко и по делу. Обещали - сделайте. Хотите убедить - покажите результат, не презентацию.",
            "A": "Сначала цифры и факты, потом выводы. Предупреждайте заранее, а не в последний день. Меняете правила - объясните зачем.",
            "E": "Приносите идеи сырыми - дошлифуем вместе. Не убивайте предложение словом «нереально». Напоминайте о сроках - я умею увлечься.",
            "I": "Говорите прямо - выдержу и услышу. Не играйте в политику - это единственное, что рушит мое доверие. Тяжело - скажите раньше, чем сгорите.",
        },
        "forecast": {
            "A": "Вырастет хаос: деньги начнут теряться в процессах, которых нет. Первый симптом - никто не может назвать точную маржу.",
            "P": "Решения будут приниматься - и не доезжать до результата. Первый симптом - стратегия обновляется, выручка нет.",
            "E": "Бизнес упрется в потолок текущей модели. Первый симптом - растете медленнее рынка.",
            "I": "Команда начнет тихо расходиться. Первый симптом - лучшие уходят «без причины».",
        },
        "legend": "Заглавная - сильный код · строчная - средний · «-» - провал",
        "disc_soft": "Код - это стиль, не оценка способностей.",
        "disc_full": "Код - стиль управления, а не способности. Не основание для кадровых решений.",
        "not_ceiling": "Код - это сегодняшний стиль, не потолок: стили двигаются вместе с контекстом.",
        "sign": "По модели Ицхака Адизеса · YES•GROUP",
        "intro_own": ("<b>Код руководителя</b> - игра-симуляция от YES•GROUP.\n\n"
                      "За 3 минуты вы проживете один рабочий день: 8 обычных ситуаций. "
                      "В каждой выбираете, как поступили бы на самом деле, а не как правильно.\n\n"
                      "По ответам бот соберет ваш управленческий код по модели Ицхака Адизеса - "
                      "в чем вы сильны и кого не хватает команде. Плохих кодов нет: у каждого "
                      "сильны 1-2 из четырех.\n\nГотовы? День начинается в 9:00."),
        "intro_inv": ("<b>Код руководителя</b> - игра-симуляция от YES•GROUP.\n\n"
                      "Вас пригласил(а) <b>{name}</b>, чтобы определить ваш управленческий код. "
                      "Результат увидите только вы и {name} - больше никто. Это не оценка работы: "
                      "плохих кодов нет, сильная команда состоит из разных.\n\n"
                      "За 3 минуты вы проживете один рабочий день: 8 ситуаций. В каждой выбирайте, "
                      "как поступили бы на самом деле, а не как правильно.\n\nГотовы? День начинается в 9:00."),
        "start_btn": "Начать день - 9:00",
        "counter": "сцена {n} из 8",
        "q_most": "Ваш ход:",
        "q_least": "А что вы точно НЕ сделаете?",
        "q_proxy_most": "Как поступил бы {name}?",
        "q_proxy_least": "А что {name} точно НЕ сделает?",
        "back": "← Назад",
        "flat": ("<b>Ровный профиль - редкий случай.</b> Обычно у живого руководителя одни оси "
                 "заметно сильнее других: на них и держится команда.\n\n"
                 "Если в сценах ты выбирал, как стоило бы поступить - пройди день еще раз и "
                 "отвечай, как реально делаешь в моменте, даже если это неидеально. Именно там "
                 "виден твой настоящий код: /start\n\n"
                 "Если же ты и правда разносторонний - это сила. Но команде сложнее понять, на "
                 "что опереться. Твой расклад ниже."),
        "res_head_self": "Ваш код по одному дню - предварительный",
        "res_head_proxy": "Код {name} (оценка со стороны)",
        "pair_note": "Коды идут вплотную - оба ваши, порядок может меняться день ото дня.",
        "h_manage": "Как вы управляете",
        "h_sags": "Что проседает и кого добирать",
        "h_instr": "Инструкция к себе - перешлите команде",
        "h_forecast": "У руководителей с таким профилем при росте ×2 чаще всего ломается",
        "forecast_anchor": "Из практики проектов YES•GROUP в 30+ отраслях",
        "btn_invite": "Узнай коды своей команды",
        "btn_proxy": "Пройти за члена команды",
        "btn_team": "Карта команды",
        "btn_review": "Получить разбор карты",
        "btn_channel": "Подпишись на мой ТГ-канал",
        "review_sent": "Заявка на разбор принята - свяжусь с вами в ближайшее время.",
        "atlas_note": ("Для группы Атласа: соберите карту команды и принесите ее на встречу 6 (13 июля). "
                       "Разбор по шагам пришлю вместе с картой."),
        "owner_notif": "В вашу карту добавлен код: <b>{name}</b> - <b>{prof}</b>.\nКарта: /team",
        "invite_wrapper": "<b>Готовое приглашение для твоей команды.</b> Перешли его 3-5 ключевым людям:",
        "invite_fwd": ("Я прошел «Код руководителя» - за 3 минуты проживаешь один рабочий день и видишь "
                       "свой управленческий код: в чем ты силен и инструкцию, как с тобой работать. "
                       "Пройди и ты, это 3 минуты: {link}"),
        "invite_after": ("Каждый прошедший сам появится в твоей карте - пришлю уведомление. Карта: /team"),
        "proxy_ask": ("За кого проходите? Имя и роль - например, «Мария, руководитель отдела».\n\n"
                      "Проходите за него честно - как поступил бы он, а не как вам хотелось бы. "
                      "В карте результат будет помечен «оценка со стороны»."),
        "proxy_confirm": "Проходим за <b>{name}</b>. Тот же день, 8 сцен - как поступил бы он.",
        "resume_prompt": "У вас незаконченный день - сцена {n} из 8. Продолжить?",
        "resume_continue": "Продолжить",
        "resume_startover": "Начать заново",
        "fallback": "Начать тест: /start · Карта команды: /team",
        "reminder": ("Карта команды ждет: вчера вы взяли ссылку для топов, но пока никто не прошел. "
                     "Перешлите приглашение 3-5 ключевым людям - карта соберется сама. /team"),
        "team_title": "Карта команды",
        "team_you": "Вы: <b>{prof}</b>",
        "team_side": "(со стороны)",
        "team_closed": "Закрыты командой: <b>{codes}</b>",
        "team_only_you": ("Закрыты только вами: <b>{codes}</b> - без вас команда эти коды не держит. "
                          "Это и есть зона, где бизнес стоит на вас."),
        "team_open": "Не закрыты никем: <b>{codes}</b>",
        "team_note": ("Карта показывает предпочтения людей, а не их ценность. Пробел в коде - задача "
                      "на найм или усиление, не приговор команде."),
        "team_multi": "Несколько бизнесов? В этой версии - карта одной ключевой команды.",
        "team_atlas": "Принесите карту на встречу 6 (13.07) + одно решение по одному пробелу.",
        "team_empty": "Карта пока пустая. Сначала пройдите день сами: /start",
        "team_invite_more": "Пригласить еще",
    },
    "en": {
        "scenes": SCENES_EN,
        "names": {"P": "Producer", "A": "Administrator", "E": "Entrepreneur", "I": "Integrator"},
        "manage": {
            "P": "The results engine. You're wherever things need pushing over the line: the deal, the product, the crisis. Your team trusts you because you answer with action.",
            "A": "The keeper of order. Numbers, processes, risks - with you, the business runs predictably, no fires. “How do we do this right?” - that question comes to you.",
            "E": "The future-maker. You see what doesn't exist yet and get people fired up to chase it. You change the rules of the game instead of playing by someone else's.",
            "I": "The team builder. You read people, defuse conflicts before they blow up, and people want to be on your team. Trust is your currency.",
        },
        "lowtxt": {
            "P": "Follow-through: decisions hang unfinished. Cover the Producer (P) code - grow the person who drives things to revenue, or find one.",
            "A": "Systems: growth runs on heroics, not process. Cover the Administrator (A) code - grow your systems person, or find one.",
            "E": "Innovation: the business coasts on momentum and the next breakthrough keeps slipping. Add E carefully - as a partner or advisor, not a hired executive.",
            "I": "People: the team is a set of doers, not an organism. Cover the Integrator (I) code - grow the one who already holds the relationships, or find one.",
        },
        "instr": {
            "P": "Bring me solutions, not problems. Short and to the point. You promised? Then deliver. Want to convince me? Show results, not slides.",
            "A": "Facts and numbers first, conclusions second. Warn me early, not on the last day. Changing the rules? Tell me why.",
            "E": "Bring ideas raw - we'll shape them together. Don't kill an idea with “that'll never work”. Nudge me about deadlines - I get carried away.",
            "I": "Talk straight - I can take it, and I'll hear you. Don't play politics; it's the one thing that kills my trust. If you're struggling, say so before you burn out.",
        },
        "forecast": {
            "A": "Chaos grows: money starts leaking through processes that don't exist. First symptom - nobody can name your actual margin.",
            "P": "Decisions get made - and never turn into results. First symptom - the strategy keeps updating, revenue doesn't.",
            "E": "The business hits the ceiling of its current model. First symptom - you're growing slower than the market.",
            "I": "The team starts quietly drifting away. First symptom - your best people leave “for no reason”.",
        },
        "legend": "Capital = strong code · lowercase = moderate · “-” = a gap",
        "disc_soft": "Your code is a style, not a measure of ability.",
        "disc_full": "The code describes management style, not ability. Not a basis for HR decisions.",
        "not_ceiling": "Your code is today's style, not a ceiling - styles shift as your context shifts.",
        "sign": "Based on Ichak Adizes' model · YES•GROUP",
        "intro_own": ("<b>Leader Code</b> - a 3-minute simulation game from YES•GROUP.\n\n"
                      "You'll live through one workday: 8 everyday situations. "
                      "For each, pick what you'd actually do - not the ‘right’ answer.\n\n"
                      "From your answers, the bot maps your management code (based on Ichak Adizes' model) - "
                      "where you're strong and who your team is missing. There are no bad codes: "
                      "everyone's strong in one or two of the four.\n\nReady? The day starts at 9:00."),
        "intro_inv": ("<b>Leader Code</b> - a 3-minute simulation game from YES•GROUP.\n\n"
                      "<b>{name}</b> invited you to map your management code. Only you and {name} "
                      "will see the result - no one else. This isn't a performance review: there are "
                      "no bad codes, and the strongest teams are built from different ones.\n\n"
                      "You'll live through one workday: 8 situations. For each, pick "
                      "what you'd actually do - not the ‘right’ answer.\n\nReady? The day starts at 9:00."),
        "start_btn": "Start the day - 9:00",
        "counter": "scene {n} of 8",
        "q_most": "Your move:",
        "q_least": "And what would you definitely NOT do?",
        "q_proxy_most": "What would {name} do?",
        "q_proxy_least": "And what would {name} never do?",
        "back": "← Back",
        "flat": ("<b>A flat profile is rare.</b> In a real leader, some axes are usually clearly "
                 "stronger than others - that's what the team leans on.\n\n"
                 "If you were picking the answer you thought was right, run the day again and "
                 "answer the way you actually act in the moment, even if it's not ideal. That's "
                 "where your real code shows: /start\n\n"
                 "But if you truly are all-round - that's a strength. It just makes it harder for "
                 "the team to know what to lean on. Your breakdown is below."),
        "res_head_self": "Your one-day code - a first read",
        "res_head_proxy": "{name}'s code (an outside view)",
        "pair_note": "The codes run neck and neck - both are yours; the order may shift from day to day.",
        "h_manage": "How you lead",
        "h_sags": "Where you're thin - and who to bring in",
        "h_instr": "Working with me - forward this to your team",
        "h_forecast": "For leaders with this profile, here's what usually breaks first at 2x growth",
        "forecast_anchor": "From YES•GROUP projects across 30+ industries",
        "btn_invite": "See your team's codes",
        "btn_proxy": "Take it for a team member",
        "btn_team": "Team map",
        "btn_review": "Get your map reviewed",
        "btn_channel": "Follow my Telegram channel",
        "review_sent": "Request received - I'll be in touch shortly.",
        "atlas_note": ("For the Atlas group: build your team map and bring it to meeting 6 (July 13). "
                       "I'll send the step-by-step review together with the map."),
        "owner_notif": "A code was added to your map: <b>{name}</b> - <b>{prof}</b>.\nMap: /team",
        "invite_wrapper": "<b>Here's a ready invite for your team.</b> Forward it to 3-5 key people:",
        "invite_fwd": ("I just took Leader Code - in 3 minutes you live through one workday and get "
                       "your management code: where you're strong, plus a “working with me” memo. "
                       "Take it too: {link}"),
        "invite_after": ("Everyone who takes it shows up on your map automatically - I'll ping you. Map: /team"),
        "proxy_ask": ("Who are you taking it for? Name and role - e.g. “Maria, head of sales”.\n\n"
                      "Answer honestly - as they'd actually act, not as you'd like them to. "
                      "The map will show it as an outside view."),
        "proxy_confirm": "Taking it for <b>{name}</b>. The same day, 8 scenes - as they would act.",
        "resume_prompt": "You have an unfinished day - scene {n} of 8. Pick up where you left off?",
        "resume_continue": "Continue",
        "resume_startover": "Start over",
        "fallback": "Take the test: /start · Team map: /team",
        "reminder": ("Your team map is waiting: you grabbed the link yesterday, but no one has taken it yet. "
                     "Forward the invite to 3-5 key people - the map builds itself. /team"),
        "team_title": "Team map",
        "team_you": "You: <b>{prof}</b>",
        "team_side": "(outside view)",
        "team_closed": "Covered by the team: <b>{codes}</b>",
        "team_only_you": ("Covered only by you: <b>{codes}</b> - without you, the team doesn't hold these codes. "
                          "That's exactly where the business still rests on you."),
        "team_open": "Not covered by anyone: <b>{codes}</b>",
        "team_note": ("The map shows people's preferences, not their worth. A gap in a code is a hiring or "
                      "development question - not a verdict on the team."),
        "team_multi": "Several businesses? This version maps one core team.",
        "team_atlas": "Bring the map to meeting 6 (July 13) + one decision on one gap.",
        "team_empty": "Your map is empty. Take the day yourself first: /start",
        "team_invite_more": "Invite more",
    },
}


def T(lang):
    return TXT.get(lang, TXT[DEFAULT_LANG])


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
        # миграции
        for col, ddl in (("lang", "ALTER TABLE users ADD COLUMN lang TEXT"),
                         ("starts", "ALTER TABLE users ADD COLUMN starts INTEGER DEFAULT 0")):
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass  # колонка уже есть

def upsert_user(m: Message, source, invited_by=None):
    is_new = False
    with db() as c:
        row = c.execute("SELECT tg_id FROM users WHERE tg_id=?", (m.from_user.id,)).fetchone()
        if row is None:
            is_new = True
            c.execute("INSERT INTO users(tg_id,username,name,first_source,invited_by,created_at) "
                      "VALUES(?,?,?,?,?,?)",
                      (m.from_user.id, m.from_user.username or "",
                       m.from_user.full_name, source, invited_by,
                       datetime.now(timezone.utc).isoformat()))
    if invited_by:
        with db() as c:
            c.execute("INSERT OR IGNORE INTO links VALUES(?,?,?)",
                      (invited_by, m.from_user.id, datetime.now(timezone.utc).isoformat()))
    return is_new

def set_lang(uid, lang):
    with db() as c:
        c.execute("UPDATE users SET lang=? WHERE tg_id=?", (lang, uid))

def get_lang(uid):
    with db() as c:
        r = c.execute("SELECT lang FROM users WHERE tg_id=?", (uid,)).fetchone()
    if r and r["lang"] in LANGS:
        return r["lang"]
    return DEFAULT_LANG

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

def bars(s, lang):
    names = T(lang)["names"]
    out = []
    for c in CODES:
        n = max(0, min(7, round((s[c] + 3) / 19 * 7)))
        out.append(f"{c} {'▰' * n}{'▱' * (7 - n)} {s[c]:>3}  {names[c]}")
    return "\n".join(out)

# ---------- Состояние ----------
ST = {}  # uid -> dict

def new_state(lang, subject="self", subject_name="", owner=None):
    return {"mode": "quiz", "scene": 0, "stage": "most", "lang": lang,
            "ans": [{"m": None, "l": None} for _ in SCENES_RU],
            "order": [random.sample(CODES, 4) for _ in SCENES_RU],
            "subject": subject, "subject_name": subject_name, "owner": owner}

# ---------- Рендер сцен ----------
def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def scene_text(st):
    lang = st["lang"]
    t = T(lang)
    i = st["scene"]
    tm, txt, opts, dbl = t["scenes"][i]
    proxy = st["subject"] == "proxy"
    nm = escape(st["subject_name"])
    head = f"<b>{tm}</b> · " + t["counter"].format(n=i + 1)
    if proxy:
        q = t["q_proxy_most"].format(name=nm) if st["stage"] == "most" else t["q_proxy_least"].format(name=nm)
    else:
        q = t["q_most"] if st["stage"] == "most" else t["q_least"]
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
        rows.append([InlineKeyboardButton(text=T(st["lang"])["back"], callback_data="back")])
    return kb(rows)

async def show_scene(msg_or_cb, st, edit=True):
    text, markup = scene_text(st), scene_kb(st)
    if edit and not isinstance(msg_or_cb, Message):
        await msg_or_cb.message.edit_text(text, reply_markup=markup)
    else:
        m = msg_or_cb if isinstance(msg_or_cb, Message) else msg_or_cb.message
        await m.answer(text, reply_markup=markup)

# ---------- Результат ----------
def build_result(s, lang, subject="self", subject_name="", source="site"):
    t = T(lang)
    dom, flat, prof = notation(s)
    who = t["res_head_proxy"].format(name=escape(subject_name)) if subject == "proxy" else t["res_head_self"]
    parts = []
    if flat:
        parts.append(t["flat"])
    parts += [f"<i>{who}</i>",
             f"<b>{prof}</b> - {' + '.join(t['names'][c] for c in dom)}",
             f"<pre>{bars(s, lang)}</pre><i>{t['legend']}</i>"]
    if len(dom) > 1:
        parts.append(f"<i>{t['pair_note']}</i>")
    parts.append(f"<b>{t['h_manage']}</b>\n" + "\n\n".join(t["manage"][c] for c in dom))
    lows = [c for c in sorted(CODES, key=lambda c: s[c]) if c not in dom][:2]
    if lows and subject == "self":
        parts.append(f"<b>{t['h_sags']}</b>\n" + "\n\n".join(t["lowtxt"][c] for c in lows)
                     + f"\n\n<i>{t['not_ceiling']}</i>")
        parts.append(f"<b>{t['h_instr']}</b>\n"
                     + "\n\n".join(f"{t['names'][c]}: {t['instr'][c]}" for c in dom))
        lowest = min(CODES, key=lambda c: s[c])
        if lowest not in dom:
            parts.append(f"<b>{t['h_forecast']}</b>\n"
                         f"{t['forecast'][lowest]}\n<i>{t['forecast_anchor']}</i>")
    disc = t["disc_soft"] if subject == "self" else t["disc_full"]
    parts.append(f"<i>{disc} · {t['sign']}</i>")
    return "\n\n".join(parts), prof, dom

def result_kb(source, lang):
    t = T(lang)
    rows = [[InlineKeyboardButton(text=t["btn_invite"], callback_data="invite")],
            [InlineKeyboardButton(text=t["btn_proxy"], callback_data="proxy")],
            [InlineKeyboardButton(text=t["btn_team"], callback_data="team")]]
    if source in ("site", "ads"):
        rows.append([InlineKeyboardButton(text=t["btn_review"], callback_data="review")])
        rows.append([InlineKeyboardButton(text=t["btn_channel"], url=CHANNEL_URL)])
    return kb(rows)

async def finish(cb: CallbackQuery, st):
    uid = cb.from_user.id
    lang = st["lang"]
    s = {c: 0 for c in CODES}
    for a in st["ans"]:
        if a["m"]:
            s[a["m"]] += 2
        if a["l"]:
            s[a["l"]] -= 1
    source = user_source(uid)
    subject = st["subject"]
    owner = st["owner"] or uid
    text, prof, dom = build_result(s, lang, subject, st["subject_name"], source)
    with db() as c:
        c.execute("INSERT INTO results(user_id,owner_id,subject,subject_name,p,a,e,i,profile,source,ts) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (uid, owner, subject, st["subject_name"], s["P"], s["A"], s["E"], s["I"],
                   prof, source, datetime.now(timezone.utc).isoformat()))
    ST.pop(uid, None)
    await cb.message.edit_text(text, reply_markup=result_kb(source, lang))
    if source == "atlas" and subject == "self":
        await cb.message.answer(T(lang)["atlas_note"])
    with db() as c:
        u = c.execute("SELECT invited_by FROM users WHERE tg_id=?", (uid,)).fetchone()
    is_top = bool(u["invited_by"]) if u else False
    if u and u["invited_by"] and subject == "self" and u["invited_by"] != uid:
        try:
            olang = get_lang(u["invited_by"])
            await bot.send_message(u["invited_by"],
                T(olang)["owner_notif"].format(name=escape(cb.from_user.full_name), prof=prof))
        except Exception:
            pass
    # прошел день -> в группу лидов (только холодные self, топов прячем; proxy не шлем)
    if LEADS_TARGET and subject == "self" and not is_top:
        try:
            uname = cb.from_user.username
            handle = f"@{uname}" if uname else "без username"
            names = " + ".join(T(lang)["names"][c] for c in dom)
            await bot.send_message(LEADS_TARGET,
                f"🟢 Прошел «Код руководителя»\n"
                f"Кто: {escape(cb.from_user.full_name)} ({handle})\n"
                f"Код: {prof} - {names}\n"
                f"Источник: {source} · язык: {lang}\n"
                f"Разбор:\n<pre>{bars(s, lang)}</pre>")
        except Exception as e:
            log.error("lead completion to group: %s", e)

# ---------- Хендлеры ----------
def lang_kb():
    return kb([[InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")]])

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
    # счетчик заходов + признак приглашенного топа
    with db() as c:
        c.execute("UPDATE users SET starts = COALESCE(starts,0)+1 WHERE tg_id=?", (m.from_user.id,))
        urow = c.execute("SELECT starts, invited_by FROM users WHERE tg_id=?", (m.from_user.id,)).fetchone()
    n_starts = urow["starts"] if urow else 1
    is_top = bool(urow["invited_by"]) if urow else False
    # заход -> в группу лидов (топов по inv_ не шлем: это чей-то член команды)
    if LEADS_TARGET and not is_top:
        try:
            uname = m.from_user.username
            handle = f"@{uname}" if uname else "без username"
            gsrc = payload if payload in ("atlas", "site", "ads") else "direct"
            head = "Новый лид зашел" if n_starts == 1 else "Повторный заход"
            await bot.send_message(LEADS_TARGET,
                f"🟡 {head} в «Код руководителя»\n"
                f"Кто: {escape(m.from_user.full_name)} ({handle})\n"
                f"Источник: {gsrc} · заход №{n_starts}")
        except Exception as e:
            log.error("lead entry to group: %s", e)
    if m.from_user.id in ST and ST[m.from_user.id].get("mode") == "quiz":
        st = ST[m.from_user.id]
        lang = st["lang"]
        await m.answer(T(lang)["resume_prompt"].format(n=st["scene"] + 1),
                       reply_markup=kb([[InlineKeyboardButton(text=T(lang)["resume_continue"], callback_data="resume"),
                                         InlineKeyboardButton(text=T(lang)["resume_startover"], callback_data="go")]]))
        return
    # запоминаем контекст до выбора языка
    ST[m.from_user.id] = {"mode": "prelang", "invited_by": invited_by, "inviter_name": inviter_name}
    await m.answer("Выберите язык / Choose your language", reply_markup=lang_kb())

@dp.callback_query(F.data.startswith("lang:"))
async def choose_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[1]
    if lang not in LANGS:
        lang = DEFAULT_LANG
    uid = cb.from_user.id
    set_lang(uid, lang)
    ctx = ST.get(uid, {})
    invited_by = ctx.get("invited_by")
    inviter_name = ctx.get("inviter_name", "")
    ST[uid] = {"mode": "intro", "lang": lang, "invited_by": invited_by}
    t = T(lang)
    if invited_by:
        text = t["intro_inv"].format(name=escape(inviter_name))
    else:
        text = t["intro_own"] + f"\n\n<i>{t['sign']}</i>"
    await cb.message.edit_text(text,
        reply_markup=kb([[InlineKeyboardButton(text=t["start_btn"], callback_data="go")]]))
    await cb.answer()

@dp.callback_query(F.data == "go")
async def go(cb: CallbackQuery):
    uid = cb.from_user.id
    lang = get_lang(uid)
    ST[uid] = new_state(lang)
    await show_scene(cb, ST[uid])
    await cb.answer()

@dp.callback_query(F.data == "resume")
async def resume(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st or st.get("mode") != "quiz":
        await cb.answer("Сессия истекла, начните заново: /start", show_alert=True)
        return
    await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data.startswith("ans:"))
async def answer(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st or st.get("mode") != "quiz":
        await cb.answer("Начните заново: /start", show_alert=True)
        return
    code = cb.data.split(":")[1]
    i = st["scene"]
    a = st["ans"][i]
    dbl = T(st["lang"])["scenes"][i][3]
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
    if st["scene"] >= len(SCENES_RU):
        await finish(cb, st)
    else:
        await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data == "back")
async def back(cb: CallbackQuery):
    st = ST.get(cb.from_user.id)
    if not st or st.get("mode") != "quiz":
        await cb.answer()
        return
    if st["stage"] == "least":
        st["ans"][st["scene"]]["m"] = None
        st["stage"] = "most"
    elif st["scene"] > 0:
        st["scene"] -= 1
        prev = st["ans"][st["scene"]]
        prev["l"] = None
        if T(st["lang"])["scenes"][st["scene"]][3]:
            st["stage"] = "least"
        else:
            prev["m"] = None
            st["stage"] = "most"
    await show_scene(cb, st)
    await cb.answer()

@dp.callback_query(F.data == "invite")
async def invite(cb: CallbackQuery):
    uid = cb.from_user.id
    lang = get_lang(uid)
    t = T(lang)
    link = f"https://t.me/{BOT_USERNAME}?start=inv_{uid}"
    with db() as c:
        c.execute("INSERT OR REPLACE INTO invites(owner_id, issued_at, reminded) VALUES(?,?,0)",
                  (uid, datetime.now(timezone.utc).isoformat()))
    await cb.message.answer(t["invite_wrapper"])
    await cb.message.answer(t["invite_fwd"].format(link=link))
    await cb.message.answer(t["invite_after"])
    await cb.answer()

@dp.callback_query(F.data == "proxy")
async def proxy_start(cb: CallbackQuery):
    lang = get_lang(cb.from_user.id)
    ST[cb.from_user.id] = {"mode": "ask_name", "lang": lang}
    await cb.message.answer(T(lang)["proxy_ask"])
    await cb.answer()

@dp.message(F.text, ~F.text.startswith("/"))
async def text_input(m: Message):
    st = ST.get(m.from_user.id)
    if st and st.get("mode") == "ask_name":
        lang = st.get("lang", get_lang(m.from_user.id))
        name = m.text.strip()[:60]
        ST[m.from_user.id] = new_state(lang, subject="proxy", subject_name=name, owner=m.from_user.id)
        await m.answer(T(lang)["proxy_confirm"].format(name=escape(name)),
                       reply_markup=kb([[InlineKeyboardButton(text=T(lang)["start_btn"], callback_data="resume")]]))
        return
    await m.answer(T(get_lang(m.from_user.id))["fallback"])

@dp.callback_query(F.data == "review")
async def review(cb: CallbackQuery):
    uid = cb.from_user.id
    lang = get_lang(uid)
    with db() as c:
        r = c.execute("SELECT profile FROM results WHERE user_id=? AND subject='self' "
                      "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    prof = r["profile"] if r else "-"
    src = user_source(uid)
    uname = cb.from_user.username
    handle = f"@{uname}" if uname else "без username"
    lead = (f"🔴 Запросил разбор карты команды\n"
            f"Кто: {escape(cb.from_user.full_name)} ({handle})\n"
            f"Код: {prof}\n"
            f"Источник: {src} · язык: {lang}\n"
            f"Написать: <a href=\"tg://user?id={uid}\">открыть чат</a>")
    sent = False
    if LEADS_TARGET:
        try:
            await bot.send_message(LEADS_TARGET, lead)
            sent = True
        except Exception as e:
            log.error("review lead to group: %s", e)
    if not sent and ADMIN:
        try:
            await bot.send_message(ADMIN, "[лид, группа недоступна]\n" + lead)
        except Exception:
            pass
    await cb.message.answer(T(lang)["review_sent"])
    await cb.answer()

@dp.callback_query(F.data == "team")
async def team_cb(cb: CallbackQuery):
    await send_team(cb.from_user.id, cb.message)
    await cb.answer()

@dp.message(Command("team"))
async def team_cmd(m: Message):
    await send_team(m.from_user.id, m)

async def send_team(uid, msg):
    lang = get_lang(uid)
    t = T(lang)
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
        await msg.answer(t["team_empty"])
        return
    lines = []
    dom_by_tops = set()
    if own:
        lines.append(t["team_you"].format(prof=own["profile"]))
    for r in tops:
        nm = escape(r["uname"] or "")
        login = f" (@{r['ulogin']})" if r["ulogin"] else ""
        lines.append(f"{nm}{login}: <b>{r['profile']}</b>")
        d, _, _ = notation({"P": r["p"], "A": r["a"], "E": r["e"], "I": r["i"]})
        dom_by_tops.update(d)
    for r in proxies:
        lines.append(f"{escape(r['subject_name'])}: <b>{r['profile']}</b> <i>{t['team_side']}</i>")
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
    parts = [f"<b>{t['team_title']}</b>", "\n".join(lines),
             t["team_closed"].format(codes=closed)]
    if only_owner:
        parts.append(t["team_only_you"].format(codes=" · ".join(only_owner)))
    if fully_open:
        parts.append(t["team_open"].format(codes=" · ".join(fully_open)))
    parts.append(f"<i>{t['team_note']}</i>")
    parts.append(f"<i>{t['team_multi']}</i>")
    src = user_source(uid)
    if src == "atlas":
        parts.append(t["team_atlas"])
    parts.append(f"<i>{t['disc_full']}</i>")
    kb_rows = [[InlineKeyboardButton(text=t["team_invite_more"], callback_data="invite")]]
    if src in ("site", "ads"):
        kb_rows.append([InlineKeyboardButton(text=t["btn_review"], callback_data="review")])
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
                            await bot.send_message(r["owner_id"], T(get_lang(r["owner_id"]))["reminder"])
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
