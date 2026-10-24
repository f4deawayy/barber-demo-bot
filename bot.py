"""
Демо Telegram-бот для записи клиентов в салон / барбершоп.
Умеет: выбор услуги -> дата -> свободное время -> имя и телефон -> подтверждение.
Клиент видит свои записи и может их отменить.
Владелец получает уведомления о новых записях и отменах, а командой /admin видит все записи.
Записи хранятся в bookings.json.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

# ---------- Настройки ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # твой Telegram ID, чтобы получать уведомления (необязательно)

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
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

DATA_FILE = Path(__file__).with_name("bookings.json")


# ---------- Хранилище записей ----------
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


def save_bookings(bookings: list[dict]) -> None:
    DATA_FILE.write_text(json.dumps(bookings, ensure_ascii=False, indent=2), encoding="utf-8")


def taken_times(day: str) -> set[str]:
    return {b["time"] for b in load_bookings() if b["date"] == day}


def is_upcoming(b: dict) -> bool:
    return datetime.fromisoformat(f"{b['date']}T{b['time']}") >= datetime.now()


def sort_key(b: dict) -> str:
    return f"{b['date']} {b['time']}"


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and str(user_id) == str(ADMIN_ID).strip()


# ---------- Состояния диалога ----------
class Booking(StatesGroup):
    service = State()
    day = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


# ---------- Клавиатуры ----------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Записаться")],
            [KeyboardButton(text="🗂 Мои записи"), KeyboardButton(text="💈 Цены и адрес")],
        ],
        resize_keyboard=True,
    )


def services_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{name} — {price}€", callback_data=f"srv:{key}")]
        for key, (name, price, _) in SERVICES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def days_kb() -> InlineKeyboardMarkup:
    rows = []
    today = date.today()
    for i in range(1, DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        if d.weekday() == 6:  # воскресенье — выходной
            continue
        label = f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"day:{d.isoformat()}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:service")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def times_kb(day: str) -> InlineKeyboardMarkup | None:
    busy = taken_times(day)
    free = [t for t in WORK_HOURS if t not in busy]
    if not free:
        return None
    buttons = [InlineKeyboardButton(text=t, callback_data=f"time:{t}") for t in free]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text="⬅️ Другой день", callback_data="back:day")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm:yes"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:no"),
            ]
        ]
    )


def pretty_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m.%Y')}"


# ---------- Обработчики ----------
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        f"Это бот записи в <b>{SALON_NAME}</b>.\n\n"
        "Выбери, что хочешь сделать:",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "💈 Цены и адрес")
async def prices(message: Message):
    lines = [f"• {name} — {price}€ ({mins} мин)" for name, price, mins in SERVICES.values()]
    await message.answer(
        f"<b>{SALON_NAME}</b>\n📍 {SALON_ADDRESS}\n🕐 Пн–Сб, 10:00–19:00\n\n" + "\n".join(lines)
    )


@dp.message(F.text == "🗂 Мои записи")
async def my_bookings(message: Message):
    mine = sorted(
        (b for b in load_bookings() if b["user_id"] == message.from_user.id and is_upcoming(b)),
        key=sort_key,
    )
    if not mine:
        await message.answer("У тебя пока нет предстоящих записей. Жми «📅 Записаться» 🙂")
        return
    text = "\n".join(f"• {pretty_date(b['date'])} в {b['time']} — {b['service']}" for b in mine)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить {date.fromisoformat(b['date']).strftime('%d.%m')} {b['time']}",
                    callback_data=f"cancel:{b['id']}",
                )
            ]
            for b in mine
        ]
    )
    await message.answer(f"Твои записи:\n{text}", reply_markup=kb)


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_booking(call: CallbackQuery, bot: Bot):
    booking_id = call.data.split(":", 1)[1]
    bookings = load_bookings()
    target = next(
        (b for b in bookings if b["id"] == booking_id and b["user_id"] == call.from_user.id), None
    )
    if target is None:
        await call.answer("Эта запись уже отменена.", show_alert=True)
        return
    bookings.remove(target)
    save_bookings(bookings)
    await call.message.edit_text(
        f"Запись на {pretty_date(target['date'])} в {target['time']} отменена ❌\n"
        "Если что — записывайся снова 🙂"
    )
    await call.answer()
    if ADMIN_ID:
        try:
            await bot.send_message(
                int(ADMIN_ID),
                "🚫 <b>Отмена записи</b>\n"
                f"{target['service']}\n"
                f"{pretty_date(target['date'])} в {target['time']}\n"
                f"{target['name']}, {target['phone']}",
            )
        except Exception as e:  # noqa: BLE001
            logging.warning("Не удалось уведомить админа: %s", e)


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда только для владельца 🙂")
        return
    upcoming = sorted((b for b in load_bookings() if is_upcoming(b)), key=sort_key)
    if not upcoming:
        await message.answer("Предстоящих записей нет.")
        return
    lines, current_day = [], None
    for b in upcoming:
        if b["date"] != current_day:
            current_day = b["date"]
            lines.append(f"\n<b>{pretty_date(current_day)}</b>")
        lines.append(f"{b['time']} — {b['service']} ({b['price']}€) — {b['name']}, {b['phone']}")
    total = sum(b["price"] for b in upcoming)
    await message.answer(
        f"📋 <b>Все записи</b> ({len(upcoming)} шт., на {total}€):" + "\n".join(lines)
    )


@dp.message(F.text == "📅 Записаться")
@dp.message(Command("book"))
async def start_booking(message: Message, state: FSMContext):
    await state.set_state(Booking.service)
    await message.answer("Выбери услугу:", reply_markup=services_kb())


@dp.callback_query(F.data.startswith("srv:"))
async def choose_service(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":", 1)[1]
    await state.update_data(service=key)
    await state.set_state(Booking.day)
    await call.message.edit_text("Выбери день:", reply_markup=days_kb())
    await call.answer()


@dp.callback_query(F.data.startswith("day:"))
async def choose_day(call: CallbackQuery, state: FSMContext):
    day = call.data.split(":", 1)[1]
    kb = times_kb(day)
    if kb is None:
        await call.answer("На этот день всё занято 😔 Выбери другой.", show_alert=True)
        return
    await state.update_data(day=day)
    await state.set_state(Booking.time)
    await call.message.edit_text(f"{pretty_date(day)} — выбери время:", reply_markup=kb)
    await call.answer()


@dp.callback_query(F.data.startswith("time:"))
async def choose_time(call: CallbackQuery, state: FSMContext):
    time_ = call.data.split(":", 1)[1]
    await state.update_data(time=time_)
    await state.set_state(Booking.name)
    await call.message.edit_text(f"Время {time_} ✅")
    await call.message.answer("Как тебя зовут?")
    await call.answer()


@dp.callback_query(F.data.startswith("back:"))
async def go_back(call: CallbackQuery, state: FSMContext):
    target = call.data.split(":", 1)[1]
    if target == "service":
        await state.set_state(Booking.service)
        await call.message.edit_text("Выбери услугу:", reply_markup=services_kb())
    else:
        await state.set_state(Booking.day)
        await call.message.edit_text("Выбери день:", reply_markup=days_kb())
    await call.answer()


@dp.message(Booking.name, F.text)
async def get_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:50])
    await state.set_state(Booking.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Отправь номер телефона кнопкой ниже или напиши его:", reply_markup=kb)


@dp.message(Booking.phone)
async def get_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else (message.text or "").strip()
    if len(phone) < 6:
        await message.answer("Похоже, это не номер. Попробуй ещё раз 🙂")
        return
    await state.update_data(phone=phone)
    data = await state.get_data()
    name, price, _ = SERVICES[data["service"]]
    await state.set_state(Booking.confirm)
    await message.answer("Почти готово!", reply_markup=ReplyKeyboardRemove())
    await message.answer(
        "<b>Проверь запись:</b>\n"
        f"💈 {name} — {price}€\n"
        f"📅 {pretty_date(data['day'])} в {data['time']}\n"
        f"👤 {data['name']}\n"
        f"📱 {phone}",
        reply_markup=confirm_kb(),
    )


@dp.callback_query(Booking.confirm, F.data.startswith("confirm:"))
async def confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    if call.data == "confirm:no":
        await call.message.edit_text("Запись отменена.")
        await call.message.answer("Чем ещё помочь?", reply_markup=main_menu())
        await call.answer()
        return

    if data["time"] in taken_times(data["day"]):
        await call.message.edit_text("Ой, это время только что заняли 😔 Попробуй выбрать другое.")
        await call.message.answer("Меню:", reply_markup=main_menu())
        await call.answer()
        return

    service_name, price, _ = SERVICES[data["service"]]
    booking = {
        "id": uuid.uuid4().hex[:8],
        "user_id": call.from_user.id,
        "username": call.from_user.username,
        "name": data["name"],
        "phone": data["phone"],
        "service": service_name,
        "price": price,
        "date": data["day"],
        "time": data["time"],
    }
    bookings = load_bookings()
    bookings.append(booking)
    save_bookings(bookings)

    await call.message.edit_text(
        f"✅ Ты записан на {pretty_date(data['day'])} в {data['time']}!\n"
        f"📍 {SALON_ADDRESS}\nЖдём тебя 👋"
    )
    await call.message.answer("Меню:", reply_markup=main_menu())
    await call.answer()

    if ADMIN_ID:
        try:
            await bot.send_message(
                int(ADMIN_ID),
                "🔔 <b>Новая запись</b>\n"
                f"{service_name} — {price}€\n"
                f"{pretty_date(data['day'])} в {data['time']}\n"
                f"{data['name']}, {data['phone']}",
            )
        except Exception as e:  # noqa: BLE001
            logging.warning("Не удалось уведомить админа: %s", e)


@dp.message(Command("myid"))
async def my_id(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


# ---------- Запуск ----------
async def main():
    if not BOT_TOKEN:
        raise SystemExit("Нет BOT_TOKEN. Создай файл .env и впиши туда токен (см. .env.example).")
    from aiogram.client.default import DefaultBotProperties

    from aiogram.types import BotCommand

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    logging.basicConfig(level=logging.INFO)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="book", description="Записаться"),
        ]
    )
    print("Бот запущен! Открой его в Telegram и нажми /start")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
