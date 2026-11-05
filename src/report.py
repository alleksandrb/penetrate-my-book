import csv
from pathlib import Path

from src.languages import get
from src.store import ROOT, api_response, language_of, text_words, word_sentences


def _numbered(items: list[str]) -> str:
    if len(items) > 1:
        return " ".join(f"{index}. {item}" for index, item in enumerate(items, start=1))
    return items[0] if items else ""


def definitions(word: str, language: str) -> list[tuple[str, str, str]]:
    lang = get(language)
    return lang.definitions(word, api_response(word, language))


def create_csv(name: str) -> Path:
    language = language_of(name)
    saved = word_sentences(name)
    pending = []
    for word, count in text_words(name):
        text = _numbered(saved.get(word, []))
        senses = definitions(word, language) or [("", "", "")]
        pending.extend(
            (word, count, pos, base, meaning, text) for pos, base, meaning in senses
        )
    pending.sort(key=lambda row: (-row[1], row[0], row[2]))
    output = ROOT / "storage" / f"{name}.csv"
    with output.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "word",
                "count-in-text",
                "part-of-speech",
                "base-form",
                "meaning",
                "sentences",
            ]
        )
        for index, row in enumerate(pending, start=1):
            writer.writerow((index, *row))
    return output
