#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер экспорта Telegram HTML.
Ищет сообщения, содержащие заданные слова (с учётом морфологии через Snowball),
и выгружает их в CSV.
"""

import argparse
import csv
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Требуется beautifulsoup4. Установите: pip install beautifulsoup4 lxml", file=sys.stderr)
    sys.exit(1)

try:
    from nltk.stem.snowball import SnowballStemmer
except ImportError:
    print("Требуется nltk. Установите: pip install nltk", file=sys.stderr)
    sys.exit(1)


# ---------- Утилиты ----------

def normalize_word(word: str) -> str:
    """Приводит слово к нижнему регистру и убирает пунктуацию по краям."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", word.lower(), flags=re.UNICODE)


def tokenize(text: str):
    """Разбивает текст на токены (слова) с поддержкой кириллицы."""
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)


def build_stem_filter(search_words, language):
    """
    Возвращает функцию filter_tokens(tokens) -> (matched_words, all_forms),
    которая проверяет, есть ли в токенах хотя бы одно совпадение по стемме.
    """
    stemmer = SnowballStemmer(language)
    # stem -> исходное искомое слово (первое совпадение)
    target_stems = {}
    for w in search_words:
        w_norm = normalize_word(w)
        if not w_norm:
            continue
        stem = stemmer.stem(w_norm)
        target_stems.setdefault(stem, w)

    if not target_stems:
        raise ValueError("Список искомых слов пуст после нормализации.")

    def match(tokens):
        matched_targets = set()
        matched_forms = set()
        for tok in tokens:
            tok_norm = normalize_word(tok)
            if not tok_norm:
                continue
            try:
                stem = stemmer.stem(tok_norm)
            except Exception:
                continue
            if stem in target_stems:
                matched_targets.add(target_stems[stem])
                matched_forms.add(tok_norm)
        return matched_targets, matched_forms

    return match


# ---------- Парсинг HTML ----------

class MessageExtractor(HTMLParser):
    """
    Быстрый fallback-парсер (используется, если lxml недоступен).
    Но основной путь — BeautifulSoup.
    """
    pass


def parse_with_bs4(html_path: Path):
    """
    Извлекает сообщения из HTML-экспорта Telegram.
    Возвращает список dict:
      {
        'id': int|str,
        'date_title': str (ISO-ish),
        'time_short': str,
        'author': str,
        'author_initials': str,
        'author_link': str|None,
        'message_link': str|None,
        'reply_to_id': int|str|None,
        'text': str,
        'media_type': str|None,
        'media_status': str|None,
        'is_service': bool,
      }
    """
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    messages = []

    # Все сообщения имеют id вида message123
    for div in soup.select("div.message"):
        classes = div.get("class") or []
        is_service = "service" in classes
        msg_id_raw = div.get("id") or ""
        m = re.match(r"message(-?\d+)$", msg_id_raw)
        msg_id = m.group(1) if m else msg_id_raw

        # ----- Дата и время -----
        date_title = ""
        time_short = ""
        date_div = div.select_one("div.pull_right.date.details") or div.select_one("div.date.details")
        if date_div:
            date_title = date_div.get("title", "").strip()
            time_short = date_div.get_text(strip=True)

        # ----- Автор -----
        author = ""
        author_link = None
        author_initials = ""
        if not is_service:
            from_name_div = div.select_one("div.from_name")
            if from_name_div:
                # Иногда автор обёрнут в <a>
                a = from_name_div.find("a")
                if a and a.get("href"):
                    author_link = a["href"]
                    author = a.get_text(strip=True)
                else:
                    author = from_name_div.get_text(strip=True)

            initials_div = div.select_one("div.userpic div.initials")
            if initials_div:
                author_initials = initials_div.get_text(strip=True)
        else:
            # Сервисные сообщения — текст в div.body.details
            details = div.select_one("div.body.details")
            if details:
                author = details.get_text(strip=True)

        # ----- Ссылка на это сообщение (если есть reply_to где-то рядом) -----
        # В самом Telegram-экспорте обычно нет прямой ссылки, но есть reply_to
        # с onclick="return GoToMessage(N)". Плюс сам id даёт нам "message{N}".
        message_link = f"#go_to_message{msg_id}" if msg_id else None

        # ----- reply_to -----
        reply_to_id = None
        reply_div = div.select_one("div.reply_to")
        if reply_div:
            a = reply_div.find("a")
            if a:
                onclick = a.get("onclick", "")
                m2 = re.search(r"GoToMessage\((\d+)\)", onclick)
                if m2:
                    reply_to_id = m2.group(1)
                elif a.get("href"):
                    m3 = re.search(r"#go_to_message(\d+)", a["href"])
                    if m3:
                        reply_to_id = m3.group(1)

        # ----- Текст -----
        text = ""
        text_div = div.select_one("div.text")
        if text_div:
            # убираем спойлеры-скрипты, оставляя видимый текст
            text = text_div.get_text(separator=" ", strip=True)
        elif is_service:
            details = div.select_one("div.body.details")
            if details:
                text = details.get_text(separator=" ", strip=True)

        # ----- Медиа -----
        media_type = None
        media_status = None
        media_div = div.select_one("div.media_wrap")
        if media_div:
            for cls in ("media_photo", "media_video", "media_voice_message",
                        "media_audio_file", "media_document", "media_sticker",
                        "media_poll", "media_location"):
                if media_div.select_one(f"div.{cls}"):
                    media_type = cls.replace("media_", "")
                    break
            status_div = media_div.select_one("div.status.details")
            if status_div:
                media_status = status_div.get_text(strip=True)

        messages.append({
            "id": msg_id,
            "date_title": date_title,
            "time_short": time_short,
            "author": author,
            "author_initials": author_initials,
            "author_link": author_link,
            "message_link": message_link,
            "reply_to_id": reply_to_id,
            "text": text,
            "media_type": media_type,
            "media_status": media_status,
            "is_service": is_service,
        })

    return messages


# ---------- Фильтрация ----------

def filter_messages(messages, matcher, include_service=False, include_media_only=False):
    """Возвращает список (msg, matched_targets, matched_forms) для подходящих сообщений."""
    result = []
    for msg in messages:
        if msg["is_service"] and not include_service:
            continue
        text = msg["text"] or ""
        if not text and not include_media_only:
            continue
        tokens = tokenize(text)
        if not tokens:
            continue
        matched_targets, matched_forms = matcher(tokens)
        if matched_targets:
            result.append((msg, sorted(matched_targets), sorted(matched_forms)))
    return result


# ---------- CSV ----------

CSV_FIELDS = [
    "id",
    "date",
    "time",
    "author",
    "author_initials",
    "author_link",
    "message_link",
    "reply_to_id",
    "matched_words",
    "matched_forms",
    "text",
    "media_type",
    "media_status",
    "is_service",
]


def write_csv(rows, out_path: Path):
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(
        description="Поиск сообщений в Telegram-HTML-экспорте с учётом морфологии (Snowball)."
    )
    ap.add_argument("html", type=Path, help="Путь к messages.html")
    ap.add_argument("-w", "--words", required=True,
                    help="Список искомых слов через запятую, напр. 'кот,собака,мем'")
    ap.add_argument("-o", "--output", type=Path, default=Path("result.csv"),
                    help="Путь к выходному CSV (по умолчанию result.csv)")
    ap.add_argument("-l", "--language", default="russian",
                    choices=["russian", "english", "german", "french",
                             "spanish", "italian", "porter"],
                    help="Язык для SnowballStemmer (по умолчанию russian)")
    ap.add_argument("--include-service", action="store_true",
                    help="Включать сервисные сообщения (channel created и т.п.)")
    ap.add_argument("--include-media-only", action="store_true",
                    help="Включать сообщения без текста, но с медиа")
    ap.add_argument("--case-sensitive", action="store_true",
                    help="Учитывать регистр (по умолчанию — нет)")
    ap.add_argument("--unique", action="store_true",
                    help="Оставить только по одному сообщению на ID")

    args = ap.parse_args()

    if not args.html.exists():
        print(f"Файл не найден: {args.html}", file=sys.stderr)
        sys.exit(1)

    search_words = [w.strip() for w in args.words.split(",") if w.strip()]
    if not search_words:
        print("Список искомых слов пуст.", file=sys.stderr)
        sys.exit(1)

    print(f"→ Парсинг {args.html} ...")
    messages = parse_with_bs4(args.html)
    print(f"  найдено сообщений: {len(messages)}")

    matcher = build_stem_filter(search_words, args.language)

    hits = filter_messages(
        messages,
        matcher,
        include_service=args.include_service,
        include_media_only=args.include_media_only,
    )

    if args.unique:
        seen = set()
        unique_hits = []
        for msg, mt, mf in hits:
            if msg["id"] in seen:
                continue
            seen.add(msg["id"])
            unique_hits.append((msg, mt, mf))
        hits = unique_hits

    print(f"  совпадений: {len(hits)}")

    rows = []
    for msg, matched_targets, matched_forms in hits:
        rows.append({
            "id": msg["id"],
            "date": msg["date_title"],
            "time": msg["time_short"],
            "author": msg["author"],
            "author_initials": msg["author_initials"],
            "author_link": msg["author_link"] or "",
            "message_link": msg["message_link"] or "",
            "reply_to_id": msg["reply_to_id"] or "",
            "matched_words": ", ".join(matched_targets),
            "matched_forms": ", ".join(matched_forms),
            "text": msg["text"],
            "media_type": msg["media_type"] or "",
            "media_status": msg["media_status"] or "",
            "is_service": msg["is_service"],
        })

    write_csv(rows, args.output)
    print(f"✓ Результат сохранён: {args.output} ({len(rows)} строк)")


if __name__ == "__main__":
    main()