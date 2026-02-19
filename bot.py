import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import yt_dlp
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("❌ Не задан BOT_TOKEN в переменных окружения")
    sys.exit(1)

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def extract_info(url: str) -> dict | None:
    """Извлекает информацию о видео (только для логов)."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Без названия'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Неизвестный'),
                'ext': info.get('ext', 'mp4'),
                'description': info.get('description', '')
            }
        except Exception as e:
            logger.error(f"Ошибка получения информации: {e}")
            return None

def download_file(url: str, file_id: str, ext: str) -> Path | None:
    """Скачивает видео и возвращает путь к файлу."""
    file_path = DOWNLOAD_DIR / f"{file_id}.{ext}"
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': str(file_path),
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            if file_path.exists():
                logger.info(f"✅ Файл скачан: {file_path}")
                return file_path
            logger.error("Файл не найден после скачивания")
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ----------------------------- ИНИЦИАЛИЗАЦИЯ БОТА -----------------------------
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------------------- ОБРАБОТЧИКИ -----------------------------
@dp.bot_started()
async def on_bot_started(event: BotStarted):
    logger.info(f"🚀 Бот запущен пользователем. chat_id={event.chat_id}, user_id={event.user.user_id if event.user else 'unknown'}")

@dp.message_created()
async def handle_message(event: MessageCreated):
    # Собираем информацию о сообщении
    user_id = event.message.sender.user_id if event.message.sender else None
    chat_id = event.message.recipient.chat_id if event.message.recipient else None
    text = event.message.body.text or ''
    msg_id = event.message.body.mid if event.message.body else None

    logger.info("=" * 60)
    logger.info(f"📩 Получено сообщение: user_id={user_id}, chat_id={chat_id}, msg_id={msg_id}")
    logger.info(f"Текст: {text[:200]}")
    logger.info("=" * 60)

    # Проверяем, есть ли в тексте ссылка
    url_match = re.search(r'https?://\S+', text)
    if url_match:
        url = url_match.group()
        logger.info(f"🔗 Обнаружена ссылка: {url}")

        # Этап 1: получение информации (только для логов)
        info = extract_info(url)
        if not info:
            await event.message.answer("❌ Не удалось получить информацию о видео.")
            return
        logger.info(f"📋 Информация: {info['title']} ({info['duration']} сек)")

        # Этап 2: скачивание файла
        file_id = re.sub(r'\W+', '', info['title'][:30])
        file_path = download_file(url, file_id, info['ext'])
        if not file_path:
            await event.message.answer("❌ Не удалось скачать видео.")
            return
        logger.info(f"💾 Файл сохранён: {file_path}")

        # Этап 3: отправка файла обратно пользователю
        try:
            logger.info("📤 Отправка файла через answer_with_file...")
            await event.message.answer_with_file(file_path=str(file_path))
            logger.info("✅ Файл успешно отправлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке файла: {e}", exc_info=True)
            await event.message.answer("❌ Не удалось отправить видео.")
        finally:
            # Удаляем временный файл
            file_path.unlink(missing_ok=True)
            logger.info("🧹 Временный файл удалён")
    else:
        # Обычное сообщение – просто тестовый ответ
        logger.info("🔄 Обычное сообщение, отправляем тестовый ответ...")
        await event.message.answer("Привет! Отправь мне ссылку на видео.")
        logger.info("✅ Ответ отправлен")

# ----------------------------- ЗАПУСК -----------------------------
async def main():
    logger.info("🚀 Запуск тестового бота...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен вручную.")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
