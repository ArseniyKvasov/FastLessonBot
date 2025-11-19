from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import User, UserRole


class Command(BaseCommand):
    help = 'Создает суперпользователя Django'

    def handle(self, *args, **options):
        UserModel = get_user_model()
        if not UserModel.objects.filter(username='admin').exists():
            UserModel.objects.create_superuser(
                username='admin',
                email='admin@example.com',
                password='1234'
            )
            self.stdout.write(
                self.style.SUCCESS('Django суперпользователь создан: admin/1234')
            )