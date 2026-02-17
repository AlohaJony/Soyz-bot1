import asyncio
import logging
import os
import re
import aiohttp
import yt_dlp
from pathlib import Path
from urllib.parse import urlparse

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("Не задан BOT_TOKEN в переменных окружения")

# Папка для скачанных файлов
DOWNLOAD_DIR = 'downloads'
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def format_duration(seconds: float) -> str:
    """Форматирует длительность в ЧЧ:ММ:СС или ММ:СС, принимает float."""
    total_seconds = int(seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def extract_info(url: str) -> dict | None:
    """
    Извлекает информацию о контенте через yt-dlp.
    Возвращает словарь с ключами:
        - type: 'single' или 'playlist'
        - title: общий заголовок
        - entries: список записей (для playlist)
        - для single: duration, uploader, description, webpage_url, ext, thumbnail
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:  # Это плейлист (пост с несколькими медиа)
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
                    'webpage_url': url
                }
            else:  # Одиночное видео/изображение
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
        'format': 'best[ext=mp4]/best' if ext in ['mp4', 'mov', 'avi'] else 'best',
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
            else:
                logger.error("Файл не найден после скачивания")
                return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ----------------------------- РАБОТА С API MAX -----------------------------
class MaxAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://platform-api.max.ru/v1"
        self.headers = {"Authorization": f"Bearer {token}"}

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

    async def get_upload_url(self, media_type: str) -> str:
        """Запрашивает URL для загрузки файла. media_type: 'image' или 'video'"""
        data = await self._request('POST', f'uploads?type={media_type}')
        return data['url']

    async def upload_file(self, upload_url: str, file_path: str) -> str:
        """Загружает файл на полученный URL, возвращает токен файла."""
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
                    return result['token']

    async def send_message(self, chat_id: int, text: str, attachments: list = None):
        """Отправляет сообщение в чат."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "attachments": attachments or []
        }
        return await self._request('POST', 'messages', json=payload)

    async def send_media(self, chat_id: int, caption: str, file_path: str, media_type: str):
        """Загружает файл и отправляет его как медиа."""
        # 1. Получаем URL для загрузки
        upload_url = await self.get_upload_url(media_type)
        # 2. Загружаем файл
        token = await self.upload_file(upload_url, file_path)
        # 3. Формируем вложение
        attachment = {
            "type": media_type,
            "payload": {"token": token}
        }
        # 4. Отправляем сообщение с вложением
        return await self.send_message(chat_id, caption, [attachment])

# ----------------------------- ОБРАБОТЧИК СООБЩЕНИЙ -----------------------------
async def handle_url(event, url: str):
    """Основная логика обработки ссылки."""
    chat_id = event.message.recipient.chat_id
    status_msg = await event.message.answer("🔍 Получаю информацию...")

    # Получаем информацию о контенте
    info = await asyncio.to_thread(extract_info, url)
    if not info:
        await status_msg.message.edit("❌ Не удалось получить информацию о контенте. Проверьте ссылку.")
        return

    await status_msg.message.edit("📥 Начинаю загрузку...")

    max_api = MaxAPI(TOKEN)

    if info['type'] == 'single':
        # Одиночное видео/изображение
        ext = info.get('ext', 'mp4')
        file_id = re.sub(r'\W+', '', info['title'][:30])  # простой идентификатор
        file_path = await download_file(info['webpage_url'], file_id, ext)
        if not file_path:
            await status_msg.message.edit("❌ Не удалось скачать файл.")
            return

        # Определяем тип медиа
        media_type = 'video' if ext in ['mp4', 'mov', 'avi', 'mkv'] else 'image'
        caption = (f"🎬 {info['title']}\n"
                   f"👤 {info['uploader']}\n"
                   f"⏱ {format_duration(info['duration'])}\n"
                   f"🔗 {info['webpage_url']}")

        # Отправляем через MAX API
        try:
            await max_api.send_media(chat_id, caption, file_path, media_type)
            logger.info("✅ Медиа отправлено")
        except Exception as e:
            logger.error(f"Ошибка отправки через MAX: {e}")
            # Fallback: пытаемся отправить как файл (документ)
            try:
                upload_url = await max_api.get_upload_url('file')
                token = await max_api.upload_file(upload_url, file_path)
                attachment = {"type": "file", "payload": {"token": token}}
                await max_api.send_message(chat_id, caption, [attachment])
                logger.info("✅ Файл отправлен как документ")
            except Exception as e2:
                logger.error(f"Fallback тоже не сработал: {e2}")
                await status_msg.message.edit("❌ Не удалось отправить файл.")
                Path(file_path).unlink(missing_ok=True)
                return

        # Удаляем временный файл
        Path(file_path).unlink(missing_ok=True)

        # Отправляем описание, если есть
        if info.get('description'):
            desc = info['description'][:4000]
            await event.message.answer(f"📝 Описание:\n\n{desc}")

    else:  # playlist
        # Пост с несколькими медиа
        await status_msg.message.edit(f"📦 Найдено {len(info['entries'])} файлов. Начинаю загрузку...")
        tasks = []
        for idx, entry in enumerate(info['entries']):
            file_id = f"{re.sub(r'\W+', '', entry['title'][:20])}_{idx}"
            ext = entry.get('ext', 'mp4')
            tasks.append(download_file(entry['webpage_url'], file_id, ext))

        file_paths = await asyncio.gather(*tasks)
        successful_paths = [p for p in file_paths if p]

        if not successful_paths:
            await status_msg.message.edit("❌ Не удалось скачать ни одного файла.")
            return

        await status_msg.message.edit(f"✅ Скачано {len(successful_paths)} файлов. Отправляю...")

        # Отправляем каждый файл отдельным сообщением
        for idx, file_path in enumerate(successful_paths):
            ext = Path(file_path).suffix.lstrip('.')
            media_type = 'video' if ext in ['mp4', 'mov', 'avi', 'mkv'] else 'image'
            entry = info['entries'][idx]
            caption = (f"📦 Файл {idx+1}/{len(successful_paths)}\n"
                       f"🎬 {entry['title']}\n"
                       f"👤 {entry['uploader']}\n"
                       f"⏱ {format_duration(entry['duration'])}\n"
                       f"🔗 {entry['webpage_url']}")
            try:
                await max_api.send_media(chat_id, caption, file_path, media_type)
            except Exception as e:
                logger.error(f"Ошибка отправки {file_path}: {e}")
                # Пробуем как документ
                try:
                    upload_url = await max_api.get_upload_url('file')
                    token = await max_api.upload_file(upload_url, file_path)
                    attachment = {"type": "file", "payload": {"token": token}}
                    await max_api.send_message(chat_id, f"{caption}\n\n(отправлено как файл)", [attachment])
                except:
                    pass
            finally:
                Path(file_path).unlink(missing_ok=True)

        # Отправляем общее описание поста (если есть)
        if info.get('description'):
            await event.message.answer(f"📝 Описание поста:\n\n{info['description'][:4000]}")

    # Удаляем статусное сообщение
    await status_msg.message.delete()

    # Сообщение о поддержке (донат)
    await event.message.answer(
        "❤️ Если вам понравился бот, поддержите проект:\n"
        "💸 [Ссылка на донат](https://donate.example.com)\n"
        "Спасибо!"
    )

# ----------------------------- ЗАПУСК БОТА -----------------------------
# Здесь должен быть код инициализации вашего бота (вебхуки или long polling)
# Например, используя aiohttp для приёма вебхуков или библиотеку для MAX.
# Поскольку у нас нет готовой библиотеки, я покажу пример с aiohttp сервером,
# который будет принимать вебхуки от MAX.

from aiohttp import web
import json

async def webhook(request):
    try:
        data = await request.json()
        logger.info(f"Получено обновление: {data}")

        # Предполагаем, что событие message_created
        if data.get('type') == 'message_created':
            event = data['payload']
            # Здесь event.message — объект сообщения
            # Нужно адаптировать под реальную структуру от MAX
            # Временно используем Mock-объект
            class MockEvent:
                def __init__(self, msg):
                    self.message = msg
            mock_event = MockEvent(event['message'])
            text = event['message']['body']['text']
            if text and ('http://' in text or 'https://' in text):
                # Извлекаем первую ссылку
                urls = re.findall(r'https?://\S+', text)
                if urls:
                    await handle_url(mock_event, urls[0])
            else:
                # Ответ на команды /start и т.д.
                if text == '/start':
                    # Отправить приветствие через API напрямую
                    max_api = MaxAPI(TOKEN)
                    await max_api.send_message(
                        event['message']['recipient']['chat_id'],
                        "👋 Привет! Я бот для скачивания видео из YouTube, Instagram и других соцсетей.\n"
                        "Просто отправь мне ссылку на пост или видео."
                    )
                else:
                    max_api = MaxAPI(TOKEN)
                    await max_api.send_message(
                        event['message']['recipient']['chat_id'],
                        "Отправь мне ссылку на видео или пост."
                    )
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}", exc_info=True)
        return web.Response(status=500)

app = web.Application()
app.router.add_post('/webhook', webhook)

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
