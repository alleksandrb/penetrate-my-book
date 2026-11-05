import re
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import parse_qs, unquote

from src.books import BookDecodeError, SyncResult, add_book, sync_book, words_to_sync
from src.languages import languages
from src.report import definitions
from src.words import TOKEN
from src.store import (
    book_counts,
    book_title,
    books,
    enter_user,
    is_book,
    known_count,
    language_of,
    list_users,
    mark_known,
    sentences_of,
    unknown_words,
    user_name,
)

_LOCK = threading.Lock()
_LESSONS: dict[tuple[int, str], dict[str, list[str] | int]] = {}
_BOOK = re.compile(
    r"/books/([A-Za-z_][A-Za-z0-9_]*-[a-z]+)/(learn|learned|prev|next|sync)"
)
_SYNC_LOCK = threading.Lock()
_RUNNING: set[str] = set()
_NOTICES: dict[str, str] = {}


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _page(title: str, body: str, head: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
{head}
<style>
body {{ font-family: sans-serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid #ccc; }}
.word {{ font-size: 2rem; margin: 1.2rem 0; }}
h2 {{ font-size: 1rem; margin: 1.2rem 0 0.4rem; }}
button {{ font-size: 1rem; padding: 0.45rem 0.8rem; }}
form.line {{ display: inline; }}
a {{ color: inherit; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _drop(words: list[str], index: int, word: str) -> int:
    while word in words:
        at = words.index(word)
        words.pop(at)
        if index > at:
            index -= 1
    if not words:
        return 0
    return min(index, len(words) - 1)


def _learn_begin(user_id: int, book: str) -> None:
    words = unknown_words(user_id, book)
    with _LOCK:
        _LESSONS[(user_id, book)] = {"words": words, "index": 0}


def _learn_current(user_id: int, book: str) -> tuple[str | None, int, int]:
    with _LOCK:
        lesson = _LESSONS.get((user_id, book))
        if lesson is not None:
            words = lesson["words"]
            if not words:
                return None, 0, 0
            index = min(max(int(lesson["index"]), 0), len(words) - 1)
            lesson["index"] = index
            return words[index], index, len(words)
    _learn_begin(user_id, book)
    return _learn_current(user_id, book)


def _learn_move(user_id: int, book: str, step: int) -> None:
    with _LOCK:
        lesson = _LESSONS.get((user_id, book))
        if lesson is None or not lesson["words"]:
            return
        last = len(lesson["words"]) - 1
        lesson["index"] = min(max(int(lesson["index"]) + step, 0), last)


def _learned(user_id: int, book: str, word: str) -> None:
    language = language_of(book)
    mark_known(user_id, word, language)
    with _LOCK:
        for (owner, other), lesson in _LESSONS.items():
            if owner != user_id or language_of(other) != language:
                continue
            lesson["index"] = _drop(lesson["words"], int(lesson["index"]), word)


def _login_page(error: str = "") -> str:
    names = "".join(
        "<li><form class='line' method='post' action='/login'>"
        f"<button name='name' value='{_esc(name)}'>{_esc(name)}</button>"
        "</form></li>"
        for _user_id, name in list_users()
    )
    notice = f"<p>{_esc(error)}</p>" if error else ""
    listing = f"<ul>{names}</ul>" if names else ""
    return _page(
        "Войти",
        f"""<h1>Войти</h1>
{notice}
{listing}
<form method="post" action="/login">
<label>Имя <input name="name" autofocus></label>
<button>Войти</button>
</form>""",
    )


def _cookie(header: str | None, key: str) -> str | None:
    if not header:
        return None
    prefix = f"{key}="
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _language(header: str | None) -> str:
    code = _cookie(header, "lang")
    if code and any(lang.code == code for lang in languages()):
        return code
    return "en"


def _language_switch(code: str) -> str:
    parts = []
    for lang in languages():
        if lang.code == code:
            parts.append(f"<strong>{_esc(lang.label)}</strong>")
            continue
        parts.append(
            "<form class='line' method='post' action='/language'>"
            f"<button name='lang' value='{lang.code}'>{_esc(lang.label)}</button>"
            "</form>"
        )
    return " ".join(parts)


def _sync_state() -> tuple[set[str], dict[str, str]]:
    with _SYNC_LOCK:
        return set(_RUNNING), dict(_NOTICES)


def _start_sync(book: str) -> None:
    with _SYNC_LOCK:
        if book in _RUNNING:
            return
        _RUNNING.add(book)
        _NOTICES.pop(book, None)

    def work() -> None:
        try:
            result = sync_book(book)
        except Exception as error:
            result = SyncResult("error", 0, type(error).__name__)
        with _SYNC_LOCK:
            _RUNNING.discard(book)
            if result.status == "rate_limit":
                _NOTICES[book] = "Достигнут рейт лимит"
            elif result.status == "error":
                _NOTICES[book] = result.detail
            else:
                _NOTICES.pop(book, None)

    threading.Thread(target=work, daemon=True).start()


def _books_page(
    user_id: int, name: str, language: str, upload_error: str = ""
) -> str:
    running, notices = _sync_state()
    rows = []
    messages = []
    for book in books(language):
        title = book_title(book)
        known, unknown = book_counts(user_id, book)
        pending = words_to_sync(book)
        if book in running:
            sync = "Синхронизация…"
        elif pending:
            sync = (
                "<form class='line' method='post' "
                f"action='/books/{_esc(book)}/sync'>"
                "<button>Синхронизировать</button></form>"
            )
        else:
            sync = ""
            notices.pop(book, None)
        if book in notices and pending:
            messages.append(f"<p>{_esc(title)}: {_esc(notices[book])}</p>")
        rows.append(
            "<tr>"
            f"<td>{_esc(title)}</td><td>{known}</td><td>{unknown}</td>"
            "<td><form class='line' method='post' "
            f"action='/books/{_esc(book)}/learn'>"
            "<button>Учить слова</button></form> "
            f"{sync}</td>"
            "</tr>"
        )
    table = (
        "<table><tr><th>Книга</th><th>Знакомые</th><th>Незнакомые</th><th></th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p>Книг пока нет.</p>"
    )
    upload = f"<p>{_esc(upload_error)}</p>" if upload_error else ""
    head = "<meta http-equiv='refresh' content='2'>" if running else ""
    return _page(
        "Книги",
        f"""<h1>Книги</h1>
<p>{_esc(name)}
<form class="line" method="post" action="/logout">
<button>Сменить пользователя</button>
</form></p>
<p>{_language_switch(language)}</p>
<p>Слова которые я знаю: {known_count(user_id, language)}</p>
<form method="post" action="/books" enctype="multipart/form-data">
<input type="file" name="book" accept=".txt,text/plain">
<button>Добавить книгу</button>
</form>
{upload}
{''.join(messages)}
{table}""",
        head,
    )


_LEARN_KEYS = """<script>
document.addEventListener("keydown", function (event) {
  if (event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
  var tag = event.target && event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  var id = event.key === "ArrowLeft" ? "prev" : event.key === "ArrowRight" ? "next" : "";
  var form = id && document.getElementById(id);
  if (!form) return;
  event.preventDefault();
  if (form.requestSubmit) form.requestSubmit();
  else form.submit();
});
</script>
"""


def _mark(sentence: str, word: str) -> str:
    parts = []
    last = 0
    for match in TOKEN.finditer(sentence):
        parts.append(_esc(sentence[last : match.start()]))
        token = match.group()
        shown = _esc(token)
        if token.lower() == word:
            shown = f"<strong>{shown}</strong>"
        parts.append(shown)
        last = match.end()
    parts.append(_esc(sentence[last:]))
    return "".join(parts)


def _items(title: str, lines: list[str], empty: str, word: str = "") -> str:
    if not lines:
        return f"<h2>{title}</h2><p>{empty}</p>"
    body = "".join(
        f"<li>{_mark(line, word) if word else _esc(line)}</li>" for line in lines
    )
    return f"<h2>{title}</h2><ul>{body}</ul>"


def _learn_page(user_id: int, book: str) -> str:
    word, index, total = _learn_current(user_id, book)
    if word is None:
        body = "<p>Незнакомых слов не осталось.</p>"
    else:
        senses = definitions(word, language_of(book))
        bases = []
        glosses = []
        for pos, base, meaning in senses:
            if base and base not in bases:
                bases.append(base)
            glosses.append(f"{pos}: {meaning}" if pos else meaning)
        body = (
            f"<p class='word'>{_esc(word)}</p>"
            f"<p>{index + 1} из {total}</p>"
            + _items("Начальная форма", bases, "Начальной формы пока нет.")
            + _items("Определения", glosses, "Определения пока нет.")
            + _items("Примеры", sentences_of(book, word), "Примеров нет.", word)
            + f"<form id='prev' class='line' method='post' action='/books/{_esc(book)}/prev'>"
            "<button>← Назад</button></form> "
            f"<form id='next' class='line' method='post' action='/books/{_esc(book)}/next'>"
            "<button>Дальше →</button></form> "
            f"<form class='line' method='post' action='/books/{_esc(book)}/learned'>"
            f"<input type='hidden' name='word' value='{_esc(word)}'>"
            "<button>Выучил</button></form>"
            + _LEARN_KEYS
        )
    title = book_title(book)
    return _page(
        title,
        f"<h1>{_esc(title)}</h1>{body}<p><a href='/'>К списку</a></p>",
    )


def _uploaded_book(content_type: str, body: bytes) -> tuple[str, bytes] | None:
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        return None
    boundary = b"--" + match.group(1).encode()
    for part in body.split(boundary):
        if b"Content-Disposition" not in part:
            continue
        header, _, data = part.partition(b"\r\n\r\n")
        if not _:
            continue
        data = data.removesuffix(b"\r\n")
        text = header.decode("utf-8", "replace")
        if 'name="book"' not in text:
            continue
        filename = ""
        found = re.search(r'filename="([^"]*)"', text)
        if found:
            filename = Path(found.group(1)).name
        return filename, data
    return None


def _user_id(header: str | None) -> int | None:
    value = _cookie(header, "user")
    if value and value.isdigit():
        user_id = int(value)
        if user_name(user_id) is not None:
            return user_id
    return None


class App(BaseHTTPRequestHandler):
    def address_string(self) -> str:
        return self.client_address[0]

    def do_GET(self) -> None:
        path_only, _, query = self.path.partition("?")
        path = unquote(path_only)
        user_id = _user_id(self.headers.get("Cookie"))
        if path == "/":
            if user_id is None:
                self._html(_login_page())
                return
            upload = parse_qs(query).get("upload", [""])[0]
            error = {
                "empty": "Выберите файл",
                "encoding": "Файл должен быть в UTF-8 или Windows-1251",
            }.get(
                upload, ""
            )
            self._html(
                _books_page(
                    user_id,
                    user_name(user_id) or "",
                    _language(self.headers.get("Cookie")),
                    error,
                )
            )
            return
        match = _BOOK.fullmatch(path)
        if match and user_id is not None and is_book(match.group(1)):
            book, action = match.group(1), match.group(2)
            if action == "learn":
                self._html(_learn_page(user_id, book))
                return
        if match and user_id is None:
            self._redirect("/")
            return
        self._html(_page("Нет страницы", "<p>Нет страницы.</p>"), status=404)

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        raw = self._raw()
        user_id = _user_id(self.headers.get("Cookie"))
        if path == "/books":
            if user_id is None:
                self._redirect("/")
                return
            self._upload(raw, _language(self.headers.get("Cookie")))
            return
        fields = parse_qs(raw.decode("utf-8"))
        if path == "/language":
            if user_id is None:
                self._redirect("/")
                return
            code = fields.get("lang", [""])[0]
            if not any(lang.code == code for lang in languages()):
                self._redirect("/")
                return
            self._redirect("/", cookie=f"lang={code}; Path=/; HttpOnly; SameSite=Lax")
            return
        if path == "/login":
            name = fields.get("name", [""])[0]
            entered = enter_user(name)
            if entered is None:
                self._html(_login_page("Введите имя"))
                return
            self._redirect("/", cookie=f"user={entered}; Path=/; HttpOnly; SameSite=Lax")
            return
        if path == "/logout":
            self._redirect("/", cookie="user=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            return
        match = _BOOK.fullmatch(path)
        if match is None or user_id is None:
            self._redirect("/")
            return
        book, action = match.group(1), match.group(2)
        if not is_book(book):
            self._html(_page("Нет страницы", "<p>Нет страницы.</p>"), status=404)
            return
        if action == "learn":
            _learn_begin(user_id, book)
            self._redirect(f"/books/{book}/learn")
            return
        if action == "sync":
            if words_to_sync(book):
                _start_sync(book)
            self._redirect("/")
            return
        if action == "prev":
            _learn_move(user_id, book, -1)
        elif action == "next":
            _learn_move(user_id, book, 1)
        elif action == "learned":
            word = fields.get("word", [""])[0]
            if word:
                _learned(user_id, book, word)
        self._redirect(f"/books/{book}/learn")

    def _raw(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length)

    def _upload(self, raw: bytes, language: str) -> None:
        found = _uploaded_book(self.headers.get("Content-Type", ""), raw)
        if found is None or not found[0]:
            self._redirect("/?upload=empty")
            return
        filename, data = found
        try:
            add_book(filename, data, language)
        except BookDecodeError:
            self._redirect("/?upload=encoding")
            return
        self._redirect("/")

    def _html(self, page: str, status: int = 200, cookie: str | None = None) -> None:
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()


class Server(ThreadingHTTPServer):
    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def serve() -> None:
    server = Server(("127.0.0.1", 8000), App)
    print("http://127.0.0.1:8000", flush=True)
    server.serve_forever()
