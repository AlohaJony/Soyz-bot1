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

# ----------------------------- УТИЛИТЫ -----------------------------
def cleanup_old_files():
    """Удаляем файлы старше 1 часа"""
    try:
        now = time.time()
        for f in Path(DOWNLOAD_DIR).glob('*'):
            if f.is_file() and now - f.stat().st_mtime > 3600:
                f.unlink()
                logger.info(f"🧹 Удалён старый файл: {f.name}")
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")

cleanup_old_files()

def format_duration(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def extract_info(url: str) -> dict | None:
    """Получаем информацию о видео/посте через yt-dlp"""
    ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']:
                # Playlist / carousel
                entries = []
                for e in info['entries']:
                    if e is None:
                        continue
                    entries.append({
                        'type': 'video' if e.get('ext', '') else 'image',
                        'title': e.get('title', 'Без названия'),
                        'url': e.get('url', e.get('webpage_url')),
                        'duration': e.get('duration', 0),
                        'ext': e.get('ext', 'mp4'),
                        'thumbnail': e.get('thumbnail'),
                    })
                return {'type': 'playlist', 'title': info.get('title', 'Пост'), 'entries': entries,
                        'description': info.get('description', ''), 'webpage_url': url}
            else:
                # Single video
                return {'type': 'single',
                        'title': info.get('title', 'Без названия'),
                        'url': info.get('url', info.get('webpage_url')),
                        'duration': info.get('duration', 0),
                        'ext': info.get('ext', 'mp4'),
                        'thumbnail': info.get('thumbnail'),
                        'description': info.get('description', ''),
                        'webpage_url': url}
    except Exception as e:
        logger.error(f"Ошибка получения информации: {e}")
        return None

async def download_file(url: str, file_id: str, ext: str) -> str | None:
    """Скачиваем файл и сохраняем локально"""
    file_path = Path(DOWNLOAD_DIR) / f"{file_id}.{ext}"
    if file_path.exists():
        return str(file_path)
    ydl_opts = {'format': 'best[ext=mp4]/best' if ext in ('mp4', 'mov', 'avi', 'mkv') else 'best',
                'outtmpl': str(file_path), 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            if file_path.exists():
                logger.info(f"✅ Файл скачан: {file_path}")
                return str(file_path)
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
    return None

# ----------------------------- Fallback Яндекс.Диск -----------------------------
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
        logger.error(f"❌ Ошибка загрузки на Яндекс.Диск: {e}")
        return None
    finally:
        await client.close()

# ----------------------------- MAX SDK -----------------------------
async def send_media_via_sdk(chat_id: int, file_path: str, file_type: str):
    """
    Универсальная отправка медиа через MAX SDK
    file_type: 'video' или 'image'
    """
    # 1️⃣ Получаем upload URL
    upload = await max_bot.get_upload_url(file_type)
    token = upload.token
    upload_url = upload.url

    # 2️⃣ Загружаем файл
    with open(file_path, 'rb') as f:
        form = aiohttp.FormData()
        form.add_field("data", f, filename=os.path.basename(file_path))
        async with aiohttp.ClientSession() as session:
            async with session.post(upload_url, data=form) as resp:
                if resp.status not in (200, 201):
                    raise Exception(f"Upload failed {resp.status}")

    # 3️⃣ Отправляем сообщение
    delays = [2, 5, 10, 20]
    for attempt, delay in enumerate(delays, 1):
        try:
            await max_bot.send_message(
                chat_id=chat_id,
                text="",
                attachments=[{"type": file_type, "payload": {"token": token}}]
            )
            return True
        except Exception as e:
            if "attachment.not.ready" in str(e):
                await asyncio.sleep(delay)
            else:
                raise
    return False

# ----------------------------- ОБРАБОТКА ССЫЛОК -----------------------------
async def handle_url(event, url: str):
    chat_id = event.message.chat.id
    status_msg = await event.message.answer("🔍 Получаю информацию...")

    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await status_msg.message.edit("❌ Не удалось получить информацию о контенте.")
        return

    await status_msg.message.edit("📥 Начинаю загрузку...")

    # ------------------ SINGLE ------------------
    if info['type'] == 'single':
        safe_title = re.sub(r'\W+', '', info['title'][:30])
        file_path = await download_file(info['url'], safe_title, info['ext'])
        if not file_path:
            await status_msg.message.edit("❌ Не удалось скачать файл.")
            return

        success = await send_media_via_sdk(chat_id, file_path, 'video')
        if not success:
            public_url = await upload_to_yadisk(file_path)
            if public_url:
                await event.message.answer(f"🔗 Видео доступно на Яндекс.Диск: {public_url}")

        Path(file_path).unlink(missing_ok=True)

        if info.get('description'):
            await event.message.answer(f"📝 Описание:\n\n{info['description'][:4000]}")

    # ------------------ PLAYLIST / CAROUSEL ------------------
    if info['type'] == 'playlist':
        tasks = []
        for idx, entry in enumerate(info['entries']):
            safe_title = re.sub(r'\W+', '', entry['title'][:30])
            tasks.append(download_file(entry['url'], f"{safe_title}_{idx}", entry['ext']))
        file_paths = await asyncio.gather(*tasks)

        for idx, path in enumerate(file_paths):
            if not path:
                continue
            entry = info['entries'][idx]
            success = await send_media_via_sdk(chat_id, path, 'video' if entry['type'] == 'video' else 'image')
            if not success:
                public_url = await upload_to_yadisk(path)
                if public_url:
                    await event.message.answer(f"🔗 Медиа доступно на Яндекс.Диск: {public_url}")
            Path(path).unlink(missing_ok=True)

        if info.get('description'):
            await event.message.answer(f"📝 Описание поста:\n\n{info['description'][:4000]}")

    try:
        await status_msg.message.delete()
    except:
        pass

# ----------------------------- ИНИЦИАЛИЗАЦИЯ -----------------------------
max_bot = MaxBot(token=TOKEN)
dp = Dispatcher()

@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text or ''
    if text == '/start':
        await event.message.answer("👋 Привет! Отправь ссылку на видео или пост.")
        return
    urls = re.findall(r'https?://\S+', text)
    if urls:
        await handle_url(event, urls[0])
    else:
        await event.message.answer("❌ Не удалось найти ссылку.")

@dp.bot_started()
async def handle_bot_started(event: BotStarted):
    await max_bot.api.send_message(event.chat_id, "👋 Привет! Я бот для скачивания видео и картинок.")

async def main():
    logger.info("Бот запущен...")
    await dp.start_polling(max_bot)

if __name__ == '__main__':
    asyncio.run(main())
