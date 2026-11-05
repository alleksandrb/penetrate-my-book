import json
import re
import sqlite3
from pathlib import Path

from src.languages import codes

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "storage" / "knowledge.db"
STEM = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_book(name: str) -> tuple[str, str] | None:
    stem, sep, code = name.rpartition("-")
    if not sep or code not in codes() or not STEM.fullmatch(stem):
        return None
    return stem, code


def language_of(name: str) -> str:
    parsed = parse_book(name)
    if parsed is None:
        raise SystemExit(f"invalid table name: {name}")
    return parsed[1]


def book_title(name: str) -> str:
    parsed = parse_book(name)
    if parsed is None:
        raise SystemExit(f"invalid table name: {name}")
    return parsed[0]


def table_name(name: str) -> str:
    if parse_book(name) is None:
        raise SystemExit(f"invalid table name: {name}")
    return name


def book_table(stem: str, language: str) -> str:
    if language not in codes() or not STEM.fullmatch(stem):
        raise SystemExit(f"invalid table name: {stem}-{language}")
    return f"{stem}-{language}"


def table_from_path(path: Path, language: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_]", "_", path.stem)
    if not stem or stem[0].isdigit():
        stem = f"t_{stem}"
    return book_table(stem, language)


def dictionary_table(language: str) -> str:
    if language not in codes():
        raise SystemExit(f"unknown language: {language}")
    return language


def known_table(language: str) -> str:
    if language not in codes():
        raise SystemExit(f"unknown language: {language}")
    return f"known-{language}"


def _reserved() -> set[str]:
    names = {"users", "api", "known"}
    for code in codes():
        names.add(code)
        names.add(known_table(code))
    return names


def _existing(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _migrate(db: sqlite3.Connection) -> None:
    names = _existing(db)
    if "api" in names and "en" not in names:
        db.execute("ALTER TABLE api RENAME TO en")
        names.remove("api")
        names.add("en")
    if "known" in names and "known-en" not in names:
        db.execute('ALTER TABLE known RENAME TO "known-en"')
        names.remove("known")
        names.add("known-en")
    reserved = _reserved()
    for name in list(names):
        if name in reserved or name.startswith("sqlite_") or parse_book(name):
            continue
        if not STEM.fullmatch(name):
            continue
        target = f"{name}-en"
        if target in names:
            continue
        db.execute(f'ALTER TABLE "{name}" RENAME TO "{target}"')
        names.remove(name)
        names.add(target)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
    )
    _migrate(db)
    for code in codes():
        db.execute(
            f'''CREATE TABLE IF NOT EXISTS "{dictionary_table(code)}" (
                word TEXT PRIMARY KEY,
                response TEXT NOT NULL
            )'''
        )
        db.execute(
            f'''CREATE TABLE IF NOT EXISTS "{known_table(code)}" (
                user_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                PRIMARY KEY (user_id, word)
            )'''
        )
    return db


def save_text_words(
    name: str, counts: dict[str, int], sentences: dict[str, list[str]]
) -> None:
    name = table_name(name)
    with connect() as db:
        db.execute(f'DROP TABLE IF EXISTS "{name}"')
        db.execute(
            f'''CREATE TABLE "{name}" (
                word TEXT PRIMARY KEY,
                count INTEGER NOT NULL,
                sentences TEXT NOT NULL
            )'''
        )
        db.executemany(
            f'INSERT INTO "{name}" (word, count, sentences) VALUES (?, ?, ?)',
            (
                (
                    word,
                    count,
                    json.dumps(sentences.get(word, []), ensure_ascii=False),
                )
                for word, count in counts.items()
            ),
        )


def text_words(name: str) -> list[tuple[str, int]]:
    name = table_name(name)
    with connect() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        if not exists:
            raise SystemExit(f"no table: {name}")
        return db.execute(
            f'SELECT word, count FROM "{name}" ORDER BY count DESC, word'
        ).fetchall()


def word_sentences(name: str) -> dict[str, list[str]]:
    name = table_name(name)
    with connect() as db:
        rows = db.execute(f'SELECT word, sentences FROM "{name}"').fetchall()
    return {word: json.loads(raw) for word, raw in rows}


def sentences_of(name: str, word: str) -> list[str]:
    name = table_name(name)
    with connect() as db:
        row = db.execute(
            f'SELECT sentences FROM "{name}" WHERE word = ?',
            (word,),
        ).fetchone()
    return [] if row is None else json.loads(row[0])


def known_api_words(language: str) -> set[str]:
    table = dictionary_table(language)
    with connect() as db:
        return {row[0] for row in db.execute(f'SELECT word FROM "{table}"')}


def save_api_response(word: str, response: str, language: str) -> None:
    table = dictionary_table(language)
    with connect() as db:
        db.execute(
            f'INSERT INTO "{table}" (word, response) VALUES (?, ?)',
            (word, response),
        )


def api_response(word: str, language: str) -> str | None:
    table = dictionary_table(language)
    with connect() as db:
        row = db.execute(
            f'SELECT response FROM "{table}" WHERE word = ?',
            (word,),
        ).fetchone()
    return None if row is None else row[0]


def list_users() -> list[tuple[int, str]]:
    with connect() as db:
        return db.execute("SELECT id, name FROM users ORDER BY name").fetchall()


def enter_user(name: str) -> int | None:
    name = name.strip()
    if not name:
        return None
    with connect() as db:
        row = db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        cursor = db.execute("INSERT INTO users (name) VALUES (?)", (name,))
        return cursor.lastrowid


def user_name(user_id: int) -> str | None:
    with connect() as db:
        row = db.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
    return None if row is None else row[0]


def books(language: str | None = None) -> list[str]:
    if language is not None and language not in codes():
        raise SystemExit(f"unknown language: {language}")
    reserved = _reserved()
    with connect() as db:
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    found = []
    for (name,) in rows:
        parsed = parse_book(name)
        if name in reserved or name.startswith("sqlite_") or parsed is None:
            continue
        if language is not None and parsed[1] != language:
            continue
        found.append(name)
    return found


def is_book(name: str) -> bool:
    return parse_book(name) is not None and name in books()


def book_counts(user_id: int, book: str) -> tuple[int, int]:
    book = table_name(book)
    known = known_table(language_of(book))
    with connect() as db:
        known_total = db.execute(
            f'''SELECT COUNT(*) FROM "{book}" AS book
                WHERE EXISTS (
                    SELECT 1 FROM "{known}" AS known
                    WHERE known.user_id = ? AND known.word = book.word
                )''',
            (user_id,),
        ).fetchone()[0]
        total = db.execute(f'SELECT COUNT(*) FROM "{book}"').fetchone()[0]
    return known_total, total - known_total


def unknown_words(user_id: int, book: str) -> list[str]:
    book = table_name(book)
    known = known_table(language_of(book))
    with connect() as db:
        rows = db.execute(
            f'''SELECT book.word FROM "{book}" AS book
                WHERE NOT EXISTS (
                    SELECT 1 FROM "{known}" AS known
                    WHERE known.user_id = ? AND known.word = book.word
                )
                ORDER BY book.count DESC, book.word''',
            (user_id,),
        ).fetchall()
    return [row[0] for row in rows]


def known_count(user_id: int, language: str) -> int:
    table = known_table(language)
    with connect() as db:
        return db.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE user_id = ?',
            (user_id,),
        ).fetchone()[0]


def mark_known(user_id: int, word: str, language: str) -> None:
    table = known_table(language)
    with connect() as db:
        db.execute(
            f'INSERT OR IGNORE INTO "{table}" (user_id, word) VALUES (?, ?)',
            (user_id, word),
        )
