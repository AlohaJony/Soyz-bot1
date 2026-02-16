import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
import yt_dlp
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # ID администратора для ручной активации
# Стоимость подписок (в рублях)
SUBSCRIPTION_PRICES = {
    'week': 200,
    'month': 599
}

# Длительность видео, после которой требуется подписка (в секундах)
FREE_LIMIT_SECONDS = 10 * 60  # 10 минут

# Путь к базе данных
DB_PATH = 'subscriptions.db'

# Папка для скачанных видео
DOWNLOAD_DIR = 'downloads'
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------- РАБОТА С БАЗОЙ ДАННЫХ -----------------------------
def init_db():
    """Создаёт таблицу подписок, если её нет."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            expires_at TIMESTAMP,
            subscribed_since TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_subscription(user_id: int):
    """Возвращает дату окончания подписки или None."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT expires_at FROM subscriptions WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        # Парсим строку с датой
        try:
            expires = datetime.fromisoformat(row[0])
            if expires > datetime.now():
                return expires
        except:
            pass
    return None

def add_subscription(user_id: int, duration_days: int):
    """Добавляет или продлевает подписку."""
    expires = datetime.now() + timedelta(days=duration_days)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO subscriptions (user_id, expires_at)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET expires_at = ?
    ''', (user_id, expires.isoformat(), expires.isoformat()))
    conn.commit()
    conn.close()

def remove_subscription(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def format_duration(seconds: int) -> str:
    """Форматирует длительность в ЧЧ:ММ:СС или ММ:СС."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"

def extract_video_info(url: str) -> dict:
    """Извлекает информацию о видео через yt-dlp."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,  # не скачиваем, только получаем метаданные
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Без названия'),
                'uploader': info.get('uploader', 'Неизвестный автор'),
                'duration': info.get('duration', 0),
                'description': info.get('description', ''),
                'webpage_url': info.get('webpage_url', url),
                'extractor': info.get('extractor', 'unknown'),
            }
        except Exception as e:
            logger.error(f"Ошибка получения информации: {e}")
            return None

async def download_video(url: str) -> str | None:
    """Скачивает видео и возвращает путь к файлу."""
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Определяем имя файла
            filename = ydl.prepare_filename(info)
            if Path(filename).exists():
                return filename
            # Если расширение не совпало – ищем по ID
            base = Path(DOWNLOAD_DIR) / info['id']
            for f in Path(DOWNLOAD_DIR).glob(f"{info['id']}.*"):
                return str(f)
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ----------------------------- ИНИЦИАЛИЗАЦИЯ БОТА -----------------------------
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------------------- ОБРАБОТЧИКИ КОМАНД -----------------------------
@dp.message_created()
#async def handle_message(event: MessageCreated):
    # ОТЛАДКА
    #logging.info(f"Тип event.message: {type(event.message)}")
    #logging.info(f"Доступные атрибуты message: {dir(event.message)}")
    #if hasattr(event.message, 'from_'):
        #logging.info(f"from_ есть, тип: {type(event.message.from_)}")
        #if hasattr(event.message.from_, 'id'):
            #logging.info("from_.id доступен")
        #else:
            #logging.info("from_.id НЕ доступен")
    #else:
        #logging.info("from_ отсутствует")
async def handle_message(event: MessageCreated):
    user_id = event.message.sender.user_id 
    text = event.message.body.text or ''
    text_lower = text.lower().strip()

    # Команда /start
    if text_lower == '/start':
        await event.message.answer(
            "👋 Привет! Я бот для скачивания видео из YouTube, Instagram и TikTok.\n\n"
            "📥 Просто отправь мне ссылку на видео.\n"
            "🔒 Если видео длится больше 10 минут, для скачивания нужна подписка.\n"
            "💳 Набери /subscribe, чтобы узнать условия."
        )
        return

    # Команда /subscribe
    if text_lower == '/subscribe':
        sub = get_subscription(user_id)
        if sub:
            days_left = (sub - datetime.now()).days
            msg = f"✅ У вас активна подписка до {sub.strftime('%d.%m.%Y')} (осталось {days_left} дн.)"
        else:
            msg = "❌ У вас нет активной подписки.\n\n"
        msg += (
            f"💰 Тарифы:\n"
            f"• Неделя — {SUBSCRIPTION_PRICES['week']} руб.\n"
            f"• Месяц — {SUBSCRIPTION_PRICES['month']} руб.\n\n"
            f"Для оплаты отправьте /payweek или /paymonth.\n"
            f"После оплаты пришлите скриншот администратору @your_admin (замени на свой контакт)."
        )
        await event.message.answer(msg)
        return

    # Команды оплаты (заглушка)
    if text_lower == '/payweek':
        await event.message.answer(
            f"💳 Для оплаты недельной подписки ({SUBSCRIPTION_PRICES['week']} руб.) переведите сумму на карту:\n"
            f"`1234 5678 9012 3456`\n"
            f"(укажите в комментарии ваш ID: {user_id})\n\n"
            f"После оплаты отправьте скриншот администратору."
        )
        return

    if text_lower == '/paymonth':
        await event.message.answer(
            f"💳 Для оплаты месячной подписки ({SUBSCRIPTION_PRICES['month']} руб.) переведите сумму на карту:\n"
            f"`1234 5678 9012 3456`\n"
            f"(укажите в комментарии ваш ID: {user_id})\n\n"
            f"После оплаты отправьте скриншот администратору."
        )
        return

    # Команда администратора для активации подписки (скрытая)
    if text_lower.startswith('/activate') and event.message.sender.user_id == ADMIN_ID:
        parts = text.split()
        if len(parts) == 3:
            try:
                target_id = int(parts[1])
                days = int(parts[2])
                add_subscription(target_id, days)
                await event.message.answer(f"✅ Подписка для {target_id} активирована на {days} дней.")
            except:
                await event.message.answer("❌ Ошибка. Используй: /activate USER_ID DAYS")
        else:
            await event.message.answer("❌ Неверный формат. /activate USER_ID DAYS")
        return

    # Обработка ссылок
    if 'http://' in text or 'https://' in text:
        # Извлекаем первую ссылку
        urls = re.findall(r'https?://\S+', text)
        if not urls:
            await event.message.answer("❌ Не удалось найти ссылку.")
            return
        url = urls[0]

        # Отправляем статус
        status_msg = await event.message.answer("🔍 Получаю информацию о видео...")
        # ОТЛАДКА status_msg
        logging.info("===== STATUS_MSG ATTRIBUTES =====")
        logging.info(f"Тип status_msg: {type(status_msg)}")
        logging.info(f"Атрибуты status_msg: {dir(status_msg)}")
        if hasattr(status_msg, 'recipient'):
            logging.info(f"recipient атрибуты: {dir(status_msg.recipient)}")
        if hasattr(status_msg, 'chat'):
            logging.info(f"chat атрибуты: {dir(status_msg.chat)}")
   

        # Получаем метаданные
        info = await asyncio.to_thread(extract_video_info, url)
        if not info:
            await bot.edit_message(
                status_msg.chat_id,
                status_msg.message_id,
                "❌ Не удалось получить информацию о видео. Проверьте ссылку."
            )
            return

        # Проверяем длительность
        duration = info['duration']
        if duration > FREE_LIMIT_SECONDS:
            # Проверяем подписку
            sub = get_subscription(user_id)
            if not sub:
                await bot.edit_message(
                    status_msg.chat_id,
                    status_msg.message_id,
                    f"⏱ Видео длится {format_duration(duration)} (больше 10 минут).\n"
                    f"🔒 Для скачивания длинных видео нужна подписка.\n"
                    f"Наберите /subscribe для оформления."
                )
                return
            else:
                # Подписка есть – можно качать
                await bot.edit_message(
                    status_msg.chat_id,
                    status_msg.message_id,
                    f"⏱ Длительность: {format_duration(duration)}. Подписка активна, скачиваю..."
                )
        else:
            await bot.edit_message(
                status_msg.chat_id,
                status_msg.message_id,
                f"⏱ Длительность: {format_duration(duration)}. Скачиваю..."
            )

        # Скачиваем видео
        file_path = await download_video(url)
        if not file_path or not Path(file_path).exists():
            await bot.edit_message(
                status_msg.chat_id,
                status_msg.message_id,
                "❌ Не удалось скачать видео. Возможно, видео защищено или недоступно."
            )
            return

        # Отправляем видео
        caption = (f"🎬 {info['title']}\n"
                   f"👤 {info['uploader']}\n"
                   f"⏱ {format_duration(duration)}\n"
                   f"🔗 {info['webpage_url']}")
        await event.message.answer_with_file(
            file_path=file_path,
            caption=caption
        )

        # Удаляем статус
        # Пока используем chat.id, но после отладки может измениться
        await bot.delete_message(status_msg.chat.id, status_msg.message_id)

        # Удаляем файл с диска
        Path(file_path).unlink(missing_ok=True)

        # Отправляем описание (если есть)
        if info['description']:
            # Обрезаем слишком длинное описание (MAX ограничение 4096 символов)
            desc = info['description'][:4000]
            await event.message.answer(f"📝 Описание:\n\n{desc}")

        # Сообщение о поддержке
        await event.message.answer(
            "❤️ Если вам понравился бот, поддержите проект:\n"
            "💸 [Ссылка на донат](https://donate.example.com)\n"
            "Спасибо!"
        )

    else:
        # Не ссылка и не команда
        await event.message.answer("Отправьте мне ссылку на видео с YouTube, Instagram или TikTok.")

# ----------------------------- ЗАПУСК БОТА -----------------------------
async def main():
    init_db()
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
