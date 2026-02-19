import asyncio
import logging
import os
import sys

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

# ----------------------------- НАСТРОЙКИ -----------------------------
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    print("❌ Ошибка: не задан BOT_TOKEN в переменных окружения")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------- ИНИЦИАЛИЗАЦИЯ БОТА -----------------------------
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ----------------------------- ОБРАБОТЧИКИ -----------------------------
@dp.bot_started()
async def handle_bot_started(event: BotStarted):
    """Логирует запуск бота пользователем (диплинк или первый вход)."""
    logger.info(f"🚀 Бот запущен пользователем. chat_id={event.chat_id}, user_id={event.user.user_id if event.user else 'unknown'}")

@dp.message_created()
async def handle_message(event: MessageCreated):
    """Обрабатывает входящие сообщения."""
    # Получаем информацию о сообщении
    user_id = event.message.sender.user_id if event.message.sender else None
    chat_id = event.message.recipient.chat_id if event.message.recipient else None
    text = event.message.body.text or ''
    msg_id = event.message.body.mid if event.message.body else None

    logger.info("=" * 50)
    logger.info(f"📩 Получено сообщение:")
    logger.info(f"   • user_id: {user_id}")
    logger.info(f"   • chat_id: {chat_id}")
    logger.info(f"   • message_id: {msg_id}")
    logger.info(f"   • текст: {text[:100]}{'...' if len(text)>100 else ''}")

    # Пробуем отправить простое текстовое сообщение в ответ
    try:
        logger.info("🔄 Попытка отправить ответное сообщение...")
        # Используем стандартный метод reply (он отправит в тот же чат)
        await event.message.answer("Привет! Это тестовый ответ от минимального бота.")
        logger.info("✅ Ответное сообщение успешно отправлено!")
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке ответа: {e}", exc_info=True)
        # Дополнительно пытаемся отправить через bot.api напрямую (если reply не сработал)
        try:
            logger.info("🔄 Пробуем отправить через bot.api.sendMessageToChat...")
            await bot.api.sendMessageToChat(chat_id, "Привет! (отправлено через API)")
            logger.info("✅ Сообщение через API отправлено!")
        except Exception as e2:
            logger.error(f"❌ Ошибка при отправке через API: {e2}", exc_info=True)

    logger.info("=" * 50)

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
