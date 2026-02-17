import asyncio
from maxapi import Bot

async def set_webhook():
    # Вставь сюда свой токен (в кавычках)
    bot = Bot("ТВОЙ_ТОКЕН")
    
    # Вставь сюда свой публичный URL (обязательно с https)
    webhook_url = "https://max.ru/id644016399855_bot"
    
    await bot.subscribe_webhook(webhook_url)
    print(f"✅ Вебхук успешно установлен на {webhook_url}")

if __name__ == "__main__":
    asyncio.run(set_webhook())
