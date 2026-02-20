import asyncio
import logging
import os
import re
import aiohttp
import yt_dlp
import yadisk
import time
from pathlib import Path
from urllib.parse import urlparse

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

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            extractor = info.get('extractor', '').lower()

            if info.get('description'):
                logger.info(f"📝 Получено описание: {info['description'][:100]}...")
            else:
                logger.info("📝 Описание отсутствует в ответе yt-dlp")

            if 'instagram' in extractor and 'entries' in info and info['entries']:
                entry = info['entries'][0]
                return {
                    'type': 'single',
                    'title': entry.get('title', 'Без названия'),
                    'duration': entry.get('duration', 0),
                    'uploader': entry.get('uploader', 'Неизвестный автор'),
                    'description': entry.get('description', ''),
                    'webpage_url': entry.get('webpage_url', url),
                    'ext': entry.get('ext', 'mp4'),
                    'thumbnail': entry.get('thumbnail'),
                }

            if 'entries' in info:
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
                    })
                return {
                    'type': 'playlist',
                    'title': info.get('title', 'Пост'),
                    'entries': entries,
                    'webpage_url': url,
                    'description': info.get('description', '')
                }

            return {
                'type': 'single',
                'title': info.get('title', 'Без названия'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Неизвестный автор'),
                'description': info.get('description', ''),
                'webpage_url': info.get('webpage_url', url),
                'ext': info.get('ext', 'mp4'),
                'thumbnail': info.get('thumbnail'),
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
            logger.error("Файл не найден после скачивания")
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ----------------------------- КЛАСС ДЛЯ РАБОТЫ С MAX -----------------------------
class MaxAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://platform-api.max.ru"
        self.headers = {"Authorization": token}

    async def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}/{path.lstrip('/')}"
        if 'json' in kwargs:
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json; charset=utf-8"
        else:
            headers = self.headers

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, **kwargs) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(f"MAX API error {resp.status}: {text}")
                    raise Exception(f"MAX API error: {resp.status}")
                if resp.status == 204:
                    return None
                try:
                    return await resp.json()
                except:
                    text = await resp.text()
                    logger.debug(f"Non-JSON response: {text[:200]}")
                    return text

    async def get_upload_url(self, media_type: str) -> dict:
        """
        Получает URL для загрузки.
        Для video/audio токен приходит сразу.
        Для image/file токен будет получен после загрузки.
        """
        endpoint = f"uploads?type={media_type}"

        data = await self._request("POST", endpoint)

        if not isinstance(data, dict):
            raise Exception(f"❌ Ожидался JSON, получено: {data}")

        if "url" not in data:
            raise Exception(f"❌ В ответе нет поля 'url': {data}")

        logger.info(f"📥 Получен upload URL для {media_type}: {data['url']}")

        # Для video/audio токен приходит сразу
        token = data.get("token")

        if media_type in ("video", "audio"):
            if not token:
                raise Exception(f"❌ Для {media_type} не получен token в ответе /uploads")
            logger.info(f"🔑 Получен token сразу из /uploads: {token[:20]}...")

        return data

    async def upload_file(self, upload_url: str, file_path: str):
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('data', f, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(upload_url, data=form) as resp:
                    if resp.status not in(200, 201):
                        text = await resp.text()
                        logger.error(f"Upload failed: {resp.status} {text}")
                        raise Exception(f"Upload failed: {resp.status}")
                    text = await resp.text()
                    try:
                        return await resp.json()
                    except:
                        # Если ответ не JSON, но статус 200, возможно, это успех, но токена нет – падаем
                        if "<retval>1</retval>" in text:
                            logger.info ("✅ Upload успешен (retval=1)")
                            return {"status": "ok"}
                        logger.error(f"❌ Неожиданный ответ upload: {text}")
                        raise Exeption("Upload response is not valid")
                    if 'token' not in result:
                        raise Exception("No token in upload response")
                    logger.info(f"🔑 Получен токен: {result['token'][:20]}...")
                    return result['token']

    async def send_media(self, chat_id: int, caption: str, file_path: str):
        # Шаг 1: получаем URL для загрузки
        upload_data = await self.get_upload_url('video')
        upload_url = upload_data["url"]
        # Шаг 2: загружаем файл и получаем токен
        token = upload_data["token"]
        # Шаг 3: отправляем сообщение с повторными попытками
        await self.upload_file(upload_url, file_path)                    
        attachment = {"type": "video", "payload": {"token": token}}
        delays = [2, 5, 10, 20, 30]  # интервалы в секундах
        for attempt, delay in enumerate(delays, 1):
            try:
                logger.info(f"📤 Попытка {attempt} отправки сообщения...")
                await self.send_message(chat_id, caption, [attachment])
                logger.info(f"✅ Сообщение отправлено с {attempt}-й попытки")
                return
            except Exception as e:
                if "attachment.not.ready" in str(e):
                    logger.warning(f"⏳Видео еще обрабатывается.Ждеем {delay} сек...")
                    await asyncio.sleep(delay)
                else:    
                    raise
                
                

    async def send_message(self, user_id: int, text: str, attachments: list = None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "attachments": attachments or []
        }
        logger.info(f"📤 Отправка сообщения: {payload}")
        return await self._request('POST', 'messages', json=payload)

# ----------------------------- FALLBACK НА ЯНДЕКС.ДИСК -----------------------------
async def upload_to_yadisk(file_path: str) -> str | None:
    logger.info(f"📤 Яндекс.Диск: начало загрузки {file_path}")
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
        public_url = meta.public_url
        logger.info(f"✅ Файл загружен на Яндекс.Диск: {public_url}")
        return public_url
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки на Яндекс.Диск: {e}", exc_info=True)
        return None
    finally:
        await client.close()

# ----------------------------- ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ ССЫЛОК -----------------------------
async def handle_url(event, url: str):
    chat_id = event.message.recipient.chat_id
    status_msg = await event.message.answer("🔍 Получаю информацию...")

    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await status_msg.message.edit("❌ Не удалось получить информацию о контенте. Проверьте ссылку.")
        return

    await status_msg.message.edit("📥 Начинаю загрузку...")
    max_api = MaxAPI(TOKEN)

    async def send_single_file(file_path: str, entry_info: dict, file_index: int = None, total_files: int = None):
        # Для file тип медиа не нужен, используем универсальную отправку
        if file_index is not None and total_files is not None:
            caption = (f"📦 Файл {file_index}/{total_files}\n"
                       f"🎬 {entry_info['title']}\n"
                       f"👤 {entry_info['uploader']}\n"
                       f"⏱ {format_duration(entry_info['duration'])}\n"
                       f"🔗 {entry_info['webpage_url']}")
        else:
            caption = (f"🎬 {entry_info['title']}\n"
                       f"👤 {entry_info['uploader']}\n"
                       f"⏱ {format_duration(entry_info['duration'])}\n"
                       f"🔗 {entry_info['webpage_url']}")

        user_id = event.message.sender.user_id

        try:
            await max_api.send_media(user_id, caption, file_path)
            logger.info("✅ Медиа отправлено через MAX")
            return True, None
        except Exception as e:
            logger.error(f"Ошибка отправки через MAX: {e}")
            # Пробуем отправить только текст (диагностика)
            try:
                await max_api.send_message(user_id, caption)
                logger.info("✅ Текст отправлен, проблема во вложении")
            except Exception as e2:
                logger.error(f"Даже текст не ушёл: {e2}")
            # Fallback на Яндекс.Диск
            yadisk_url = await upload_to_yadisk(file_path)
            if yadisk_url:
                await event.message.answer(
                    f"⚠️ Файл{' ' + str(file_index) if file_index else ''} временно недоступен в MAX, но доступен по ссылке:\n"
                    f"🔗 [Скачать]({yadisk_url})"
                )
                return True, yadisk_url
            else:
                await event.message.answer(f"❌ Не удалось отправить файл{' ' + str(file_index) if file_index else ''}.")
                return False, None

    if info['type'] == 'single':
        ext = info.get('ext', 'mp4')
        safe_title = re.sub(r'\W+', '', info['title'][:30])
        file_path = await download_file(info['webpage_url'], safe_title, ext)
        if not file_path:
            await status_msg.message.edit("❌ Не удалось скачать файл.")
            return

        success, _ = await send_single_file(file_path, info)
        Path(file_path).unlink(missing_ok=True)

        if success:
            if info.get('description'):
                logger.info(f"📤 Отправка описания, длина {len(info['description'])}")
                await event.message.answer(f"📝 Описание:\n\n{info['description'][:4000]}")
                logger.info("✅ Описание отправлено")
            else:
                logger.info("📝 Описание отсутствует в info")
            await event.message.answer(
                "❤️ Если вам понравился бот, поддержите проект:\n"
                "💸 [Ссылка на донат](https://donate.example.com)\n"
                "Спасибо!"
            )
            logger.info("✅ Сообщение о донате отправлено")
        else:
            logger.info("❌ success=False, описание и донат не отправлены")

    else:  # playlist
        await status_msg.message.edit(f"📦 Найдено {len(info['entries'])} файлов. Загружаю...")
        tasks = []
        for idx, entry in enumerate(info['entries']):
            safe_title = re.sub(r'\W+', '', entry['title'][:20])
            file_id = f"{safe_title}_{idx}"
            ext = entry.get('ext', 'mp4')
            tasks.append(download_file(entry['webpage_url'], file_id, ext))

        file_paths = await asyncio.gather(*tasks)
        successful_paths = [p for p in file_paths if p]

        if not successful_paths:
            await status_msg.message.edit("❌ Не удалось скачать ни одного файла.")
            return

        await status_msg.message.edit(f"✅ Скачано {len(successful_paths)} файлов. Отправляю...")

        any_success = False
        for idx, file_path in enumerate(successful_paths):
            entry = info['entries'][idx]
            success, _ = await send_single_file(file_path, entry, idx+1, len(successful_paths))
            if success:
                any_success = True
            Path(file_path).unlink(missing_ok=True)

        if any_success:
            if info.get('description'):
                logger.info(f"📤 Отправка описания поста, длина {len(info['description'])}")
                await event.message.answer(f"📝 Описание поста:\n\n{info['description'][:4000]}")
                logger.info("✅ Описание поста отправлено")
            else:
                logger.info("📝 Описание поста отсутствует")
            await event.message.answer(
                "❤️ Если вам понравился бот, поддержите проект:\n"
                "💸 [Ссылка на донат](https://donate.example.com)\n"
                "Спасибо!"
            )
            logger.info("✅ Сообщение о донате отправлено")
        else:
            logger.info("❌ no files sent, описание и донат не отправлены")
            await event.message.answer("❌ Не удалось отправить ни одного файла. Сервис временно недоступен.")

    try:
        await status_msg.message.delete()
    except:
        pass

# ----------------------------- ИНИЦИАЛИЗАЦИЯ БОТА MAX -----------------------------
max_bot = MaxBot(token=TOKEN)
dp = Dispatcher()

# ----------------------------- ОБРАБОТЧИКИ СОБЫТИЙ -----------------------------
@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text or ''
    if text == '/start':
        await event.message.answer(
            "👋 Привет! Я бот для скачивания видео из YouTube, Instagram и других соцсетей.\n"
            "Просто отправь мне ссылку."
        )
        return

    if 'http://' in text or 'https://' in text:
        urls = re.findall(r'https?://\S+', text)
        if urls:
            await handle_url(event, urls[0])
        else:
            await event.message.answer("❌ Не удалось найти ссылку.")
    else:
        await event.message.answer("Отправь мне ссылку на видео или пост.")

@dp.bot_started()
async def handle_bot_started(event: BotStarted):
    await max_bot.api.send_message(
        chat_id=event.chat_id,
        text="👋 Привет! Я бот для скачивания видео. Отправь мне ссылку."
    )

# ----------------------------- ЗАПУСК -----------------------------
async def main():
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(max_bot)

if __name__ == '__main__':
    asyncio.run(main())
