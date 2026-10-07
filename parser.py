#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер экспорта Telegram HTML (папка с *.html).
Ищет сообщения, содержащие заданные слова (Snowball-стемминг),
выгружает совпадения в CSV, включая имя чата.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Требуется beautifulsoup4. pip install beautifulsoup4 lxml", file=sys.stderr)
    sys.exit(1)

try:
    from nltk.stem.snowball import SnowballStemmer
except ImportError:
    print("Требуется nltk. pip install nltk", file=sys.stderr)
    sys.exit(1)


# ---------- Утилиты ----------

def normalize_word(word: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", word.lower(), flags=re.UNICODE)


def tokenize(text: str):
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text)


def build_stem_filter(search_words, language):
    """
    Стеммит только "нормальные" слова.
    Короткие (<3 букв) и аббревиатуры (все заглавные) сравниваются как есть,
    регистрозависимо.
    """
    stemmer = SnowballStemmer(language)

    target_stems = {}
    exact_targets = set()

    for w in search_words:
        w_stripped = w.strip()
        if not w_stripped:
            continue

        letters = [c for c in w_stripped if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            exact_targets.add(w_stripped)
            continue

        w_norm = normalize_word(w_stripped)
        if not w_norm:
            continue

        if len(w_norm) < 3:
            exact_targets.add(w_norm)
            continue

        stem = stemmer.stem(w_norm)
        target_stems.setdefault(stem, w_stripped)

    if not target_stems and not exact_targets:
        raise ValueError("Список искомых слов пуст после нормализации.")

    def match(tokens):
        matched_targets = set()
        matched_forms = set()

        for tok in tokens:
            if tok in exact_targets:
                matched_targets.add(tok)
                matched_forms.add(tok)
                continue

            tok_norm = normalize_word(tok)
            if not tok_norm:
                continue

            if len(tok_norm) < 3:
                if tok_norm in exact_targets:
                    matched_targets.add(tok_norm)
                    matched_forms.add(tok_norm)
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

def extract_chat_name(soup: BeautifulSoup, html_path: Path) -> str:
    header = soup.select_one("div.page_header .content .text.bold")
    if header:
        name = header.get_text(strip=True)
        if name:
            return name

    if soup.title and soup.title.string:
        t = soup.title.string.strip()
        if t and t.lower() not in ("exported data",):
            return t

    return html_path.stem


def parse_html_file(html_path: Path):
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    chat_name = extract_chat_name(soup, html_path)

    messages = []
    for div in soup.select("div.message"):
        classes = div.get("class") or []
        is_service = "service" in classes
        msg_id_raw = div.get("id") or ""
        m = re.match(r"message(-?\d+)$", msg_id_raw)
        msg_id = m.group(1) if m else msg_id_raw

        date_title = ""
        time_short = ""
        date_div = div.select_one("div.pull_right.date.details") or div.select_one("div.date.details")
        if date_div:
            date_title = date_div.get("title", "").strip()
            time_short = date_div.get_text(strip=True)

        author = ""
        author_link = None
        author_initials = ""
        if not is_service:
            from_name_div = div.select_one("div.from_name")
            if from_name_div:
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
            details = div.select_one("div.body.details")
            if details:
                author = details.get_text(strip=True)

        message_link = f"#go_to_message{msg_id}" if msg_id else None

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

        text = ""
        text_div = div.select_one("div.text")
        if text_div:
            text = text_div.get_text(separator=" ", strip=True)
        elif is_service:
            details = div.select_one("div.body.details")
            if details:
                text = details.get_text(separator=" ", strip=True)

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
            "source_file": html_path.name,
        })

    return chat_name, messages


def collect_html_files(root: Path):
    if root.is_file():
        if root.suffix.lower() == ".html":
            return [root]
        return []
    return sorted([p for p in root.rglob("*.html") if p.is_file()])


# ---------- Фильтрация ----------

def filter_messages(messages, matcher, exclude_patterns=None,
                    include_service=False, include_media_only=False,
                    min_matches=1):
    """
    exclude_patterns — список скомпилированных regex'ов.
    Если в тексте есть ЛЮБОЕ совпадение — сообщение отбрасывается.
    """
    exclude_patterns = exclude_patterns or []
    result = []

    for msg in messages:
        if msg["is_service"] and not include_service:
            continue
        text = msg["text"] or ""
        if not text and not include_media_only:
            continue

        # 1) выкидываем по чёрному списку фраз
        if any(rx.search(text) for rx in exclude_patterns):
            continue

        # 2) обычный поиск
        tokens = tokenize(text)
        if not tokens:
            continue
        matched_targets, matched_forms = matcher(tokens)
        if len(matched_targets) < min_matches:
            continue

        result.append((msg, sorted(matched_targets), sorted(matched_forms)))

    return result


# ---------- CSV ----------

CSV_FIELDS = [
    "chat",
    "source_file",
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
        description="Поиск сообщений в папке с Telegram-HTML-экспортом (Snowball)."
    )
    ap.add_argument("path", type=Path,
                    help="Путь к папке с *.html или к одному messages.html")
    ap.add_argument("-w", "--words", required=True,
                    help="Список искомых слов через запятую: 'кот,собака,мем'")
    ap.add_argument("-o", "--output", type=Path, default=Path("result.csv"),
                    help="Путь к выходному CSV (по умолчанию result.csv)")
    ap.add_argument("-l", "--language", default="russian",
                    choices=["russian", "english", "german", "french",
                             "spanish", "italian", "porter"],
                    help="Язык для SnowballStemmer (по умолчанию russian)")
    ap.add_argument("--include-service", action="store_true",
                    help="Включать сервисные сообщения")
    ap.add_argument("--include-media-only", action="store_true",
                    help="Включать сообщения без текста, но с медиа")
    ap.add_argument("--unique", action="store_true",
                    help="Оставить только по одному сообщению на (chat,id)")
    ap.add_argument("--case-sensitive", action="store_true",
                    help="Учитывать регистр (зарезервировано)")
    ap.add_argument("--exclude-phrases", default="",
                    help="Список фраз через || (двойной вертикальный слэш), "
                         "при наличии любой из них сообщение отбрасывается. "
                         "Пример: 'Подпишись на новости Иркутска||реклама||erid:'")
    ap.add_argument("--min-matches", type=int, default=1,
                    help="Минимальное число разных искомых слов в сообщении "
                         "(по умолчанию 1)")
    args = ap.parse_args()

    exclude_patterns = []
    if args.exclude_phrases.strip():
        for phrase in args.exclude_phrases.split("||"):
            phrase = phrase.strip()
            if not phrase:
                continue
            exclude_patterns.append(re.compile(re.escape(phrase), re.IGNORECASE))
        print(f"→ Чёрный список фраз ({len(exclude_patterns)}):")
        for p in exclude_patterns:
            print(f"   - {p.pattern}")

    if not args.path.exists():
        print(f"Путь не найден: {args.path}", file=sys.stderr)
        sys.exit(1)

    html_files = collect_html_files(args.path)
    if not html_files:
        print(f"В {args.path} не найдено ни одного .html файла", file=sys.stderr)
        sys.exit(1)

    print(f"→ Найдено HTML-файлов: {len(html_files)}")
    for p in html_files:
        print(f"   - {p}")

    search_words = [w.strip() for w in args.words.split(",") if w.strip()]
    if not search_words:
        print("Список искомых слов пуст.", file=sys.stderr)
        sys.exit(1)

    matcher = build_stem_filter(search_words, args.language)

    rows = []
    total_msgs = 0
    total_hits = 0

    for html_path in html_files:
        try:
            chat_name, messages = parse_html_file(html_path)
        except Exception as e:
            print(f"  ! Ошибка при парсинге {html_path}: {e}", file=sys.stderr)
            continue

        total_msgs += len(messages)
        hits = filter_messages(
            messages,
            matcher,
            exclude_patterns=exclude_patterns,
            include_service=args.include_service,
            include_media_only=args.include_media_only,
            min_matches=args.min_matches,
        )

        if args.unique:
            seen = set()
            unique_hits = []
            for msg, mt, mf in hits:
                key = (chat_name, msg["id"])
                if key in seen:
                    continue
                seen.add(key)
                unique_hits.append((msg, mt, mf))
            hits = unique_hits

        total_hits += len(hits)
        print(f"  → {html_path.name}: чат='{chat_name}', сообщений={len(messages)}, совпадений={len(hits)}")

        for msg, matched_targets, matched_forms in hits:
            rows.append({
                "chat": chat_name,
                "source_file": msg["source_file"],
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
    print(f"\n✓ Всего сообщений: {total_msgs}")
    print(f"✓ Всего совпадений: {total_hits}")
    print(f"✓ Результат: {args.output}")


if __name__ == "__main__":
    main()