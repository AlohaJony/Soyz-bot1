import asyncio
import logging
import os
import re
import sqlite3
import subprocess  # <-- добавьте этот импорт, если его нет
from datetime import datetime, timedelta
from pathlib import Path

import yt_dlp
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))

# Стоимость подписок
SUBSCRIPTION_PRICES = {
    'week': 200,
    'month': 599
}

FREE_LIMIT_SECONDS = 10 * 60
DB_PATH = 'subscriptions.db'
DOWNLOAD_DIR = 'downloads'
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------- УСТАНОВКА FFMPEG (если нужно) -----------------------------
def install_ffmpeg():
    """Пытается установить ffmpeg, если его нет (для Debian/Ubuntu)."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        logger.info("✅ ffmpeg уже установлен")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("📦 ffmpeg не найден, пытаюсь установить...")
        try:
            subprocess.run(["apt-get", "update"], check=True, timeout=60)
            subprocess.run(["apt-get", "install", "-y", "ffmpeg"], check=True, timeout=120)
            logger.info("✅ ffmpeg успешно установлен")
        except Exception as e:
            logger.error(f"❌ Не удалось установить ffmpeg: {e}")
            logger.warning("⚠️ Продолжаю без ffmpeg. Некоторые видео могут не скачиваться (требуется объединение потоков).")

# Вызываем функцию
install_ffmpeg()

# ----------------------------- РАБОТА С БАЗОЙ ДАННЫХ -----------------------------
# ... (дальше идут ваши функции init_db, get_subscription и т.д.)

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

def extract_video_info(url: str) -> dict | None:
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
        'quiet': False,          # Включаем вывод yt-dlp
        'no_warnings': False,
        'verbose': True,         # Максимально подробно
    }
    try:
        # Проверяем, доступна ли папка для записи
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        logger.info(f"📥 Начинаю скачивание: {url}")

        # Запускаем yt-dlp в отдельном потоке, чтобы не блокировать event loop
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, lambda: _sync_download(url, ydl_opts))
        return result
    except Exception as e:
        logger.error(f"🔥 Ошибка в download_video: {e}", exc_info=True)
        return None

def _sync_download(url: str, ydl_opts: dict) -> str | None:
    """Синхронная функция для запуска в executor."""
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if Path(filename).exists():
                logger.info(f"✅ Файл скачан: {filename}")
                return filename
            # Ищем по ID
            for f in Path(DOWNLOAD_DIR).glob(f"{info['id']}.*"):
                logger.info(f"✅ Найден альтернативный файл: {f}")
                return str(f)
            logger.error("❌ Файл не найден после скачивания")
            return None
        except Exception as e:
            logger.error(f"❌ Ошибка в _sync_download: {e}", exc_info=True)
            return None
        
# ----------------------------- ИНИЦИАЛИЗАЦИЯ БОТА -----------------------------
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------------------- ОБРАБОТЧИКИ КОМАНД -----------------------------
@dp.message_created()
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

        # (Опционально: отладка структуры status_msg, можно раскомментировать при необходимости)
        # logging.info("===== STATUS_MSG ATTRIBUTES =====")
        # logging.info(f"Тип status_msg: {type(status_msg)}")
        # logging.info(f"Атрибуты status_msg: {dir(status_msg)}")
        # if hasattr(status_msg, 'message'):
        #     logging.info(f"Тип status_msg.message: {type(status_msg.message)}")
        #     logging.info(f"Атрибуты status_msg.message: {dir(status_msg.message)}")
        #     if hasattr(status_msg.message, 'recipient'):
        #         logging.info(f"message.recipient атрибуты: {dir(status_msg.message.recipient)}")
        #         if hasattr(status_msg.message.recipient, 'chat_id'):
        #             logging.info(f"message.recipient.chat_id = {status_msg.message.recipient.chat_id}")

        # Получаем метаданные
        info = await asyncio.to_thread(extract_video_info, url)
        if not info:
            await status_msg.message.edit("❌ Не удалось получить информацию о видео. Проверьте ссылку.")
            return

        # Проверяем длительность
        duration = info['duration']
        if duration > FREE_LIMIT_SECONDS:
            sub = get_subscription(user_id)
            if not sub:
                await status_msg.message.edit(
                    f"⏱ Видео длится {format_duration(duration)} (больше 10 минут).\n"
                    f"🔒 Для скачивания длинных видео нужна подписка.\n"
                    f"Наберите /subscribe для оформления."
                )
                return
            else:
                await status_msg.message.edit(
                    f"⏱ Длительность: {format_duration(duration)}. Подписка активна, скачиваю..."
                )
        else:
            await status_msg.message.edit(
                f"⏱ Длительность: {format_duration(duration)}. Скачиваю..."
            )

        # Скачиваем видео
        file_path = await download_video(url)
        if not file_path or not Path(file_path).exists():
            await status_msg.message.edit("❌ Не удалось скачать видео. Возможно, видео защищено или недоступно.")
            return

        # Отправляем видео
        caption = (f"🎬 {info['title']}\n"
                   f"👤 {info['uploader']}\n"
                   f"⏱ {format_duration(duration)}\n"
                   f"🔗 {info['webpage_url']}")
        
        try:
            # 1. Загружаем файл, передаём путь и тип (скорее всего 'video')
            file_type = 'video'  # для видео
            upload_result = await bot.upload_file(file_path, file_type)
            logging.info(f"✅ Файл загружен, результат: {upload_result}")
            
            # 2. Извлекаем file_id (может быть строкой или объектом)
            if isinstance(upload_result, str):
                file_id = upload_result
            elif hasattr(upload_result, 'file_id'):
                file_id = upload_result.file_id
            elif isinstance(upload_result, dict) and 'file_id' in upload_result:
                file_id = upload_result['file_id']
            else:
                file_id = str(upload_result)
                logging.warning(f"⚠️ Неизвестный формат upload_result, используется как есть: {file_id}")
            
            # 3. Отправляем сообщение с файлом
            await bot.send_message(
                chat_id=event.message.recipient.chat_id,
                text=caption,
                file_id=file_id
            )
            logging.info("✅ Видео отправлено через send_message с file_id")
            
        except Exception as e:
            logging.error(f"❌ Ошибка при отправке видео: {e}", exc_info=True)
            await status_msg.message.edit("❌ Не удалось отправить видео. Проверьте логи.")
            return

        # Удаляем статусное сообщение
        await status_msg.message.delete()

        # Удаляем файл с диска
        Path(file_path).unlink(missing_ok=True)

        # Отправляем описание (если есть)
        if info['description']:
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
