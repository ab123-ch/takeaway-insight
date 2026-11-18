FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && useradd --uid 10001 --create-home takeaway && mkdir /app/data && chown takeaway:takeaway /app/data
ENV PATH="/app/.venv/bin:$PATH"
USER takeaway
EXPOSE 8765
CMD ["takeaway", "serve", "--host", "0.0.0.0", "--port", "8765"]
