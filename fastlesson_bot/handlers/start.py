from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied

from fastlesson_bot.handlers.teacher import main_menu
from fastlesson_bot.services.rate_limit import check_rate_limit
from fastlesson_bot.services.user_service import get_or_create_user, get_user_by_tg, track_user_activity
from core.models import UserRole

router = Router()


def role_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="👨🏫 Репетитор", callback_data="set_role:tutor")
    kb.button(text="🏫 Школьный учитель", callback_data="set_role:school_teacher")
    kb.adjust(1)
    return kb


@router.message(F.text == "На главную")
async def main_menu_via_reply_button(message: types.Message, state: FSMContext):
    """
    Обработка reply-кнопки "На главную".
    """
    try:
        await main_menu(message, state)
    except Exception:
        try:
            await main_menu(message, state)
        except Exception:
            pass


@router.message(Command("start"))
async def start_handler(message: types.Message):
    """
    Обрабатывает команду /start: показывает приветствие, проверяет авторизацию
    пользователя и выводит клавиатуры для продолжения.
    """
    user = await get_user_by_tg(message.from_user.id)

    reply_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="На главную")]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    greeting_text = (
        f"👋 Здравствуйте, {message.from_user.first_name}!\n\n"
        "Создавайте идеальные рабочие листы, соответствующие ФГОС, за 2 клика — "
        "забудьте о долгих часах подготовки!"
    )

    if user:
        try:
            await message.answer(greeting_text, reply_markup=reply_kb)
        except Exception:
            pass

        try:
            await sync_to_async(check_rate_limit)(
                message.from_user.id,
                "start_command",
                limit=1,
                window=20
            )
        except PermissionDenied as e:
            try:
                await message.answer(f"⚠️ {str(e)}")
            except Exception:
                pass
            return

        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Изменить роль", callback_data="change_role")
        kb.button(text="➡️ Далее", callback_data="choose_subject")

        text = (
            f"Вы уже зарегистрированы как *{user.get_role_display()}*.\n\n"
            "Нажмите одну из кнопок ниже, чтобы продолжить."
        )

        try:
            await message.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
        except Exception:
            try:
                await message.answer(text, parse_mode="Markdown")
            except Exception:
                pass

    else:
        try:
            await message.answer(greeting_text, reply_markup=reply_kb)
        except Exception:
            pass

        choose_text = (
            "Выберите свою роль, чтобы я мог правильно помогать вам:"
        )

        try:
            await message.answer(
                text=choose_text,
                reply_markup=role_keyboard().as_markup()
            )
        except Exception:
            try:
                await message.answer(choose_text)
            except Exception:
                pass


@router.callback_query(F.data == "change_role")
async def change_role_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        text="Выберите новую роль:",
        reply_markup=role_keyboard().as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_role:"))
async def set_role_handler(callback: types.CallbackQuery):
    try:
        role_value = callback.data.split(":")[1]
        role = UserRole(role_value)
        user = await get_or_create_user(callback.from_user.id, role, telegram_username=callback.from_user.username)

        await sync_to_async(track_user_activity)(user)

        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Изменить роль", callback_data="change_role")

        text = f"✅ Роль сохранена!\nВы зарегистрированы как *{user.get_role_display()}*."
        if role == UserRole.STUDENT:
            text += (
                "\nСтановитесь лучше, оставаясь в Telegram.\n"
                "Отправьте ссылку на этого бота вашему преподавателю.\n\n"
                "Если вы уже договорились с учителем, но задание не появилось — "
                "попросите отправить ссылку еще раз."
            )
        else:
            kb.button(text="➡️ Далее", callback_data="choose_subject")

        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.as_markup())
        await callback.answer()

    except (ValueError, IndexError) as e:
        await callback.answer(f"Ошибка: {str(e)}", show_alert=True)
