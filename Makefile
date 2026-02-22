ARCH := $(shell uname -m)

upgrade:
	uv sync
	uv lock --upgrade
	uv sync --frozen --no-install-project

lint:
	uv run ruff format .
	uv run ruff check . --fix

test:
	uv run python main.py https://uhudtour.com/