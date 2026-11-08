# Image de l'application PaperReading (API + interface web).
# torch en version CPU : image bien plus légère que la version CUDA, suffisante pour
# calculer les embeddings de quelques milliers de papiers.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu ".[embeddings]"

# Utilisateur sans privilèges ; modèles SPECTER2 et cache arXiv dans un volume.
RUN useradd --create-home --uid 1000 papersim
USER papersim
ENV HF_HOME=/home/papersim/.cache/huggingface \
    PAPERSIM_CACHE_DIR=/home/papersim/.cache/papersim \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["papersim", "serve", "--host", "0.0.0.0", "--port", "8000"]
