FROM python:3.13-slim AS project-base

ARG POETRY_VERSION=2.1.3

COPY poetry.lock poetry.toml /app/
RUN python -m pip install --upgrade pip && pip install poetry==2.1.3

FROM project-base AS widesky-base

WORKDIR /widesky/

COPY ./src/widesky/widesky ./

RUN poetry install --without dev

CMD ["python", "/widesky/widesky/widesky.py"]
