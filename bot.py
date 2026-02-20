import os
import re
import asyncio
import logging
import aiohttp
from pathlib import Path

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан")

bot = Bot(token=TOKEN)
dp = Dispatcher()

DOWNLOAD_DIR = "downloads"
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# -------------------------
# Вспомогательные функции
# -------------------------

def extract_urls(text: str):
    return re.findall(r"https?://\S+", text or "")


async def upload_file_to_max(file_path: str, media_type: str):
    """
    media_type: 'image' или 'video'
    """
    upload = await bot.get_upload_url(media_type)

    async with aiohttp.ClientSession() as session:
        with open(file_path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("data", f, filename=os.path.basename(file_path))

            async with session.post(upload.url, data=data) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    raise Exception(f"Upload error: {text}")

    return upload.token


async def download_file(url: str, filename: str):
    file_path = Path(DOWNLOAD_DIR) / filename

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()
            with open(file_path, "wb") as f:
                f.write(content)

    return str(file_path)


# -------------------------
# Обработка ссылки
# -------------------------

async def handle_url(event, url: str):
    chat_id = event.chat_id  # ✅ правильный способ для твоей версии SDK

    await bot.send_message(chat_id, "🔍 Обрабатываю ссылку...")

    filename = url.split("/")[-1].split("?")[0]

    if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        media_type = "image"
    elif url.lower().endswith((".mp4", ".mov", ".webm")):
        media_type = "video"
    else:
        await bot.send_message(chat_id, "❌ Тип файла не поддерживается напрямую.")
        return

    file_path = await download_file(url, filename)
    if not file_path:
        await bot.send_message(chat_id, "❌ Не удалось скачать файл.")
        return

    try:
        token = await upload_file_to_max(file_path, media_type)

        await bot.send_message(
            chat_id=chat_id,
            text="Готово ✅",
            attachments=[
                {
                    "type": media_type,
                    "payload": {
                        "token": token
                    }
                }
            ]
        )

        logger.info("Файл успешно отправлен")

    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await bot.send_message(chat_id, "❌ Ошибка отправки файла.")

    finally:
        try:
            os.remove(file_path)
        except:
            pass


# -------------------------
# Обработчик сообщений
# -------------------------

@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text or ""
    urls = extract_urls(text)

    if text.startswith("/start"):
        await bot.send_message(event.chat_id, "👋 Привет! Отправь ссылку.")
        return

    if not urls:
        await bot.send_message(event.chat_id, "Ссылки не найдены.")
        return

    for url in urls:
        await handle_url(event, url)


# -------------------------
# Запуск
# -------------------------

async def main():
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
