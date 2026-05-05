FROM node:20-slim AS ui-build

WORKDIR /ui

COPY ui/ ./

RUN npm ci && npm run build

FROM python:3.12-slim

WORKDIR /app

COPY server/pyproject.toml ./pyproject.toml
COPY server/alembic.ini ./alembic.ini
COPY server/alembic ./alembic
COPY server/src ./src

RUN pip install --no-cache-dir .

COPY --from=ui-build /ui/dist ./ui/dist

EXPOSE 8000

CMD ["uvicorn", "syncarr_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
