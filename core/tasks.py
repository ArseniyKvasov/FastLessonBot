import logging
import json

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from core.models import Lesson, LessonBlock, GenerationStatus, SubjectChoices, ImproveStatus
from .services.ai import generate_text

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="core.tasks.generate_lesson_task")
def generate_lesson_task(self, lesson_id: str):
    """
    Генерация структуры урока и содержимого блоков.
    Структура (список hints) не учитывается в completed,
    completed увеличивается только при успешной генерации каждого блока.
    Если блок не сгенерировался — уменьшаем total и продолжаем.
    """
    try:
        lesson = Lesson.objects.get(id=lesson_id)
    except Lesson.DoesNotExist:
        logger.error(f"❌ Lesson {lesson_id} not found")
        return

    status, _ = GenerationStatus.objects.get_or_create(
        lesson=lesson,
        defaults={"status": GenerationStatus.Status.IN_PROGRESS, "total": 0, "completed": 0},
    )
    status.status = GenerationStatus.Status.IN_PROGRESS
    status.completed = 0
    status.save(update_fields=["status", "completed", "updated_at"])

    try:
        structure_query = (
            f"Составь структуру рабочего листа по теме: {lesson.title}. "
            f"Предмет: {lesson.get_subject_display()}, уровень: {lesson.get_level_display()}. "
            f"Верни JSON с ключом 'blocks', где каждый элемент содержит поле 'block_topic' — "
            f"тему, что должно быть в блоке."
        )

        logger.info(f"🔄 Generating structure for interactive lesson {lesson_id}")
        structure_json = generate_text(query=structure_query)
        logger.info(f"🔍 structure_json type={type(structure_json)} repr={str(structure_json)[:800]}")

        if isinstance(structure_json, dict):
            structure = structure_json
        else:
            try:
                structure = json.loads(structure_json)
            except Exception as e:
                logger.exception(f"❌ Failed to parse structure JSON for lesson {lesson_id}: {e}")
                status.status = GenerationStatus.Status.FAILED
                status.save(update_fields=["status", "updated_at"])
                return

        blocks_hints = structure.get("blocks", [])
        if not isinstance(blocks_hints, list):
            logger.error(f"❌ 'blocks' is not a list for lesson {lesson_id}: {blocks_hints}")
            status.status = GenerationStatus.Status.FAILED
            status.save(update_fields=["status", "updated_at"])
            return

        total_blocks = len(blocks_hints)
        status.total = total_blocks
        status.save(update_fields=["total", "updated_at"])
        logger.info(f"Structure generated for lesson {lesson_id}: total_blocks={total_blocks}")

        for i, block_topic in enumerate(blocks_hints, start=1):
            hint = None
            if isinstance(block_topic, dict):
                hint = block_topic.get("block_topic") or block_topic.get("prompt") or str(block_topic)
            else:
                hint = str(block_topic)

            block_query = (
                f"Ты - методист. Используй Markdown. Сгенерируй целый контент раздела рабочего листа и небольшое задание без ответов для урока '{lesson.title}'. "
                f"Предмет: '{lesson.get_subject_display()}'. "
                f"Тема раздела: {hint}. "
                f"Верни JSON с полями: title (str), content (str), has_task (true/false). "
                f"Отвечай только JSON-объектом."
            )
            if lesson.subject == SubjectChoices.FOREIGN_LANG:
                block_query += (
                    " Все примеры предложений и задания должны быть написаны "
                    "на изучаемом иностранном языке (НЕ на русском). А объяснение (если не сказано иного) - на русском."
                )

            logger.info(f"→ Generating block {i}/{total_blocks} for lesson {lesson_id} (hint={hint[:200]})")

            try:
                block_data = generate_text(query=block_query)

                if isinstance(block_data, str):
                    try:
                        block_data = json.loads(block_data)
                    except Exception as e:
                        raise ValueError(f"block_data is str but not json: {e}")

                if not isinstance(block_data, dict):
                    raise ValueError(f"block_data is not dict: {type(block_data)}")

                title = block_data.get("title") or f"Блок {i}"
                content = block_data.get("content") or ""
                has_task = bool(block_data.get("has_task", False))

            except Exception as e:
                logger.warning(f"Failed to generate block {i} for lesson {lesson_id}: {e}")
                if status.total > 0:
                    status.total = max(0, status.total - 1)
                    status.save(update_fields=["total", "updated_at"])
                continue

            try:
                with transaction.atomic():
                    LessonBlock.objects.create(
                        lesson=lesson,
                        order=i,
                        title=title,
                        content=content,
                        has_task=has_task,
                    )
                status.completed = status.completed + 1
                status.save(update_fields=["completed", "updated_at"])

                logger.info(f"✅ Block {i} saved for lesson {lesson_id} (has_task={has_task})")
            except Exception as e:
                logger.exception(f"❌ DB error saving block {i} for lesson {lesson_id}: {e}")
                if status.total > 0:
                    status.total = max(0, status.total - 1)
                    status.save(update_fields=["total", "updated_at"])
                continue

        try:
            blocks = list(LessonBlock.objects.filter(lesson=lesson).order_by('order', 'id'))
            for idx, block in enumerate(blocks, start=1):
                if block.order != idx:
                    block.order = idx
                    block.save(update_fields=['order'])
            logger.info(f"🔧 Orders fixed for lesson {lesson_id}")
        except Exception as e:
            logger.exception(f"❌ Failed to fix orders for lesson {lesson_id}: {e}")

        status.status = GenerationStatus.Status.DONE if status.completed == status.total else GenerationStatus.Status.FAILED if status.completed == 0 else GenerationStatus.Status.IN_PROGRESS
        status.save(update_fields=["status", "updated_at"])
        logger.info(
            f"Generation finished for lesson {lesson_id}: {status.completed}/{status.total} (status={status.status})")

    except Exception as e:
        logger.exception(f"❌ Unexpected error during generation for lesson {lesson_id}: {e}")
        status.status = GenerationStatus.Status.FAILED
        status.save(update_fields=["status", "updated_at"])


MODE_DESCRIPTIONS = {
    "complexify": "усложни материал, добавь подробности и дополнительные примеры",
    "simplify": "упрости материал, сделай его проще и понятнее",
    "more_tasks": "добавь больше заданий и упражнений по теме",
    "remove_tasks": "убери часть заданий и упражнений, оставив только ключевые"
}


@shared_task(bind=True, name="core.tasks.improve_block_task")
def improve_block_task(self, block_id: int, mode: str, improve_id: int):
    try:
        block = LessonBlock.objects.get(id=block_id)
        status = ImproveStatus.objects.get(id=improve_id)
        human_mode = MODE_DESCRIPTIONS.get(mode, mode)
    except (LessonBlock.DoesNotExist, ImproveStatus.DoesNotExist):
        return

    status.status = ImproveStatus.Status.IN_PROGRESS
    status.task_id = self.request.id
    status.save(update_fields=["status", "task_id", "updated_at"])

    try:
        query = (
            f"Ты помогаешь улучшить урок.\n"
            f"Тема раздела: {block.title}\n"
            f"Текущий контент раздела:\n{block.content}\n\n"
            f"Задача — {human_mode}.\n"
            "Верни только JSON с полем {\"improved_content\": str}"
        )

        ai_response = generate_text(query=query)

        if isinstance(ai_response, str):
            try:
                ai_response = json.loads(ai_response)
            except Exception as e:
                raise ValueError(f"AI вернул невалидный JSON: {e}")

        new_content = ai_response.get("improved_content")
        if not new_content:
            raise ValueError("AI не вернул поле 'improved_content'")

        block.content = new_content
        block.save(update_fields=["content", "updated_at"])

        status.status = ImproveStatus.Status.DONE
        status.result_content = new_content
        status.save(update_fields=["status", "result_content", "updated_at"])

    except Exception as e:
        logger.exception(f"❌ Failed to improve block {block_id}: {e}")
        status.status = ImproveStatus.Status.FAILED
        status.save(update_fields=["status", "updated_at"])
