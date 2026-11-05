import asyncio
import urllib.error
from pathlib import Path

from config.api import ASYNC_REQUESTS
from src.languages import get
from src.store import (
    known_api_words,
    language_of,
    save_text_words,
    table_from_path,
    text_words,
)
from src.sync import RateLimit, fetch_entry, save_entry


class SyncResult:
    def __init__(self, status: str, saved: int = 0, detail: str = ""):
        self.status = status
        self.saved = saved
        self.detail = detail


class BookDecodeError(Exception):
    pass


def book_text(data: bytes) -> str:
    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BookDecodeError


def add_book(filename: str, text: str | bytes, language: str) -> str:
    lang = get(language)
    if isinstance(text, bytes):
        text = book_text(text)
    name = table_from_path(Path(Path(filename).name), lang.code)
    word_counts, sentences = lang.read(text)
    save_text_words(name, word_counts, sentences)
    return name


def words_to_sync(name: str) -> list[str]:
    language = language_of(name)
    lang = get(language)
    known = known_api_words(language)
    return [
        word
        for word, _count in text_words(name)
        if lang.can_sync(word) and word not in known
    ]


async def _run(words: list[str], language: str, on_progress) -> SyncResult:
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for index, word in enumerate(words, start=1):
        queue.put_nowait((index, word))

    stop = False
    saved = 0
    status = "done"
    detail = ""
    total = len(words)

    async def worker() -> None:
        nonlocal stop, saved, status, detail
        while not stop:
            try:
                index, word = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                body = await asyncio.to_thread(fetch_entry, word, language)
            except RateLimit:
                if not stop:
                    stop = True
                    status = "rate_limit"
                return
            except urllib.error.HTTPError as error:
                if not stop:
                    stop = True
                    status = "error"
                    detail = f"http {error.code} for {word}"
                return
            except Exception as error:
                if not stop:
                    stop = True
                    status = "error"
                    detail = f"{type(error).__name__} for {word}"
                return
            await asyncio.to_thread(save_entry, word, body, language)
            saved += 1
            if on_progress:
                on_progress(index, total, word)

    await asyncio.gather(*(worker() for _ in range(min(ASYNC_REQUESTS, total))))
    return SyncResult(status, saved, detail)


def sync_book(name: str, on_progress=None) -> SyncResult:
    language = language_of(name)
    words = words_to_sync(name)
    if not words:
        return SyncResult("empty")
    return asyncio.run(_run(words, language, on_progress))
