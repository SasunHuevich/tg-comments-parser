FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Предзагрузка стеммеров, чтобы работать офлайн
RUN python -c "\
from nltk.stem.snowball import SnowballStemmer;\
[SnowballStemmer(l) for l in ['russian','english','german','french','spanish','italian','porter']]" \
    && echo "Snowball stemmers cached"

COPY parser.py .

# /in — входная папка с html, /out — куда положить csv
RUN mkdir -p /in /out

ENTRYPOINT ["python", "parser.py"]
CMD ["--help"]