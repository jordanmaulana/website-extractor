ARCH := $(shell uname -m)

upgrade:
	uv sync
	uv lock --upgrade
	uv sync --frozen --no-install-project

lint:
	uv run ruff format .
	uv run ruff check . --fix

dev:
	uv run manage.py runserver 8000

tele:
	uv run python manage.py run_telegram_bot

mmg:
	uv run manage.py makemigrations

migrate:
	uv run manage.py migrate

tw-run:
	npx @tailwindcss/cli -i ./static/input.css -o ./static/output.css --watch

tw-build:
	npx @tailwindcss/cli -i ./static/input.css -o ./static/output.css

web:
	cd frontend && pnpm run dev

test:
	uv run python main.py https://help.libreoffice.org/latest/en-US/text/shared/05/new_help.html --selenium

test-images:
	uv run python main.py https://sisi.id --include-images

worker:
	uv run celery -A celery_app worker --loglevel=info --concurrency=4

worker-daemon:
	uv run celery -A celery_app worker --loglevel=info --concurrency=4 --detach

flower:
	uv run celery -A celery_app flower --port=5555

redis:
	redis-server --port 6379