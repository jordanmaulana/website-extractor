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

mmg:
	uv run manage.py makemigrations

migrate:
	uv run manage.py migrate

tw:
	npx @tailwindcss/cli -i ./static/input.css -o ./static/output.css --watch

test:
	uv run python main.py https://uhudtour.com/