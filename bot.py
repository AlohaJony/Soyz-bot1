import asyncio
import logging
import os
import re
import aiohttp
import yt_dlp
import yadisk
from pathlib import Path
from urllib.parse import urlparse

# Импорты из официальной библиотеки MAX для получения обновлений
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

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def format_duration(seconds: float) -> str:
    """Форматирует длительность в ЧЧ:ММ:СС или ММ:СС."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def extract_info(url: str) -> dict | None:
    """
    Извлекает информацию о контенте через yt-dlp.
    Возвращает словарь:
        - type: 'single' или 'playlist'
        - title, entries, duration, uploader, description, webpage_url, ext, thumbnail
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:  # плейлист (пост с несколькими медиа)
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
                    'description': info.get('description', '')  # описание всего поста
                }
            else:  # одиночное видео/изображение
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
    """
    Скачивает файл по URL, сохраняет в DOWNLOAD_DIR/{file_id}.{ext}
    Возвращает путь к файлу или None.
    """
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

# ----------------------------- РАБОТА С API MAX -----------------------------
class MaxAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://platform-api.max.ru"
        self.headers = {"Authorization": token}  # Без "Bearer"

    async def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=self.headers, **kwargs) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(f"MAX API error {resp.status}: {text}")
                    raise Exception(f"MAX API error: {resp.status}")
                if resp.status == 204:
                    return None
                return await resp.json()

    async def get_upload_info(self, media_type: str) -> dict:
        """
        Запрашивает информацию для загрузки файла.
        Возвращает словарь с ключами 'url' и, для video/audio, 'token'.
        """
        endpoint = f"uploads?type={media_type}"
        return await self._request('POST', endpoint)

    async def send_media(self, chat_id: int, caption: str, file_path: str, media_type: str):
        """
        Загружает файл и отправляет его как медиа.
        Поддерживает типы: video, audio, image, file.
        """
        # 1. Получаем URL и (для видео/аудио) токен
        upload_info = await self.get_upload_info(media_type)
        upload_url = upload_info['url']
        video_token = upload_info.get('token')  # None для image/file

        # 2. Загружаем файл
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('data', f, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(upload_url, data=form) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"Upload failed: {resp.status} {text}")
                        raise Exception(f"Upload failed: {resp.status}")
                    result = await resp.json()
                    token = video_token or result['token']

        # 3. Пауза для обработки (рекомендация из документации)
        await asyncio.sleep(2)

        # 4. Отправляем сообщение с вложением
        attachment = {"type": media_type, "payload": {"token": token}}
        return await self.send_message(chat_id, caption, [attachment])

    async def send_message(self, chat_id: int, text: str, attachments: list = None):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "attachments": attachments or []
        }
        return await self._request('POST', 'messages', json=payload)

# ----------------------------- FALLBACK НА ЯНДЕКС.ДИСК -----------------------------
async def upload_to_yadisk(file_path: str) -> str | None:
    """
    Загружает файл на Яндекс.Диск и возвращает прямую ссылку на скачивание.
    """
    logger.info(f"📤 Яндекс.Диск: начало загрузки {file_path}")

    # Создаём асинхронного клиента
    client = yadisk.AsyncClient(token=YADISK_TOKEN)

    try:
        # Загружаем файл в корень диска
        # Важно: путь на диске должен быть уникальным, чтобы не перезаписать существующий файл
        disk_path = f"/bot_uploads/{os.path.basename(file_path)}"
        await client.upload(file_path, disk_path, overwrite=True)

        # Делаем файл публичным и получаем ссылку
        await client.publish(disk_path)
        public_url = await client.get_public_link(disk_path)

        logger.info(f"✅ Файл загружен на Яндекс.Диск: {public_url}")
        return public_url

    except Exception as e:
        logger.error(f"❌ Ошибка загрузки на Яндекс.Диск: {e}", exc_info=True)
        return None
    finally:
        # Важно: закрываем сессию
        await client.close()

# ----------------------------- ОСНОВНАЯ ЛОГИКА ОБРАБОТКИ ССЫЛОК -----------------------------
async def handle_url(event, url: str):
    """Обрабатывает ссылку: скачивает контент и отправляет через MAX, при ошибке через Яндекс.Диск."""
    chat_id = event.message.recipient.chat_id
    status_msg = await event.message.answer("🔍 Получаю информацию...")

    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await status_msg.message.edit("❌ Не удалось получить информацию о контенте. Проверьте ссылку.")
        return

    await status_msg.message.edit("📥 Начинаю загрузку...")

    max_api = MaxAPI(TOKEN)

    # Функция для отправки одного файла (используется и для single, и для playlist)
    async def send_single_file(file_path: str, entry_info: dict, file_index: int = None, total_files: int = None):
        ext = Path(file_path).suffix.lstrip('.')
        media_type = 'video' if ext in ('mp4', 'mov', 'avi', 'mkv') else 'image'

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

        try:
            # Пытаемся отправить через MAX
            await max_api.send_media(chat_id, caption, file_path, media_type)
            logger.info(f"✅ Медиа отправлено через MAX")
            return True, None  # Успех, ссылка не нужна
        except Exception as e:
            logger.error(f"Ошибка отправки через MAX: {e}")
            # Fallback на Яндекс.Диск
            yadisk_url = await upload_to_yadisk(file_path)
            if yadisk_url:
                await event.message.answer(
                    f"⚠️ Файл{' ' + str(file_index) if file_index else ''} временно недоступен в MAX, но доступен по ссылке:\n"
                    f"🔗 [Скачать]({yadisk_url})"
                )
                logger.info(f"✅ Сообщение со ссылкой Яндекс.Диск для файла {file_index if file_index else ''} отправлено")
                return True, yadisk_url  # Успех через fallback
            else:
                await event.message.answer(f"❌ Не удалось отправить файл{' ' + str(file_index) if file_index else ''}.")
                return False, None

    if info['type'] == 'single':
        # Одиночный файл
        ext = info.get('ext', 'mp4')
        safe_title = re.sub(r'\W+', '', info['title'][:30])
        file_path = await download_file(info['webpage_url'], safe_title, ext)
        if not file_path:
            await status_msg.message.edit("❌ Не удалось скачать файл.")
            return

        success, _ = await send_single_file(file_path, info)
        Path(file_path).unlink(missing_ok=True)

        # Отправляем описание, если есть
        if success and info.get('description'):
            desc = info['description'][:4000]
            await event.message.answer(f"📝 Описание:\n\n{desc}")

        if success:
            # Сообщение о поддержке
            await event.message.answer(
                "❤️ Если вам понравился бот, поддержите проект:\n"
                "💸 [Ссылка на донат](https://donate.example.com)\n"
                "Спасибо!"
            )

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
            # Сообщение о поддержке
            await event.message.answer(
                "❤️ Если вам понравился бот, поддержите проект:\n"
                "💸 [Ссылка на донат](https://donate.example.com)\n"
                "Спасибо!"
            )
        else:
            await event.message.answer("❌ Не удалось отправить ни одного файла. Сервис временно недоступен.")

        # Отправляем описание поста, если есть
        if info.get('description'):
            await event.message.answer(f"📝 Описание поста:\n\n{info['description'][:4000]}")

    # Удаляем статусное сообщение
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
            "Просто отправь мне ссылку на пост или видео."
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
        text="👋 Привет! Я бот для скачивания видео. Просто отправь мне ссылку."
    )

# ----------------------------- ЗАПУСК -----------------------------
async def main():
    logger.info("Бот запущен и слушает...")
    await dp.start_polling(max_bot)

if __name__ == '__main__':
    asyncio.run(main())
