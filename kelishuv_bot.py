"""
KELISHUV BOTI — Haqdorlik / Qarzdorlik, Kassa va Xarajatlar (UMUMIY OILA HISOBI)

O'rnatish:
    pip install aiogram

Ishga tushirish:
    python kelishuv_bot.py

MUHIM: Hisob UMUMIY — siz va dadangiz (yoki botga /start bosgan har qanday
oila a'zosi) bitta hisobni ko'radi. Har qanday o'zgarish BARCHA boshqa
a'zolarga avtomatik xabar sifatida yuboriladi.

Shart: har bir a'zo botga kamida BIR MARTA /start bosishi kerak.

MANTIQ:
    + (musbat)  -> Dadangiz sizdan shuncha QARZDOR (masalan: vazifa, dars, dastur)
    - (manfiy)  -> Siz dadangizdan HAQDORSIZ

    Dadangiz "To'lab qo'ydim" tugmasi orqali pul kiritsa:
        -> hisob (qarz) kamayadi
        -> Kassadagi mavjud pul ko'payadi

    Siz kassadagi puldan biror narsaga sarflasangiz ("💸 Xarajat qilish"):
        -> Kassadagi mavjud pul kamayadi
        -> Dadangizga "nimaga sarflaganingiz" ko'rinadi

    Har kuni belgilangan vaqtda (soat 21:00, kompyuter vaqti bo'yicha)
    BARCHA a'zolarga o'sha kunning yakuniy CHEKI avtomatik yuboriladi.

/balance -> joriy umumiy hisob
/kassa   -> kassa ko'rinishi
/tarix   -> so'nggi 10 ta xom yozuv
"""

import asyncio
import re
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

BOT_TOKEN = "8709820987:AAG3eY4_sBEgtUDvlDyaJpZ4h65fKNtwPz4"
DB_PATH = "kelishuv.db"

REWARD_TASK_DONE = 5000
DEBT_TASK_NOT_DONE = 10000
REWARD_SUBJECT = 100000
BOOK_EXTRA = 5000

SHARED_ID = 0  # barcha oila a'zolari shu BITTA umumiy hisobni ishlatadi
DAILY_REPORT_HOUR = 21  # kunlik chek shu soatda (kompyuter vaqti) yuboriladi


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------- ma'lumotlar bazasi ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS balances (
        chat_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL DEFAULT 0,
        kassa_cash INTEGER NOT NULL DEFAULT 0)""")
    try:
        cur.execute("ALTER TABLE balances ADD COLUMN kassa_cash INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
        change INTEGER, reason TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS kassa (
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
        change INTEGER, reason TEXT, message TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount INTEGER, reason TEXT, created_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY, added_at TEXT)""")
    conn.commit()
    conn.close()


def register_user(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (chat_id, added_at) VALUES (?, ?)", (chat_id, now_str()))
    conn.commit()
    conn.close()


def get_all_chat_ids():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT chat_id FROM users")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_other_chat_ids(exclude_chat_id: int):
    return [c for c in get_all_chat_ids() if c != exclude_chat_id]


def _ensure_row(cur):
    cur.execute("SELECT amount FROM balances WHERE chat_id=?", (SHARED_ID,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO balances (chat_id, amount, kassa_cash) VALUES (?, 0, 0)", (SHARED_ID,))


def get_balance() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    _ensure_row(cur)
    conn.commit()
    cur.execute("SELECT amount FROM balances WHERE chat_id=?", (SHARED_ID,))
    amount = cur.fetchone()[0]
    conn.close()
    return amount


def get_kassa_cash() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    _ensure_row(cur)
    conn.commit()
    cur.execute("SELECT kassa_cash FROM balances WHERE chat_id=?", (SHARED_ID,))
    cash = cur.fetchone()[0]
    conn.close()
    return cash


def add_kassa_cash(amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    _ensure_row(cur)
    cur.execute("UPDATE balances SET kassa_cash = kassa_cash + ? WHERE chat_id=?", (amount, SHARED_ID))
    conn.commit()
    conn.close()


def balance_status_text() -> str:
    bal = get_balance()
    if bal > 0:
        return f"🟢 Umumiy hisob: dadangiz sizdan {fmt(bal)} so'm QARZDOR."
    elif bal < 0:
        return f"🔴 Umumiy hisob: siz dadangizdan {fmt(abs(bal))} so'm HAQDORSIZ (dadangizga shuncha qarzdorsiz)."
    else:
        return "⚪ Umumiy hisob: 0 so'm — hech kim hech kimdan qarzdor emas."


def change_balance(delta: int, reason: str) -> tuple[int, str]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    _ensure_row(cur)
    cur.execute("UPDATE balances SET amount = amount + ? WHERE chat_id=?", (delta, SHARED_ID))
    cur.execute(
        "INSERT INTO history (chat_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
        (SHARED_ID, delta, reason, now_str()),
    )
    msg = "💳 Pulni kassada to'lang!"
    cur.execute(
        "INSERT INTO kassa (chat_id, change, reason, message, created_at) VALUES (?, ?, ?, ?, ?)",
        (SHARED_ID, delta, reason, msg, now_str()),
    )
    conn.commit()
    cur.execute("SELECT amount FROM balances WHERE chat_id=?", (SHARED_ID,))
    new_amount = cur.fetchone()[0]
    conn.close()
    return new_amount, msg


def get_kassa(limit: int = 15):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT change, reason, created_at FROM kassa WHERE chat_id=? ORDER BY id ASC LIMIT ?",
        (SHARED_ID, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_expenses(limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT amount, reason, created_at FROM expenses ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def record_expense(amount: int, reason: str) -> int:
    """Kassadagi puldan sarflaydi. Yangi (qolgan) kassa pulini qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    _ensure_row(cur)
    cur.execute("UPDATE balances SET kassa_cash = kassa_cash - ? WHERE chat_id=?", (amount, SHARED_ID))
    cur.execute(
        "INSERT INTO expenses (amount, reason, created_at) VALUES (?, ?, ?)",
        (amount, reason, now_str()),
    )
    conn.commit()
    cur.execute("SELECT kassa_cash FROM balances WHERE chat_id=?", (SHARED_ID,))
    remaining = cur.fetchone()[0]
    conn.close()
    return remaining


def apply_payment(amount: int) -> int:
    bal = get_balance()
    if bal > 0:
        delta = -amount
    elif bal < 0:
        delta = amount
    else:
        delta = 0
    new_balance, _ = change_balance(delta, f"To'lov qilindi ({fmt(amount)} so'm)")
    add_kassa_cash(amount)
    return new_balance


def get_today_summary_text() -> str | None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT change, reason, created_at FROM history WHERE chat_id=? AND created_at LIKE ? ORDER BY id ASC",
        (SHARED_ID, today_str() + "%"),
    )
    hist_rows = cur.fetchall()
    cur.execute(
        "SELECT amount, reason, created_at FROM expenses WHERE created_at LIKE ? ORDER BY id ASC",
        (today_str() + "%",),
    )
    exp_rows = cur.fetchall()
    conn.close()

    if not hist_rows and not exp_rows:
        return None

    parts = [f"🧾 KUNLIK CHEK — {today_str()}\n"]
    if hist_rows:
        parts.append("📋 Haqdorlik/Qarzdorlik harakatlari:")
        for ch, reason, ts in hist_rows:
            t = ts.split(" ")[1][:5] if " " in ts else ts
            sign = "+" if ch >= 0 else ""
            parts.append(f"  {t} — {sign}{fmt(ch)} so'm — {reason}")
    if exp_rows:
        parts.append("\n💸 Kassadan sarflangan xarajatlar:")
        for amt, reason, ts in exp_rows:
            t = ts.split(" ")[1][:5] if " " in ts else ts
            parts.append(f"  {t} — -{fmt(amt)} so'm — {reason}")
    parts.append(f"\n💵 Kassadagi mavjud pul: {fmt(get_kassa_cash())} so'm")
    parts.append(balance_status_text())
    return "\n".join(parts)


# ---------- xabar yuborish (barcha a'zolarga) ----------

async def broadcast(bot: Bot, sender_chat_id: int, text: str):
    for chat_id in get_other_chat_ids(sender_chat_id):
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            pass


async def broadcast_all(bot: Bot, text: str):
    for chat_id in get_all_chat_ids():
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            pass


async def record_and_notify(bot: Bot, sender_chat_id: int, delta: int, reason: str) -> tuple[int, str]:
    new_balance, kmsg = change_balance(delta, reason)
    sign = "+" if delta >= 0 else ""
    notify_text = (
        f"🔔 Yangilanish!\n{sign}{fmt(delta)} so'm — {reason}\n\n{balance_status_text()}"
    )
    await broadcast(bot, sender_chat_id, notify_text)
    return new_balance, kmsg


# ---------- kunlik chek (fon vazifasi) ----------

async def daily_digest_loop(bot: Bot):
    while True:
        now = datetime.now()
        next_run = now.replace(hour=DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        summary = get_today_summary_text()
        if summary:
            await broadcast_all(bot, summary)


# ---------- holatlar ----------

class Form(StatesGroup):
    waiting_book_price = State()
    waiting_manual_amount = State()
    waiting_manual_reason = State()
    waiting_payment_amount = State()
    waiting_spend_amount = State()
    waiting_spend_reason = State()


dp = Dispatcher(storage=MemoryStorage())
manual_amount_tmp: dict[int, int] = {}
spend_amount_tmp: dict[int, int] = {}


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Haqdorlik / Qarzdorlik", callback_data="haq_qarz_menu")],
        [InlineKeyboardButton(text="💰 Hisobim", callback_data="balance")],
        [InlineKeyboardButton(text="🏦 Kassa", callback_data="kassa")],
        [InlineKeyboardButton(text="🧾 Tarix", callback_data="history")],
    ])


def haq_qarz_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Vazifa bajardim (+{fmt(REWARD_TASK_DONE)})", callback_data="hw_done")],
        [InlineKeyboardButton(text=f"❌ Vazifa bajarmadim (-{fmt(DEBT_TASK_NOT_DONE)})", callback_data="hw_not_done")],
        [InlineKeyboardButton(text=f"🇬🇧 Ingiliz tili (+{fmt(REWARD_SUBJECT)})", callback_data="english")],
        [InlineKeyboardButton(text=f"➗ Matematika (+{fmt(REWARD_SUBJECT)})", callback_data="math")],
        [InlineKeyboardButton(text="📚 Kitob oldi", callback_data="book")],
        [InlineKeyboardButton(text="➕➖ Pul miqdori kiritish", callback_data="manual")],
        [InlineKeyboardButton(text="💸 Xarajat qilish (kassadan)", callback_data="spend")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_main")],
    ])


def kassa_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 To'lab qo'ydim", callback_data="pay_settle")],
    ])


async def show_result(target: Message, delta: int, reason: str, new_balance: int, kassa_msg: str):
    sign = "+" if delta >= 0 else ""
    await target.answer(
        f"🧾 CHEK\n"
        f"────────────\n"
        f"Nima uchun: {reason}\n"
        f"Summa: {sign}{fmt(delta)} so'm\n"
        f"────────────\n\n"
        f"{kassa_msg}",
        reply_markup=haq_qarz_menu(),
    )


async def send_balance(target: Message):
    await target.answer(balance_status_text(), reply_markup=main_menu())


async def send_history(target: Message):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT change, reason, created_at FROM history WHERE chat_id=? ORDER BY id DESC LIMIT 10",
        (SHARED_ID,),
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await target.answer("Hali yozuvlar yo'q.", reply_markup=main_menu())
        return
    lines = [f"{ts}: {'+' if ch >= 0 else ''}{fmt(ch)} — {reason}" for ch, reason, ts in rows]
    await target.answer("🧾 So'nggi yozuvlar:\n\n" + "\n".join(lines), reply_markup=main_menu())


async def send_kassa(target: Message):
    rows = get_kassa()
    exp_rows = get_expenses()
    cash = get_kassa_cash()
    cash_line = f"💵 Kassadagi mavjud pul: {fmt(cash)} so'm"

    parts = ["🏦 KASSA\n"]
    if rows:
        parts.append("\n".join(f"{ts}: {'+' if ch >= 0 else ''}{fmt(ch)} so'm — {reason}" for ch, reason, ts in rows))
    else:
        parts.append("Hali yozuvlar yo'q.")

    if exp_rows:
        parts.append("\n💸 So'nggi xarajatlar:")
        parts.append("\n".join(f"{ts}: -{fmt(amt)} so'm — {reason}" for amt, reason, ts in exp_rows))

    parts.append("\n💳 To'lov qiling!\n")
    parts.append(cash_line)
    parts.append(balance_status_text())

    await target.answer("\n".join(parts), reply_markup=kassa_keyboard())


# ---------- buyruqlar ----------

@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    register_user(message.chat.id)
    await message.answer(
        "Salom! Kerakli bo'limni tanlang:\n\n"
        "(Bu — oilaviy umumiy hisob. Dadangiz ham shu botga /start bossa, "
        "u ham xuddi shu hisobni ko'radi va har bir yangilanish haqida xabar oladi. "
        "Har kuni soat 21:00 da kunlik chek avtomatik yuboriladi.)",
        reply_markup=main_menu(),
    )


@dp.message(Command("balance"))
async def balance_command(message: Message):
    await send_balance(message)


@dp.message(Command("kassa"))
async def kassa_command(message: Message):
    await send_kassa(message)


@dp.message(Command("tarix"))
async def history_command(message: Message):
    await send_history(message)


# ---------- tugma bosishlar ----------

@dp.callback_query(F.data == "hw_done")
async def cb_hw_done(cq: CallbackQuery):
    new_balance, kmsg = await record_and_notify(cq.message.bot, cq.message.chat.id, REWARD_TASK_DONE, "Uyga vazifa bajarildi")
    await show_result(cq.message, REWARD_TASK_DONE, "Uyga vazifa bajarildi", new_balance, kmsg)
    await cq.answer()


@dp.callback_query(F.data == "hw_not_done")
async def cb_hw_not_done(cq: CallbackQuery):
    new_balance, kmsg = await record_and_notify(cq.message.bot, cq.message.chat.id, -DEBT_TASK_NOT_DONE, "Uyga vazifa bajarilmadi")
    await show_result(cq.message, -DEBT_TASK_NOT_DONE, "Uyga vazifa bajarilmadi", new_balance, kmsg)
    await cq.answer()


@dp.callback_query(F.data == "english")
async def cb_english(cq: CallbackQuery):
    new_balance, kmsg = await record_and_notify(cq.message.bot, cq.message.chat.id, REWARD_SUBJECT, "Ingiliz tili darsi")
    await show_result(cq.message, REWARD_SUBJECT, "Ingiliz tili darsi", new_balance, kmsg)
    await cq.answer()


@dp.callback_query(F.data == "math")
async def cb_math(cq: CallbackQuery):
    new_balance, kmsg = await record_and_notify(cq.message.bot, cq.message.chat.id, REWARD_SUBJECT, "Matematika darsi")
    await show_result(cq.message, REWARD_SUBJECT, "Matematika darsi", new_balance, kmsg)
    await cq.answer()


@dp.callback_query(F.data == "haq_qarz_menu")
async def cb_haq_qarz_menu(cq: CallbackQuery):
    await cq.message.answer("Nima qilamiz?", reply_markup=haq_qarz_menu())
    await cq.answer()


@dp.callback_query(F.data == "back_main")
async def cb_back_main(cq: CallbackQuery):
    await cq.message.answer("Bosh menyu:", reply_markup=main_menu())
    await cq.answer()


@dp.callback_query(F.data == "balance")
async def cb_balance(cq: CallbackQuery):
    await send_balance(cq.message)
    await cq.answer()


@dp.callback_query(F.data == "kassa")
async def cb_kassa(cq: CallbackQuery):
    await send_kassa(cq.message)
    await cq.answer()


@dp.callback_query(F.data == "pay_settle")
async def cb_pay_settle(cq: CallbackQuery, state: FSMContext):
    bal = get_balance()
    if bal == 0:
        await cq.message.answer("Hisob allaqachon 0 so'm — to'lov shart emas.", reply_markup=main_menu())
        await cq.answer()
        return
    await cq.message.answer("Qancha pul to'ladingiz? Summani kiriting (masalan: 20000):")
    await state.set_state(Form.waiting_payment_amount)
    await cq.answer()


@dp.callback_query(F.data == "spend")
async def cb_spend(cq: CallbackQuery, state: FSMContext):
    await cq.message.answer(
        f"💵 Kassadagi mavjud pul: {fmt(get_kassa_cash())} so'm\n\n"
        "Qancha sarfladingiz? Summani kiriting (masalan: 20000):"
    )
    await state.set_state(Form.waiting_spend_amount)
    await cq.answer()


@dp.callback_query(F.data == "history")
async def cb_history(cq: CallbackQuery):
    await send_history(cq.message)
    await cq.answer()


@dp.callback_query(F.data == "book")
async def cb_book(cq: CallbackQuery, state: FSMContext):
    await cq.message.answer("Kitob necha pulga sotib olindi? Faqat summani yozing (masalan: 25000)")
    await state.set_state(Form.waiting_book_price)
    await cq.answer()


@dp.callback_query(F.data == "manual")
async def cb_manual(cq: CallbackQuery, state: FSMContext):
    await cq.message.answer(
        "Qancha yozay? Musbat son (masalan 5000) — dadangiz sizdan qarzdor bo'ladi.\n"
        "Manfiy son (masalan -3000) — siz dadangizdan haqdor bo'lasiz.\n"
        "Summani yozing:"
    )
    await state.set_state(Form.waiting_manual_amount)
    await cq.answer()


# ---------- FSM matn qadamlari (ustunlik beriladi) ----------

@dp.message(Form.waiting_book_price)
async def book_price_handler(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "")
    if not text.isdigit():
        await message.answer("Iltimos, musbat son yuboring (masalan: 25000)")
        return
    price = int(text)
    total = price + BOOK_EXTRA
    reason = f"Kitob (narxi {fmt(price)} + {fmt(BOOK_EXTRA)})"
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, total, reason)
    await state.clear()
    await show_result(message, total, reason, new_balance, kmsg)


@dp.message(Form.waiting_manual_amount)
async def manual_amount_handler(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "")
    if not text.lstrip("-").isdigit():
        await message.answer("Iltimos, faqat son yuboring (masalan: 5000 yoki -3000)")
        return
    manual_amount_tmp[message.chat.id] = int(text)
    await message.answer("Nima uchunligini yozing (sababi):")
    await state.set_state(Form.waiting_manual_reason)


@dp.message(Form.waiting_manual_reason)
async def manual_reason_handler(message: Message, state: FSMContext):
    amount = manual_amount_tmp.pop(message.chat.id, 0)
    reason = message.text.strip() or "Qo'lda kiritilgan"
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, amount, reason)
    await state.clear()
    await show_result(message, amount, reason, new_balance, kmsg)


@dp.message(Form.waiting_payment_amount)
async def payment_amount_handler(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "")
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat son yuboring (masalan: 20000)")
        return
    amount = int(text)
    apply_payment(amount)
    await state.clear()
    notify_text = (
        f"✅ {fmt(amount)} so'm to'lov qilindi — kassaga tushdi!\n\n"
        f"💵 Kassadagi mavjud pul: {fmt(get_kassa_cash())} so'm\n"
        f"{balance_status_text()}"
    )
    await broadcast(message.bot, message.chat.id, notify_text)
    await message.answer(notify_text, reply_markup=main_menu())


@dp.message(Form.waiting_spend_amount)
async def spend_amount_handler(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "")
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat son yuboring (masalan: 20000)")
        return
    spend_amount_tmp[message.chat.id] = int(text)
    await message.answer("Nimaga sarfladingiz? (masalan: yo'l kira uchun)")
    await state.set_state(Form.waiting_spend_reason)


@dp.message(Form.waiting_spend_reason)
async def spend_reason_handler(message: Message, state: FSMContext):
    amount = spend_amount_tmp.pop(message.chat.id, 0)
    reason = message.text.strip() or "Xarajat"
    remaining = record_expense(amount, reason)
    await state.clear()
    notify_text = (
        f"💸 Kassadan sarflandi: -{fmt(amount)} so'm — {reason}\n\n"
        f"💵 Kassadagi qolgan pul: {fmt(remaining)} so'm"
    )
    await broadcast(message.bot, message.chat.id, notify_text)
    await message.answer(notify_text, reply_markup=haq_qarz_menu())


# ---------- oddiy matn (faqat holat bo'sh bo'lganda ishlaydi) ----------

@dp.message(StateFilter(None), F.text.func(lambda t: bool(re.match(r"^\+\s*\d+", t.strip()))))
async def text_plus_entry(message: Message):
    m = re.match(r"^\+\s*(\d+)\s*(.*)", message.text.strip())
    amount = int(m.group(1))
    reason = m.group(2).strip() or "Qo'lda kiritilgan"
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, amount, reason)
    await show_result(message, amount, reason, new_balance, kmsg)


@dp.message(StateFilter(None), F.text.func(lambda t: bool(re.match(r"^-\s*\d+", t.strip()))))
async def text_minus_entry(message: Message):
    m = re.match(r"^-\s*(\d+)\s*(.*)", message.text.strip())
    amount = -int(m.group(1))
    reason = m.group(2).strip() or "Qo'lda kiritilgan"
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, amount, reason)
    await show_result(message, amount, reason, new_balance, kmsg)


@dp.message(StateFilter(None), F.text.func(lambda t: t.strip().lower() in ("qarz", "qarzim", "haqdorlik", "qarzdorlik")))
async def text_status_report(message: Message):
    await send_kassa(message)


@dp.message(StateFilter(None), F.text.func(lambda t: "kitob" in t.lower() and "old" in t.lower()))
async def text_book(message: Message, state: FSMContext):
    await message.answer("Kitob necha pulga sotib olindi? Faqat summani yozing (masalan: 25000)")
    await state.set_state(Form.waiting_book_price)


@dp.message(StateFilter(None), F.text.func(
    lambda t: "vazifa" in t.lower() and ("qilmad" in t.lower() or "bajarmad" in t.lower())
))
async def text_hw_not_done(message: Message):
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, -DEBT_TASK_NOT_DONE, "Uyga vazifa bajarilmadi")
    await show_result(message, -DEBT_TASK_NOT_DONE, "Uyga vazifa bajarilmadi", new_balance, kmsg)


@dp.message(StateFilter(None), F.text.func(
    lambda t: "vazifa" in t.lower() and ("qild" in t.lower() or "bajard" in t.lower())
))
async def text_hw_done(message: Message):
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, REWARD_TASK_DONE, "Uyga vazifa bajarildi")
    await show_result(message, REWARD_TASK_DONE, "Uyga vazifa bajarildi", new_balance, kmsg)


@dp.message(StateFilter(None), F.text.func(lambda t: "ingiliz" in t.lower()))
async def text_english(message: Message):
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, REWARD_SUBJECT, "Ingiliz tili darsi")
    await show_result(message, REWARD_SUBJECT, "Ingiliz tili darsi", new_balance, kmsg)


@dp.message(StateFilter(None), F.text.func(lambda t: "matematika" in t.lower()))
async def text_math(message: Message):
    new_balance, kmsg = await record_and_notify(message.bot, message.chat.id, REWARD_SUBJECT, "Matematika darsi")
    await show_result(message, REWARD_SUBJECT, "Matematika darsi", new_balance, kmsg)


@dp.message(StateFilter(None))
async def fallback_handler(message: Message):
    await message.answer(
        "Tushunmadim 🤔 Tugmalardan foydalaning yoki quyidagicha yozing:\n"
        "«uyga vazifa qildim», «ingiliz tili», «matematika», «kitob oldi», «+5000 izoh», «-3000 izoh»",
        reply_markup=main_menu(),
    )


async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(daily_digest_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
