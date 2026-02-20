import asyncio
import logging
import os
import re
import aiohttp
import yt_dlp
import yadisk
import time
from pathlib import Path
from maxapi import Bot as MaxBot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("❌ Не задан BOT_TOKEN в переменных окружения")

YADISK_TOKEN = os.getenv('YADISK_TOKEN')
if not YADISK_TOKEN:
    raise ValueError("❌ Не задан YADISK_TOKEN в переменных окружения")

DOWNLOAD_DIR = 'downloads'
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Очистка старых файлов при запуске
def cleanup_old_files():
    try:
        now = time.time()
        for f in Path(DOWNLOAD_DIR).glob('*'):
            if f.is_file() and now - f.stat().st_mtime > 3600:
                f.unlink()
                logger.info(f"🧹 Удалён старый файл: {f.name}")
    except Exception as e:
        logger.error(f"Ошибка при очистке: {e}")

cleanup_old_files()

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def format_duration(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def extract_info(url: str) -> dict | None:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']:
                # playlist или carousel
                entries = []
                for entry in info['entries']:
                    if entry is None:
                        continue
                    entries.append({
                        'title': entry.get('title', 'Без названия'),
                        'duration': entry.get('duration', 0),
                        'uploader': entry.get('uploader', 'Неизвестный автор'),
                        'description': entry.get('description', ''),
                        'webpage_url': entry.get('webpage_url', url),
                        'ext': entry.get('ext', 'mp4'),
                        'thumbnail': entry.get('thumbnail'),
                        'type': 'video' if entry.get('ext','').lower() in ['mp4','mov','avi','mkv'] else 'image'
                    })
                return {
                    'type': 'playlist',
                    'title': info.get('title', 'Пост'),
                    'entries': entries,
                    'webpage_url': url,
                    'description': info.get('description', '')
                }
            else:
                return {
                    'type': 'single',
                    'title': info.get('title', 'Без названия'),
                    'duration': info.get('duration', 0),
                    'uploader': info.get('uploader', 'Неизвестный автор'),
                    'description': info.get('description', ''),
                    'webpage_url': info.get('webpage_url', url),
                    'ext': info.get('ext', 'mp4'),
                    'thumbnail': info.get('thumbnail'),
                    'type': 'video' if info.get('ext','').lower() in ['mp4','mov','avi','mkv'] else 'image'
                }
    except Exception as e:
        logger.error(f"Ошибка получения информации: {e}")
        return None

async def download_file(url: str, file_id: str, ext: str) -> str | None:
    filename = f"{file_id}.{ext}"
    file_path = Path(DOWNLOAD_DIR) / filename
    if file_path.exists():
        return str(file_path)
    ydl_opts = {
        'format': 'best[ext=mp4]/best' if ext in ('mp4', 'mov', 'avi', 'mkv') else 'best',
        'outtmpl': str(file_path),
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            if file_path.exists():
                logger.info(f"✅ Файл скачан: {file_path}")
                return str(file_path)
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

async def upload_to_yadisk(file_path: str) -> str | None:
    client = yadisk.AsyncClient(token=YADISK_TOKEN)
    try:
        try:
            await client.mkdir("/bot_uploads")
        except yadisk.exceptions.ConflictError:
            pass
        disk_path = f"/bot_uploads/{os.path.basename(file_path)}"
        await client.upload(file_path, disk_path, overwrite=True)
        await client.publish(disk_path)
        meta = await client.get_meta(disk_path)
        return meta.public_url
    except Exception as e:
        logger.error(f"Ошибка Яндекс.Диск: {e}")
        return None
    finally:
        await client.close()

async def send_via_sdk(chat_id: int, file_path: str, entry_info: dict):
    ext = entry_info.get('ext','mp4').lower()
    caption = f"🎬 {entry_info['title']}\n👤 {entry_info['uploader']}\n⏱ {format_duration(entry_info['duration'])}\n🔗 {entry_info['webpage_url']}"
    
    # Выбираем метод загрузки по типу
    try:
        if entry_info['type'] == 'video':
            upload = await max_bot.get_upload_url("video")
        else:
            upload = await max_bot.get_upload_url("image")

        token = upload.token
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field("data", f, filename=os.path.basename(file_path))
                async with session.post(upload.url, data=data) as resp:
                    if resp.status not in (200,201):
                        text = await resp.text()
                        raise Exception(f"Upload failed: {text}")

        # Отправка через SDK
        attachment_type = "video" if entry_info['type']=='video' else "image"
        await max_bot.send_message(
            chat_id=chat_id,
            text=caption,
            attachments=[{"type": attachment_type, "payload": {"token": token}}]
        )
        logger.info(f"✅ {attachment_type} отправлен через SDK")
        return True
    except Exception as e:
        logger.error(f"Ошибка SDK: {e}")
        # fallback на Яндекс.Диск
        url = await upload_to_yadisk(file_path)
        if url:
            await max_bot.send_message(chat_id=chat_id, text=f"🔗 Видео/картинка доступно на Яндекс.Диск: {url}")
            return True
    return False

# ----------------------------- ОБРАБОТКА URL -----------------------------
async def handle_url(event, url: str):
    chat_id = event.chat_id  # <- верный способ получить chat_id
    status_msg = await max_bot.api.sendMessageToChat(chat_id, "🔍 Получаю информацию...")

    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await max_bot.api.sendMessageToChat(chat_id, "❌ Не удалось получить информацию.")
        return

    await max_bot.api.sendMessageToChat(chat_id, "📥 Начинаю загрузку...")

    successful_paths = []

    # SINGLE
    if info['type']=='single':
        safe_title = re.sub(r'\W+','',info['title'][:30])
        file_path = await download_file(info['webpage_url'], safe_title, info.get('ext','mp4'))
        if file_path:
            success = await send_via_sdk(chat_id, file_path, info)
            if success:
                successful_paths.append(file_path)

    # PLAYLIST / CAROUSEL
    if info['type']=='playlist':
        for idx, entry in enumerate(info['entries']):
            safe_title = re.sub(r'\W+','',entry['title'][:30])
            file_path = await download_file(entry['webpage_url'], safe_title, entry.get('ext','mp4'))
            if file_path:
                success = await send_via_sdk(chat_id, file_path, entry)
                if success:
                    successful_paths.append(file_path)

    if successful_paths:
        if info.get('description'):
            await max_bot.api.sendMessageToChat(chat_id, f"📝 Описание поста:\n{info['description'][:4000]}")
        await max_bot.api.sendMessageToChat(chat_id, "❤️ Если вам понравился бот, поддержите проект: 💸 [Донат](https://donate.example.com)")

    # удаляем временные файлы
    for f in successful_paths:
        Path(f).unlink(missing_ok=True)

# ----------------------------- ИНИЦИАЛИЗАЦИЯ -----------------------------
max_bot = MaxBot(token=TOKEN)
dp = Dispatcher()

@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text or ''
    if text.startswith('/start'):
        await max_bot.api.sendMessageToChat(event.chat_id, "👋 Привет! Отправь ссылку на видео или пост.")
        return

    urls = re.findall(r'https?://\S+', text)
    if urls:
        await handle_url(event, urls[0])
    else:
        await max_bot.api.sendMessageToChat(event.chat_id, "❌ Не удалось найти ссылку.")

@dp.bot_started()
async def handle_bot_started(event: BotStarted):
    await max_bot.api.sendMessageToChat(event.chat_id, "👋 Бот запущен и готов к работе!")

# ----------------------------- ЗАПУСК -----------------------------
async def main():
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(max_bot)

if __name__ == '__main__':
    asyncio.run(main())
