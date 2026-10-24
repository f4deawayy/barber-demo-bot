"""
Демо Telegram-бот для записи клиентов в салон / барбершоп.
Лёгкая версия: работает БЕЗ внешних библиотек, только на встроенном Python,
поэтому занимает мало памяти и подходит для самых дешёвых и бесплатных серверов.

Умеет: выбор услуги -> дата -> свободное время -> имя и телефон -> подтверждение.
Клиент видит свои записи и может их отменить.
Владелец получает уведомления о новых записях и отменах, а командой /admin видит все записи.
Записи хранятся в bookings.json.
"""

import html
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


# ---------- Загрузка .env (без библиотеки python-dotenv) ----------
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env(BASE_DIR / ".env")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()  # твой Telegram ID для уведомлений (необязательно)

# ---------- Настройки салона ----------
SALON_NAME = "Barber Demo"
SALON_ADDRESS = "Calle Ejemplo 12, Valencia"

# Услуги: ключ -> (название, цена в евро, длительность в минутах)
SERVICES = {
    "haircut": ("Мужская стрижка", 15, 45),
    "beard": ("Оформление бороды", 10, 30),
    "combo": ("Стрижка + борода", 22, 60),
}

WORK_HOURS = ["10:00", "11:00", "12:00", "13:00", "15:00", "16:00", "17:00", "18:00"]
DAYS_AHEAD = 7
DAYS_OFF = {6}  # 6 = воскресенье
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

DATA_FILE = BASE_DIR / "bookings.json"

BTN_BOOK = "📅 Записаться"
BTN_MY = "🗂 Мои записи"
BTN_INFO = "💈 Цены и адрес"

log = logging.getLogger("bot")


# ---------- Telegram API ----------
class TelegramError(Exception):
    pass


def api(method: str, timeout: int = 30, **params):
    """Вызов метода Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps({k: v for k, v in params.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8") or "{}")
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'unknown error')}")
    return data["result"]


def send(chat_id: int, text: str, markup: dict | None = None):
    return api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup)


def edit(chat_id: int, message_id: int, text: str, markup: dict | None = None):
    try:
        api(
            "editMessageText",
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except TelegramError as e:
        if "message is not modified" not in str(e):
            raise


def answer_callback(callback_id: str, text: str | None = None, alert: bool = False):
    api("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert)


def notify_admin(text: str):
    if not ADMIN_ID:
        return
    try:
        send(int(ADMIN_ID), text)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось уведомить владельца: %s", e)


# ---------- Хранилище записей ----------
def save_bookings(bookings: list[dict]) -> None:
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(bookings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def load_bookings() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    bookings = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    changed = False
    for b in bookings:  # старым записям без id выдаём id
        if "id" not in b:
            b["id"] = uuid.uuid4().hex[:8]
            changed = True
    if changed:
        save_bookings(bookings)
    return bookings


def taken_times(day: str) -> set[str]:
    return {b["time"] for b in load_bookings() if b["date"] == day}


def is_upcoming(b: dict) -> bool:
    return datetime.fromisoformat(f"{b['date']}T{b['time']}") >= datetime.now()


def sort_key(b: dict) -> str:
    return f"{b['date']} {b['time']}"


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and str(user_id) == ADMIN_ID


def pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m.%Y')}"


def esc(text) -> str:
    return html.escape(str(text))


# ---------- Клавиатуры ----------
def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def main_menu() -> dict:
    return {
        "keyboard": [[{"text": BTN_BOOK}], [{"text": BTN_MY}, {"text": BTN_INFO}]],
        "resize_keyboard": True,
    }


def services_kb() -> dict:
    rows = [[btn(f"{name} — {price}€", f"srv:{key}")] for key, (name, price, _) in SERVICES.items()]
    return {"inline_keyboard": rows}


def days_kb() -> dict:
    rows = []
    today = date.today()
    for i in range(1, DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        if d.weekday() in DAYS_OFF:
            continue
        rows.append([btn(f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}", f"day:{d.isoformat()}")])
    rows.append([btn("⬅️ Назад", "back:service")])
    return {"inline_keyboard": rows}


def times_kb(day: str) -> dict | None:
    busy = taken_times(day)
    free = [t for t in WORK_HOURS if t not in busy]
    if not free:
        return None
    buttons = [btn(t, f"time:{t}") for t in free]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([btn("⬅️ Другой день", "back:day")])
    return {"inline_keyboard": rows}


def confirm_kb() -> dict:
    return {"inline_keyboard": [[btn("✅ Подтвердить", "confirm:yes"), btn("❌ Отмена", "confirm:no")]]}


def phone_kb() -> dict:
    return {
        "keyboard": [[{"text": "📱 Отправить номер", "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


# ---------- Состояние диалога (в памяти) ----------
# user_id -> {"step": "...", "service": ..., "day": ..., "time": ..., "name": ..., "phone": ...}
sessions: dict[int, dict] = {}


# ---------- Обработка сообщений ----------
def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user = msg.get("from", {})
    user_id = user.get("id")
    text = (msg.get("text") or "").strip()
    session = sessions.get(user_id, {})
    step = session.get("step")

    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    if command == "/start":
        sessions.pop(user_id, None)
        first_name = esc(user.get("first_name") or "друг")
        send(
            chat_id,
            f"Привет, {first_name}! 👋\nЭто бот записи в <b>{esc(SALON_NAME)}</b>.\n\n"
            "Выбери, что хочешь сделать:",
            main_menu(),
        )
    elif command == "/myid":
        send(chat_id, f"Твой Telegram ID: <code>{user_id}</code>")
    elif command == "/admin":
        admin_panel(chat_id, user_id)
    elif command == "/book" or text == BTN_BOOK:
        sessions[user_id] = {"step": "service"}
        send(chat_id, "Выбери услугу:", services_kb())
    elif text == BTN_INFO:
        lines = [f"• {esc(n)} — {p}€ ({m} мин)" for n, p, m in SERVICES.values()]
        send(
            chat_id,
            f"<b>{esc(SALON_NAME)}</b>\n📍 {esc(SALON_ADDRESS)}\n🕐 Пн–Сб, 10:00–19:00\n\n"
            + "\n".join(lines),
        )
    elif text == BTN_MY:
        my_bookings(chat_id, user_id)
    elif step == "name" and text:
        session["name"] = text[:50]
        session["step"] = "phone"
        send(chat_id, "Отправь номер телефона кнопкой ниже или напиши его:", phone_kb())
    elif step == "phone":
        contact = msg.get("contact")
        phone = contact["phone_number"] if contact else text
        if len(phone) < 6:
            send(chat_id, "Похоже, это не номер. Попробуй ещё раз 🙂")
            return
        session["phone"] = phone[:30]
        session["step"] = "confirm"
        name, price, _ = SERVICES[session["service"]]
        send(chat_id, "Почти готово!", {"remove_keyboard": True})
        send(
            chat_id,
            "<b>Проверь запись:</b>\n"
            f"💈 {esc(name)} — {price}€\n"
            f"📅 {pretty_date(session['day'])} в {session['time']}\n"
            f"👤 {esc(session['name'])}\n"
            f"📱 {esc(session['phone'])}",
            confirm_kb(),
        )
    else:
        send(chat_id, "Не понял 🙂 Выбери действие в меню:", main_menu())


def my_bookings(chat_id: int, user_id: int) -> None:
    mine = sorted(
        (b for b in load_bookings() if b["user_id"] == user_id and is_upcoming(b)), key=sort_key
    )
    if not mine:
        send(chat_id, "У тебя пока нет предстоящих записей. Жми «📅 Записаться» 🙂")
        return
    text = "\n".join(f"• {pretty_date(b['date'])} в {b['time']} — {esc(b['service'])}" for b in mine)
    rows = [
        [btn(f"❌ Отменить {date.fromisoformat(b['date']).strftime('%d.%m')} {b['time']}", f"cancel:{b['id']}")]
        for b in mine
    ]
    send(chat_id, f"Твои записи:\n{text}", {"inline_keyboard": rows})


def admin_panel(chat_id: int, user_id: int) -> None:
    if not is_admin(user_id):
        send(chat_id, "Эта команда только для владельца 🙂")
        return
    upcoming = sorted((b for b in load_bookings() if is_upcoming(b)), key=sort_key)
    if not upcoming:
        send(chat_id, "Предстоящих записей нет.")
        return
    lines, current_day = [], None
    for b in upcoming:
        if b["date"] != current_day:
            current_day = b["date"]
            lines.append(f"\n<b>{pretty_date(current_day)}</b>")
        lines.append(
            f"{b['time']} — {esc(b['service'])} ({b['price']}€) — {esc(b['name'])}, {esc(b['phone'])}"
        )
    total = sum(b["price"] for b in upcoming)
    send(chat_id, f"📋 <b>Все записи</b> ({len(upcoming)} шт., на {total}€):" + "\n".join(lines))


# ---------- Обработка нажатий на кнопки ----------
def handle_callback(cb: dict) -> None:
    data = cb.get("data", "")
    user_id = cb["from"]["id"]
    message = cb.get("message") or {}
    chat_id = message.get("chat", {}).get("id", user_id)
    message_id = message.get("message_id")
    session = sessions.setdefault(user_id, {})
    action, _, value = data.partition(":")

    if action == "srv" and value in SERVICES:
        session.clear()
        session.update(step="day", service=value)
        edit(chat_id, message_id, "Выбери день:", days_kb())
        answer_callback(cb["id"])

    elif action == "day":
        if "service" not in session:
            answer_callback(cb["id"], "Начни запись заново: «📅 Записаться»", alert=True)
            return
        kb = times_kb(value)
        if kb is None:
            answer_callback(cb["id"], "На этот день всё занято 😔 Выбери другой.", alert=True)
            return
        session.update(step="time", day=value)
        edit(chat_id, message_id, f"{pretty_date(value)} — выбери время:", kb)
        answer_callback(cb["id"])

    elif action == "time":
        if "day" not in session:
            answer_callback(cb["id"], "Начни запись заново: «📅 Записаться»", alert=True)
            return
        session.update(step="name", time=value)
        edit(chat_id, message_id, f"Время {value} ✅")
        send(chat_id, "Как тебя зовут?")
        answer_callback(cb["id"])

    elif action == "back":
        if value == "service":
            session.clear()
            session["step"] = "service"
            edit(chat_id, message_id, "Выбери услугу:", services_kb())
        else:
            session["step"] = "day"
            edit(chat_id, message_id, "Выбери день:", days_kb())
        answer_callback(cb["id"])

    elif action == "confirm":
        finish_booking(cb, session, chat_id, message_id, value == "yes")

    elif action == "cancel":
        cancel_booking(cb, chat_id, message_id, value)

    else:
        answer_callback(cb["id"])


def finish_booking(cb: dict, session: dict, chat_id: int, message_id: int, confirmed: bool) -> None:
    user_id = cb["from"]["id"]
    if session.get("step") != "confirm":
        answer_callback(cb["id"], "Эта запись уже обработана.")
        return
    sessions.pop(user_id, None)

    if not confirmed:
        edit(chat_id, message_id, "Запись отменена.")
        send(chat_id, "Чем ещё помочь?", main_menu())
        answer_callback(cb["id"])
        return

    if session["time"] in taken_times(session["day"]):
        edit(chat_id, message_id, "Ой, это время только что заняли 😔 Попробуй выбрать другое.")
        send(chat_id, "Меню:", main_menu())
        answer_callback(cb["id"])
        return

    service_name, price, _ = SERVICES[session["service"]]
    booking = {
        "id": uuid.uuid4().hex[:8],
        "user_id": user_id,
        "username": cb["from"].get("username"),
        "name": session["name"],
        "phone": session["phone"],
        "service": service_name,
        "price": price,
        "date": session["day"],
        "time": session["time"],
    }
    bookings = load_bookings()
    bookings.append(booking)
    save_bookings(bookings)

    edit(
        chat_id,
        message_id,
        f"✅ Ты записан на {pretty_date(booking['date'])} в {booking['time']}!\n"
        f"📍 {esc(SALON_ADDRESS)}\nЖдём тебя 👋",
    )
    send(chat_id, "Меню:", main_menu())
    answer_callback(cb["id"])
    notify_admin(
        "🔔 <b>Новая запись</b>\n"
        f"{esc(service_name)} — {price}€\n"
        f"{pretty_date(booking['date'])} в {booking['time']}\n"
        f"{esc(booking['name'])}, {esc(booking['phone'])}"
    )


def cancel_booking(cb: dict, chat_id: int, message_id: int, booking_id: str) -> None:
    user_id = cb["from"]["id"]
    bookings = load_bookings()
    target = next((b for b in bookings if b["id"] == booking_id and b["user_id"] == user_id), None)
    if target is None:
        answer_callback(cb["id"], "Эта запись уже отменена.", alert=True)
        return
    bookings.remove(target)
    save_bookings(bookings)
    edit(
        chat_id,
        message_id,
        f"Запись на {pretty_date(target['date'])} в {target['time']} отменена ❌\n"
        "Если что — записывайся снова 🙂",
    )
    answer_callback(cb["id"])
    notify_admin(
        "🚫 <b>Отмена записи</b>\n"
        f"{esc(target['service'])}\n"
        f"{pretty_date(target['date'])} в {target['time']}\n"
        f"{esc(target['name'])}, {esc(target['phone'])}"
    )


# ---------- Запуск ----------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("Нет BOT_TOKEN. Создай файл .env и впиши туда токен (см. .env.example).")

    me = api("getMe")
    api(
        "setMyCommands",
        commands=[
            {"command": "start", "description": "Главное меню"},
            {"command": "book", "description": "Записаться"},
        ],
    )
    print(f"Бот запущен! @{me['username']} — открой его в Telegram и нажми /start", flush=True)

    offset = None
    while True:
        try:
            updates = api_get_updates(offset)
        except TelegramError as e:
            if "Conflict" in str(e):
                log.error("Этот бот уже запущен где-то ещё (на компе или другом сервере). Останови второй.")
            else:
                log.error("Ошибка Telegram: %s", e)
            time.sleep(5)
            continue
        except Exception as e:  # noqa: BLE001  (сеть пропала и т.п.)
            log.warning("Нет связи с Telegram: %s. Повтор через 5 сек.", e)
            time.sleep(5)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    handle_message(upd["message"])
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"])
            except Exception:  # noqa: BLE001
                log.exception("Ошибка при обработке сообщения")


def api_get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    body = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8") or "{}")
    if not data.get("ok"):
        raise TelegramError(f"getUpdates: {data.get('description', 'unknown error')}")
    return data["result"]


if __name__ == "__main__":
    main()
