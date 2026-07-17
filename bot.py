import os
import logging
import asyncio
import random
import qrcode
from io import BytesIO
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.types import Message, CallbackQuery, BufferedInputFile
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        SessionPasswordNeededError, 
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        FloodWaitError,
        PhoneCodeExpiredError
    )
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    exit(1)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

class SessionStates(StatesGroup):
    METHOD = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class WorkingSessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.user_messages = {}  # Для хранения сообщений пользователей
    
    async def create_qr_session(self, user_id: int, message: Message):
        """Создание QR-сессии и немедленный старт отслеживания"""
        try:
            # Закрываем старую сессию если есть
            if user_id in self.active_sessions:
                try:
                    await self.active_sessions[user_id]['client'].disconnect()
                except:
                    pass
            
            devices = [
                {
                    "device_model": "Samsung SM-G991B",
                    "system_version": "Android 13",
                    "app_version": "10.0.0",
                },
                {
                    "device_model": "iPhone15,3", 
                    "system_version": "iOS 17.1.2",
                    "app_version": "10.0.0",
                }
            ]
            
            device = random.choice(devices)
            
            client = TelegramClient(StringSession(), API_ID, API_HASH, **device)
            await client.connect()
            
            # Создаем QR-логин
            qr_login = await client.qr_login()
            
            self.active_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'created_at': datetime.now(),
                'message': message  # Сохраняем сообщение для ответа
            }
            
            # Сохраняем ID сообщения для обновления
            self.user_messages[user_id] = message
            
            return True, qr_login.url
            
        except Exception as e:
            logger.error(f"QR creation error: {e}")
            return False, f"❌ Ошибка создания QR: {str(e)}"
    
    async def start_qr_monitoring(self, user_id: int):
        """Запуск мониторинга статуса QR-авторизации"""
        if user_id not in self.active_sessions:
            return
        
        data = self.active_sessions[user_id]
        message = data['message']
        
        try:
            # Отправляем сообщение о начале ожидания
            status_msg = await message.answer("⏳ Ожидаем сканирование QR-кода...")
            
            # Ждем сканирования с таймаутом 120 секунд
            logger.info(f"🔄 Начало ожидания QR для пользователя {user_id}")
            
            # Ждем завершения QR-логина
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            logger.info(f"✅ QR код отсканирован для пользователя {user_id}")
            
            # Обновляем статус
            await status_msg.edit_text("✅ QR-код отсканирован! Проверяем авторизацию...")
            
            # Даем время на подтверждение в приложении
            await asyncio.sleep(3)
            
            # ПРОВЕРЯЕМ АВТОРИЗАЦИЮ
            is_authorized = await data['client'].is_user_authorized()
            logger.info(f"🔐 Статус авторизации для {user_id}: {is_authorized}")
            
            if not is_authorized:
                await status_msg.edit_text("❌ Авторизация не завершена. Подтвердите вход в Telegram.")
                return
            
            # ✅ АВТОРИЗАЦИЯ УСПЕШНА - СОЗДАЕМ СЕССИЮ
            await status_msg.edit_text("✅ Авторизация успешна! Создаем сессию...")
            
            # Получаем строку сессии
            session_string = data['client'].session.save()
            logger.info(f"📦 Сессия создана для {user_id}")
            
            # Создаем файл сессии
            session_bytes = session_string.encode('utf-8')
            session_file = BufferedInputFile(session_bytes, filename="telegram_session.txt")
            
            # ✅ ОТПРАВЛЯЕМ СЕССИЮ ПОЛЬЗОВАТЕЛЮ
            await message.answer_document(
                document=session_file,
                caption="✅ **Сессия успешно создана!**\n\n"
                       "💾 Сохраните этот файл\n"
                       "🔒 Он дает полный доступ к аккаунту"
            )
            
            # Также отправляем текстовую версию
            await message.answer(f"📋 **Session String:**\n```\n{session_string}\n```")
            
            logger.info(f"🎉 Сессия отправлена пользователю {user_id}")
            
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут QR для пользователя {user_id}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer("❌ Время ожидания истекло. QR-код не был отсканирован.")
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга QR для {user_id}: {e}")
            if user_id in self.user_messages:
                await self.user_messages[user_id].answer(f"❌ Ошибка: {str(e)}")
        finally:
            # Всегда очищаем сессию
            await self.cleanup_session(user_id)
    
    async def cleanup_session(self, user_id: int):
        """Очистка сессии"""
        if user_id in self.active_sessions:
            try:
                await self.active_sessions[user_id]['client'].disconnect()
            except:
                pass
            del self.active_sessions[user_id]
        
        if user_id in self.user_messages:
            del self.user_messages[user_id]

manager = WorkingSessionManager()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 Создать сессию через QR-код", callback_data="method_qr")
    builder.adjust(1)
    
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "Создайте сессию для вашего аккаунта через QR-код.\n"
        "После сканирования **сессия придет автоматически**.",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data == "method_qr")
async def handle_qr_method(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    await callback.answer()
    await callback.message.edit_text("🔄 Создаем QR-код...")
    
    # Создаем QR-сессию и начинаем отслеживание
    success, qr_url = await manager.create_qr_session(user_id, callback.message)
    
    if success:
        # Создаем QR-код изображение
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        
        qr_file = BufferedInputFile(bio.getvalue(), filename="qr_code.png")
        
        # Отправляем QR-код
        await callback.message.answer_photo(
            photo=qr_file,
            caption="📷 **QR-код для подключения:**\n\n"
                   "1. Откройте Telegram → Настройки\n"
                   "2. Устройства → Подключить устройство\n"
                   "3. Отсканируйте этот QR-код\n"
                   "4. **Подтвердите вход** в приложении\n\n"
                   "⏳ Ожидаем 2 минуты...\n"
                   "✅ Сессия придет автоматически после подключения"
        )
        
        # ✅ НЕМЕДЛЕННО ЗАПУСКАЕМ МОНИТОРИНГ
        asyncio.create_task(manager.start_qr_monitoring(user_id))
        
    else:
        await callback.message.edit_text(f"❌ {qr_url}")

@router.message(Command("check"))
async def cmd_check(message: Message):
    """Проверка статуса сессии"""
    user_id = message.from_user.id
    if user_id in manager.active_sessions:
        created_time = manager.active_sessions[user_id]['created_at']
        time_passed = datetime.now() - created_time
        await message.answer(f"🔄 Сессия активна\n⏰ Прошло: {int(time_passed.total_seconds())} сек")
    else:
        await message.answer("❌ Нет активной сессии\n🔄 Используйте /start")

@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Отладочная информация"""
    user_id = message.from_user.id
    if user_id in manager.active_sessions:
        data = manager.active_sessions[user_id]
        try:
            is_auth = await data['client'].is_user_authorized()
            await message.answer(f"🔧 Debug:\nAuth: {is_auth}\nClient: {data['client'].session}")
        except Exception as e:
            await message.answer(f"🔧 Debug Error: {e}")
    else:
        await message.answer("❌ Нет активной сессии")

@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🔐 **Помощь по генератору сессий**\n\n"
        "Как использовать:\n"
        "1. Нажмите /start\n"
        "2. Нажмите 'Создать сессию через QR-код'\n"
        "3. Отсканируйте QR-код в Telegram\n"
        "4. **Обязательно подтвердите вход** в приложении\n"
        "5. **Сессия придет автоматически**\n\n"
        "Команды:\n"
        "/start - начать создание сессии\n"
        "/check - проверить статус\n"
        "/help - эта справка\n\n"
        "⚠️ **Важно:** После сканирования нужно нажать 'Подключить' в Telegram!"
    )
    await message.answer(help_text)

async def main():
    logger.info("🚀 Starting Working QR Session Bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
