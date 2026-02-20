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
TOKEN = os.getenv("BOT_TOKEN")
YADISK_TOKEN = os.getenv("YADISK_TOKEN")
DOWNLOAD_DIR = "downloads"
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not TOKEN:
    raise ValueError("❌ Не задан BOT_TOKEN")
if not YADISK_TOKEN:
    raise ValueError("❌ Не задан YADISK_TOKEN")

# ----------------------------- УТИЛИТЫ -----------------------------
def format_duration(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def cleanup_old_files():
    now = time.time()
    for f in Path(DOWNLOAD_DIR).glob("*"):
        if f.is_file() and now - f.stat().st_mtime > 3600:
            f.unlink()
            logger.info(f"🧹 Удалён старый файл: {f.name}")

cleanup_old_files()

def extract_info(url: str) -> dict | None:
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if "entries" in info and info["entries"]:
                # playlist или carousel
                entries = []
                for e in info["entries"]:
                    if not e:
                        continue
                    entries.append({
                        "type": "video" if e.get("ext") in ["mp4", "mov"] else "photo",
                        "title": e.get("title") or "Без названия",
                        "duration": e.get("duration") or 0,
                        "uploader": e.get("uploader") or "Неизвестный",
                        "description": e.get("description") or "",
                        "url": e.get("webpage_url") or url,
                        "ext": e.get("ext") or "mp4",
                        "thumbnail": e.get("thumbnail")
                    })
                return {"type": "carousel" if len(entries) > 1 else "single",
                        "title": info.get("title", ""),
                        "entries": entries,
                        "description": info.get("description", ""),
                        "url": url}
            else:
                # одиночное видео
                return {
                    "type": "single",
                    "title": info.get("title") or "Без названия",
                    "duration": info.get("duration") or 0,
                    "uploader": info.get("uploader") or "Неизвестный",
                    "description": info.get("description") or "",
                    "url": info.get("webpage_url") or url,
                    "ext": info.get("ext") or "mp4",
                    "thumbnail": info.get("thumbnail")
                }
        except Exception as e:
            logger.error(f"Ошибка extract_info: {e}")
            return None

async def download_file(url: str, file_id: str, ext: str) -> str | None:
    file_path = Path(DOWNLOAD_DIR) / f"{file_id}.{ext}"
    if file_path.exists():
        return str(file_path)
    ydl_opts = {"format": "best", "outtmpl": str(file_path), "quiet": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if file_path.exists():
            logger.info(f"✅ Скачан: {file_path}")
            return str(file_path)
        return None
    except Exception as e:
        logger.error(f"Ошибка скачивания {url}: {e}")
        return None

async def upload_to_yadisk(file_path: str) -> str | None:
    client = yadisk.AsyncClient(token=YADISK_TOKEN)
    try:
        await client.mkdir("/bot_uploads", exist_ok=True)
    except:
        pass
    disk_path = f"/bot_uploads/{os.path.basename(file_path)}"
    try:
        await client.upload(file_path, disk_path, overwrite=True)
        await client.publish(disk_path)
        meta = await client.get_meta(disk_path)
        return meta.public_url
    except Exception as e:
        logger.error(f"❌ Яндекс.Диск ошибка: {e}")
        return None
    finally:
        await client.close()

# ----------------------------- MAX SDK -----------------------------
max_bot = MaxBot(token=TOKEN)
dp = Dispatcher()

async def send_via_sdk(chat_id: int, entry: dict):
    """Отправка видео/картинки/файла через SDK с fallback на Яндекс.Диск"""
    ext = entry.get("ext", "mp4")
    file_path = await download_file(entry["url"], re.sub(r"\W+", "", entry["title"][:30]), ext)
    if not file_path:
        return False

    caption = f"🎬 {entry['title']}\n👤 {entry['uploader']}\n⏱ {format_duration(entry.get('duration',0))}\n🔗 {entry['url']}"
    try:
        # Для видео
        if ext in ["mp4", "mov", "avi", "mkv"]:
            upload = await max_bot.get_upload_url("video")
            token = upload.token
            upload_url = upload.url
            # Upload file
            with open(file_path, "rb") as f, aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("data", f, filename=os.path.basename(file_path))
                async with session.post(upload_url, data=form) as resp:
                    if resp.status not in (200, 201):
                        raise Exception("Upload failed")
            # Send message
            await max_bot.send_message(chat_id=chat_id, text=caption,
                attachments=[{"type":"video","payload":{"token":token}}])
        else:
            # Для фото
            upload = await max_bot.get_upload_url("image")
            token = upload.token
            upload_url = upload.url
            with open(file_path,"rb") as f, aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("data", f, filename=os.path.basename(file_path))
                async with session.post(upload_url, data=form) as resp:
                    if resp.status not in (200,201):
                        raise Exception("Upload failed")
            await max_bot.send_message(chat_id=chat_id, text=caption,
                attachments=[{"type":"image","payload":{"token":token}}])
        return True
    except Exception as e:
        logger.warning(f"SDK ошибка, fallback Яндекс.Диск: {e}")
        public_url = await upload_to_yadisk(file_path)
        if public_url:
            await max_bot.send_message(chat_id=chat_id, text=f"🔗 Видео/картинка на Яндекс.Диск: {public_url}")
            return True
    finally:
        Path(file_path).unlink(missing_ok=True)
    return False

# ----------------------------- ОБРАБОТКА URL -----------------------------
async def handle_url(event, url: str):
    chat_id = event.message.chat.id  # <-- правильно
    status_msg = await event.message.answer("🔍 Получаю информацию...")

    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await status_msg.message.edit("❌ Не удалось получить информацию о контенте.")
        return

    entries = info.get("entries") if info.get("type") in ["carousel","playlist"] else [info]
    for idx, entry in enumerate(entries):
        await status_msg.message.edit(f"📥 Загружаю файл {idx+1}/{len(entries)}...")
        await send_via_sdk(chat_id, entry)

    if info.get("description"):
        await event.message.answer(f"📝 Описание:\n\n{info['description'][:4000]}")

    try:
        await status_msg.message.delete()
    except:
        pass

# ----------------------------- СОБЫТИЯ MAX -----------------------------
@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text or ""
    if text == "/start":
        await event.message.answer("👋 Привет! Отправь ссылку на видео/пост.")
        return
    urls = re.findall(r"https?://\S+", text)
    if urls:
        await handle_url(event, urls[0])
    else:
        await event.message.answer("❌ Не удалось найти ссылку.")

# ----------------------------- ЗАПУСК -----------------------------
async def main():
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(max_bot)

if __name__ == "__main__":
    asyncio.run(main())
