FROM python:3.13-slim

WORKDIR /app

COPY poetry.lock poetry.toml /app/
RUN python -m pip install --upgrade pip &&\
    pip install poetry==2.1.3 &&\
    poetry install --only dev --no-root &&\
    pip install pre-commit==4.2.0 &&\
    pre-commit install-hooks

COPY widesky /app/widesky

CMD ["python", "/app/widesky/widesky.py"]
