FROM mcr.microsoft.com/devcontainers/python:3.13

ARG POETRY_VERSION=2.1.3
ENV	PYTHONFAULTHANDLER=1 \
	PYTHONUNBUFFERED=1 \
	PYTHONHASHSEED=random \
	PIP_NO_CACHE_DIR=1 \
	PIP_DISABLE_PIP_VERSION_CHECK=1 \
	PIP_DEFAULT_TIMEOUT=100

RUN apt-get update && apt-get upgrade -y && python -m pip install --upgrade pip \
	&& pip install "poetry==$POETRY_VERSION"

COPY pyproject.toml poetry.lock ./

RUN poetry lock && poetry install --only dev --no-root