import asyncio
import logging
import os
import re
import sys
import aiohttp
import yt_dlp
from pathlib import Path
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ----------------------------- КЛАСС ДЛЯ РАБОТЫ С MAX (ИСПРАВЛЕННЫЙ) -----------------------------
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

    async def get_upload_info(self, media_type: str) -> dict:
        endpoint = f"uploads?type={media_type}"
        data = await self._request('POST', endpoint)
        if isinstance(data, str):
            raise Exception(f"Expected JSON, got: {data}")
        logger.info(f"📥 Получен URL для загрузки {media_type}: {data.get('url')}")
        return data

    async def upload_file(self, upload_url: str, file_path: str, media_type: str):
        with open(file_path, 'rb') as f:
            form = aiohttp.FormData()
            form.add_field('data', f, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(upload_url, data=form) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(f"Upload failed: {resp.status} {text}")
                        raise Exception(f"Upload failed: {resp.status}")
                    if media_type == 'video':
                        # Для видео просто проверяем статус, токен уже есть с первого шага
                        logger.debug("Video uploaded successfully, ignoring response body")
                        return None
                    else:
                        # Для других типов ожидаем JSON с токеном
                        result = await resp.json()
                        if 'token' not in result:
                            raise Exception("No token in upload response")
                        return result['token']

    async def send_media(self, user_id: int, caption: str, file_path: str):
        logger.info("📤 Этап 1: получение URL для загрузки...")
        upload_info = await self.get_upload_info('video')
        upload_url = upload_info['url']
        token_from_step1 = upload_info.get('token')  # токен с первого шага
        logger.info(f"🔑 Получен токен с первого шага: {token_from_step1[:20]}...")
        logger.info("📤 Этап 2: загрузка файла...")
        await self.upload_file(upload_url, file_path, 'video')
        logger.info("📤 Этап 3: пауза 2 секунды...")
        await asyncio.sleep(2)
        logger.info("📤 Этап 4: отправка сообщения с вложением...")
        attachment = {"type": "video", "payload": {"token": token_from_step1}}
        return await self.send_message(user_id, caption, [attachment])

    async def send_message(self, user_id: int, text: str, attachments: list = None):
        payload = {
            "user_id": user_id,
            "body": {
                "text": text,
                "attachments": attachments or []
            }
        }
        logger.info(f"📤 Отправка сообщения: {payload}")
        return await self._request('POST', 'messages', json=payload)

# ----------------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------
def extract_info(url: str) -> dict | None:
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
                'ext': info.get('ext', 'mp4'),
                'duration': info.get('duration', 0),
                'description': info.get('description', '')
            }
        except Exception as e:
            logger.error(f"Ошибка получения информации: {e}")
            return None

def download_file(url: str, file_id: str, ext: str) -> Path | None:
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
    logger.info(f"🚀 Бот запущен пользователем. chat_id={event.chat_id}")

@dp.message_created()
async def handle_message(event: MessageCreated):
    user_id = event.message.sender.user_id
    chat_id = event.message.recipient.chat_id
    text = event.message.body.text or ''
    msg_id = event.message.body.mid

    logger.info("=" * 60)
    logger.info(f"📩 Сообщение от user_id={user_id}, chat_id={chat_id}, msg_id={msg_id}")
    logger.info(f"Текст: {text[:200]}")
    logger.info("=" * 60)

    # Проверяем наличие ссылки
    url_match = re.search(r'https?://\S+', text)
    if not url_match:
        await event.message.answer("Отправь ссылку на видео.")
        return

    url = url_match.group()
    logger.info(f"🔗 Ссылка: {url}")

    # Получаем информацию
    info = extract_info(url)
    if not info:
        await event.message.answer("❌ Не удалось получить информацию о видео.")
        return

    # Скачиваем
    safe_title = re.sub(r'\W+', '', info['title'][:30])
    file_path = download_file(url, safe_title, info['ext'])
    if not file_path:
        await event.message.answer("❌ Не удалось скачать видео.")
        return

    # Пробуем отправить через MAX
    caption = f"🎬 {info['title']}\n📏 {info['duration']} сек"
    max_api = MaxAPI(TOKEN)

    try:
        await max_api.send_media(user_id, caption, str(file_path))
        logger.info("✅ Видео успешно отправлено через MAX")
        await event.message.answer("✅ Видео отправлено!")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки через MAX: {e}", exc_info=True)
        await event.message.answer("❌ Не удалось отправить видео через MAX.")
    finally:
        file_path.unlink(missing_ok=True)
        logger.info("🧹 Временный файл удалён")

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
