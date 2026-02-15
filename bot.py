import asyncio
import logging
import os
import re
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated
import yt_dlp

logging.basicConfig(level=logging.INFO)

# ⚠️ ЗАМЕНИ НА СВОЙ ТОКЕН
TOKEN = 'f9LHodD0cOLf0djnt_CytNRNv4OEObKOHreExo9jzZUd0ViE28Cwl4ZNMcCQHSGq7ykNJpBzmxznKghd79gV'

bot = Bot(token=TOKEN)
dp = Dispatcher()

async def download_video(url: str) -> str | None:
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }
    try:
        os.makedirs('downloads', exist_ok=True)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
            for f in os.listdir('downloads'):
                if f.startswith(info['id']):
                    return os.path.join('downloads', f)
            return None
    except Exception as e:
        logging.error(f"Ошибка скачивания {url}: {e}")
        return None

@dp.message_created()
async def handle_message(event: MessageCreated):
    if not event.message.body.text:
        await event.message.answer("Пожалуйста, отправь текстовую ссылку на видео.")
        return

    text = event.message.body.text.lower().strip()

    if text in ['/start', '/help']:
        await event.message.answer(
            "👋 Привет! Я умею скачивать видео из YouTube, Instagram и других сайтов.\n"
            "Просто отправь мне ссылку, и я пришлю видео файлом."
        )
        return

    if 'http://' in text or 'https://' in text:
        urls = re.findall(r'https?://\S+', event.message.body.text)
        if not urls:
            await event.message.answer("Не удалось найти ссылку в сообщении.")
            return
        url = urls[0]

        status = await event.message.answer("⏳ Скачиваю видео, подожди...")

        file_path = await download_video(url)
        if file_path and os.path.exists(file_path):
            await event.message.answer_with_file(
                file_path=file_path,
                caption="🎉 Готово! Лови видео."
            )
            await bot.delete_message(status.chat_id, status.message_id)
            os.remove(file_path)
        else:
            await bot.edit_message(
                status.chat_id,
                status.message_id,
                "❌ Не удалось скачать видео. Проверь ссылку или попробуй позже."
            )
    else:
        await event.message.answer("Отправь мне ссылку на видео, например: https://youtu.be/...")

async def main():
    print("🚀 Бот запущен и слушает...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
