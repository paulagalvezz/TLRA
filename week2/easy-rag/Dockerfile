# EASY-RAG application image.
#
# collections-manager is a PATH dependency that lives OUTSIDE this build context
# (in the course repo ~/ludo-engsoft). We deliberately do NOT bake it into the
# image. Instead:
#   * the image only carries the app source + uv,
#   * docker-compose mounts ~/ludo-engsoft at the exact relative location that
#     pyproject.toml expects (../../../ludo-engsoft from WORKDIR),
#   * the entrypoint runs `uv sync` at startup, so dependency resolution finds
#     collections-manager exactly as it does locally.
FROM python:3.12-slim

# uv manages the venv and resolves the path dependency at runtime.
RUN pip install --no-cache-dir uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Mirror the host layout so the relative path dependency resolves:
#   WORKDIR/../../../ludo-engsoft  ==  /workspace/ludo-engsoft   (the runtime mount)
WORKDIR /workspace/TLRA/week2/easy-rag

# App source + lockfile (uv.lock is portable: it references collections-manager
# by the same relative path). .venv/, data/ and .env are excluded via .dockerignore.
COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/workspace/TLRA/week2/easy-rag/docker-entrypoint.sh"]
