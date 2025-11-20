FROM python:3.11
LABEL maintainer="tg@ArseniyKvasov"

WORKDIR /FastLessonPublic

RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

RUN python manage.py collectstatic --noinput
RUN python manage.py makemigrations
RUN python manage.py migrate

CMD ["bash", "-c", "python manage.py create_admin && python -m fastlesson_bot.bot & celery -A fastlesson beat -l INFO & celery -A fastlesson worker -l INFO & python manage.py runserver 0.0.0.0:8000"]
