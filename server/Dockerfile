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

RUN pip install --no-cache-dir -e .

COPY --from=ui-build /ui/dist ./ui/dist
COPY server/entrypoint.sh ./entrypoint.sh

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
