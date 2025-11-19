import logging
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from celery import shared_task

from core.models import Lesson
from metrics.models import Message
from metrics.utils import send_message_to_user

logger = logging.getLogger(__name__)


@shared_task
def send_pending_messages():
    """
    Отправляет отложенные сообщения, учитывая активности пользователей,
    количество попыток и успешность отправки.
    """

    pending = (
        Message.objects
        .filter(status="pending", send_attempts__lt=5)
        .select_related("recipient__metrics")
    )

    if not pending.exists():
        return

    now = timezone.now()
    threshold = now - timedelta(minutes=15)

    for message in pending:
        user = message.recipient

        last_active = getattr(getattr(user, "metrics", None), "last_active_at", None)

        if last_active is None:
            message.send_attempts += 1
            message.save(update_fields=["send_attempts"])
            continue

        if last_active > threshold:
            continue

        ok = send_message_to_user(
            user=user,
            text=message.text,
            button_text=message.button_text,
            button_command=message.button_command,
            button_url=message.button_url
        )

        message.send_attempts += 1
        if ok:
            message.status = "sent"
        elif message.send_attempts >= 3:
            message.status = "error"

        message.save(update_fields=["status", "send_attempts"])


@shared_task
def notify_unopened_and_undownloaded_lessons():
    """
    Уведомляет авторов уроков, если урок не был открыт или скачан.
    Отправляет не более одного уведомления на пользователя.
    """

    MAX_ATTEMPTS = 3
    INACTIVITY_MINUTES = 5

    now = timezone.now()
    threshold = now - timedelta(minutes=INACTIVITY_MINUTES)

    lessons = (
        Lesson.objects
        .annotate(blocks_count=Count("blocks"))
        .filter(
            Q(blocks_count__gt=0) &
            (
                Q(discover_notified=False, is_discovered=False) |
                Q(download_notified=False, is_downloaded=False)
            )
        )
        .select_related("creator", "creator__metrics")
        .order_by("-created_at")
    )

    if not lessons.exists():
        return

    creator_ids = lessons.values_list("creator_id", flat=True).distinct()

    for creator_id in creator_ids:
        lesson = lessons.filter(creator_id=creator_id).first()
        if not lesson:
            continue

        if lesson.notify_attempts >= MAX_ATTEMPTS:
            continue

        user = lesson.creator
        last_active = getattr(getattr(user, "metrics", None), "last_active_at", None)

        if last_active is None:
            lesson.notify_attempts += 1
            lesson.save(update_fields=["notify_attempts"])
            continue

        if last_active > threshold:
            continue

        # Выбираем тип уведомления
        if not lesson.is_discovered and not lesson.discover_notified:
            text = f"Ваш урок «{lesson.title}» готов. Можете посмотреть!"
            btn_text = "Посмотреть"
            btn_cmd = f"lesson_view:{lesson.id}:1"
            tag = "discover"
        elif not lesson.is_downloaded and not lesson.download_notified:
            text = f"Ваш урок «{lesson.title}» готов. Его можно скачать!"
            btn_text = "Скачать"
            btn_cmd = f"lesson_download:{lesson.id}"
            tag = "download"
        else:
            continue

        ok = False
        try:
            ok = send_message_to_user(
                user=user,
                text=text,
                button_text=btn_text,
                button_command=btn_cmd
            )
        except Exception:
            ok = False

        if ok:
            if tag == "discover":
                lesson.discover_notified = True
            else:
                lesson.download_notified = True
        else:
            lesson.notify_attempts += 1

        fields = ["notify_attempts"]
        if lesson.discover_notified:
            fields.append("discover_notified")
        if lesson.download_notified:
            fields.append("download_notified")

        fields = list(dict.fromkeys(fields))

        lesson.save(update_fields=fields)
