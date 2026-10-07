FROM python:3.11-slim

LABEL maintainer="telegram-html-parser"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Системные зависимости для lxml (нужны libxml2/libxslt)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Предзагрузка Snowball-стеммеров для работы офлайн
RUN python -c "\
from nltk.stem.snowball import SnowballStemmer;\
[SnowballStemmer(l) for l in ['russian','english','german','french','spanish','italian','porter']]" \
    && echo "Snowball stemmers cached"

COPY parser.py .

# Папки для входных и выходных данных монтируются томами
RUN mkdir -p /data /out

ENTRYPOINT ["python", "parser.py"]
CMD ["--help"]