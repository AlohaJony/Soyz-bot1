import os
import re
import logging
from maxapi import Bot, MessageType
from maxapi.attachments import ImageAttachment, VideoAttachment, FileAttachment

# Для загрузки файлов на Яндекс.Диск
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------
# Настройки
# ----------------------
MAX_BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен бота MAX
YANDEX_DISK_TOKEN = os.getenv("YADISK_TOKEN")  # Токен Яндекс.Диск
FALLBACK_FOLDER = "Telegram_Fallback"

bot = Bot(MAX_BOT_TOKEN)

# ----------------------
# Вспомогательные функции
# ----------------------
def extract_urls(text):
    """Парсим ссылки из текста"""
    return re.findall(r"https?://\S+", text)

def save_to_yandex_disk(file_url, filename):
    """Сохраняем файл на Яндекс.Диск как fallback"""
    headers = {"Authorization": f"OAuth {YANDEX_DISK_TOKEN}"}
    upload_url = "https://cloud-api.yandex.net/v1/disk/resources/upload"
    params = {"path": f"{FALLBACK_FOLDER}/{filename}", "url": file_url, "overwrite": "true"}
    resp = requests.post(upload_url, headers=headers, params=params)
    if resp.status_code == 202:
        logger.info(f"Сохранили {filename} на Яндекс.Диск")
    else:
        logger.error(f"Не удалось сохранить {filename} на Яндекс.Диск: {resp.text}")

async def handle_url(event, url):
    """Обрабатываем ссылку и отправляем в MAX"""
    chat_id = event.message.body.chat_id

    try:
        # Для картинок
        if url.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            image = await event.api.uploadImage({"url": url})
            await event.reply("Вот ваша картинка:", attachments=[image.toJson()])
            return

        # Для видео
        if url.lower().endswith((".mp4", ".mov", ".webm")):
            video = await event.api.uploadVideo({"url": url})
            await event.reply("Вот ваше видео:", attachments=[video.toJson()])
            return

        # Для Instagram (carousel)
        if "instagram.com/p/" in url or "instagram.com/reel/" in url:
            # Пример: получаем картинки и видео через сторонний сервис (например, Instaloader / API)
            media_urls = get_instagram_media(url)  # твоя функция получения всех медиа в посте
            attachments = []
            for media in media_urls:
                if media.lower().endswith((".jpg", ".png", ".webp")):
                    img = await event.api.uploadImage({"url": media})
                    attachments.append(img.toJson())
                elif media.lower().endswith((".mp4", ".mov")):
                    vid = await event.api.uploadVideo({"url": media})
                    attachments.append(vid.toJson())
            if attachments:
                await event.reply("Вот медиа из Instagram:", attachments=attachments)
                return

        # Если не удалось определить, fallback на Яндекс.Диск
        filename = url.split("/")[-1].split("?")[0]
        save_to_yandex_disk(url, filename)
        await event.reply(f"Ссылка сохранена на Яндекс.Диск: {filename}")

    except Exception as e:
        logger.error(f"Ошибка при обработке URL {url}: {e}")
        # Фолбэк на Яндекс.Диск
        filename = url.split("/")[-1].split("?")[0]
        save_to_yandex_disk(url, filename)
        await event.reply(f"Не удалось обработать ссылку, сохранено на Яндекс.Диск: {filename}")

# ----------------------
# Основной обработчик сообщений
# ----------------------
@bot.on("message_created")
async def handle_message(event):
    message_body = event.message.body
    text = message_body.text or ""
    chat_id = message_body.chat_id

    # Приветствие для нового пользователя
    if not text.strip():
        await event.reply("👋 Привет! Отправь ссылку на видео или пост.")
        return

    urls = extract_urls(text)
    if not urls:
        await event.reply("Не нашёл ссылок в сообщении 😅")
        return

    # Обрабатываем каждую ссылку
    for url in urls:
        await handle_url(event, url)

# ----------------------
# Запуск бота
# ----------------------
if __name__ == "__main__":
    logger.info("Бот запущен и слушает...")
    bot.start()
