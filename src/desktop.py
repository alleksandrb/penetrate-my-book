import sqlite3
import sys
import threading
from pathlib import Path

from src.books import BookDecodeError, SyncResult, add_book, sync_book, words_to_sync
from src.languages import languages
from src.report import definitions
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
from src.words import TOKEN

try:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, ttk
except ImportError:
    tk = None
    filedialog = None
    tkfont = None
    ttk = None


class _Deck:
    def __init__(self, words: list[str]) -> None:
        self.words = words
        self.index = 0


class Lessons:
    def __init__(self) -> None:
        self._items: dict[tuple[int, str], _Deck] = {}

    def begin(self, user_id: int, book: str, words: list[str]) -> None:
        self._items[(user_id, book)] = _Deck(list(words))

    def current(self, user_id: int, book: str) -> tuple[str | None, int, int]:
        deck = self._items.get((user_id, book))
        if deck is None or not deck.words:
            return None, 0, 0
        deck.index = min(max(deck.index, 0), len(deck.words) - 1)
        return deck.words[deck.index], deck.index, len(deck.words)

    def move(self, user_id: int, book: str, step: int) -> bool:
        deck = self._items.get((user_id, book))
        if deck is None or not deck.words:
            return False
        new = min(max(deck.index + step, 0), len(deck.words) - 1)
        if new == deck.index:
            return False
        deck.index = new
        return True

    def forget(self, user_id: int, word: str, language: str) -> None:
        for (owner, book), deck in self._items.items():
            if owner != user_id or language_of(book) != language:
                continue
            deck.index = _drop(deck.words, deck.index, word)


def _drop(words: list[str], index: int, word: str) -> int:
    while word in words:
        at = words.index(word)
        words.pop(at)
        if index > at:
            index -= 1
    if not words:
        return 0
    return min(index, len(words) - 1)


def _tk_message() -> str:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.platform == "darwin":
        hint = f"brew install python-tk@{version}"
    elif sys.platform.startswith("linux"):
        hint = "поставьте пакет python3-tk"
    else:
        hint = "поставьте Python с python.org, в нём уже есть Tk"
    return f"Нет Tk, окно не откроется. {hint}"


def _learn_parts(word: str, language: str) -> tuple[list[str], list[str]]:
    bases: list[str] = []
    glosses: list[str] = []
    for pos, base, meaning in definitions(word, language):
        if base and base not in bases:
            bases.append(base)
        glosses.append(f"{pos}: {meaning}" if pos else meaning)
    return bases, glosses


class Desktop:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.minsize(640, 440)
        self._center(780, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._fonts()
        self.shell = ttk.Frame(self.root, padding=(20, 16, 20, 16))
        self.shell.pack(fill="both", expand=True)
        self.user_id: int | None = None
        self.language = "en"
        self.lessons = Lessons()
        self._book: str | None = None
        self._screen = ""
        self._login_error = ""
        self._upload_error = ""
        self._adding = False
        self._alive = True
        self._epoch = 0
        self._learn_pending = False
        self._name_var = tk.StringVar()
        self._lang_var = tk.StringVar(value="en")
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._notices: dict[str, str] = {}
        self._progress: dict[str, tuple[int, int]] = {}
        self._status: dict[str, tk.StringVar] = {}
        self._wheel_target = None
        self._texts: list[tk.Text] = []
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind_all("<Button-4>", self._wheel_up)
        self.root.bind_all("<Button-5>", self._wheel_down)
        # Tk 9 reports a touchpad swipe as TouchpadScroll, not MouseWheel.
        try:
            self.root.bind_all("<TouchpadScroll>", self._touchpad)
        except tk.TclError:
            pass
        self.root.bind_all("<Left>", self._on_left)
        self.root.bind_all("<Right>", self._on_right)
        self.root.bind_all("<KP_Left>", self._on_left)
        self.root.bind_all("<KP_Right>", self._on_right)
        self.root.bind_all("<Escape>", self._on_escape)

    def run(self) -> None:
        self._show_login()
        self.root.mainloop()

    def _fonts(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        size = abs(int(base.cget("size"))) or 13
        family = base.cget("family")
        self.body_font = tkfont.Font(family=family, size=size)
        self.bold_font = tkfont.Font(family=family, size=size, weight="bold")
        self.section_font = tkfont.Font(family=family, size=size, weight="bold")
        self.title_font = tkfont.Font(family=family, size=size + 6, weight="bold")
        self.word_font = tkfont.Font(family=family, size=size + 16, weight="bold")

    def _center(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        x = max((self.root.winfo_screenwidth() - width) // 2, 0)
        y = max((self.root.winfo_screenheight() - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _close(self) -> None:
        self._alive = False
        self.root.destroy()

    def _post(self, callback) -> None:
        if not self._alive:
            return
        try:
            self.root.after(0, callback)
        except tk.TclError:
            return

    def _clear(self) -> None:
        for child in self.shell.winfo_children():
            child.destroy()
        self._status = {}
        self._texts = []
        self._wheel_target = None

    def _title(self, text: str) -> None:
        self.root.title(f"{text} — penetrate-my-book")

    def _show_login(self) -> None:
        self._screen = "login"
        self._book = None
        self._epoch += 1
        self._clear()
        self._title("Войти")
        ttk.Label(self.shell, text="Войти", font=self.title_font).pack(anchor="w")
        if self._login_error:
            ttk.Label(self.shell, text=self._login_error).pack(anchor="w", pady=(8, 0))
        names = list_users()
        if names:
            holder, body = self._scroll()
            holder.pack(fill="both", expand=True, pady=(12, 0))
            for _user_id, name in names:
                ttk.Button(
                    body,
                    text=name,
                    command=lambda name=name: self._enter(name),
                ).pack(anchor="w", pady=2)
        form = ttk.Frame(self.shell)
        form.pack(fill="x", pady=(12, 0))
        ttk.Label(form, text="Имя").pack(side="left")
        entry = ttk.Entry(form, textvariable=self._name_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        button = ttk.Button(form, text="Войти", command=self._submit_login)
        button.pack(side="left")
        self._default_button(button)
        entry.bind("<Return>", lambda _event: self._submit_login())
        entry.focus_set()

    def _submit_login(self) -> None:
        self._enter(self._name_var.get())

    def _enter(self, name: str) -> None:
        user_id = enter_user(name)
        if user_id is None:
            self._login_error = "Введите имя"
            self._post(self._show_login)
            return
        self.user_id = user_id
        self._login_error = ""
        self._name_var.set("")
        self._upload_error = ""
        self._post(self._show_books)

    def _logout(self) -> None:
        self.user_id = None
        self._upload_error = ""
        self._post(self._show_login)

    def _show_books(self) -> None:
        name = user_name(self.user_id) if self.user_id is not None else None
        if name is None:
            self.user_id = None
            self._show_login()
            return
        self._screen = "books"
        self._book = None
        self._epoch += 1
        epoch = self._epoch
        self._clear()
        self._title("Книги")
        top = ttk.Frame(self.shell)
        top.pack(fill="x")
        ttk.Button(top, text="Сменить пользователя", command=self._logout).pack(
            side="right"
        )
        ttk.Label(top, text=name).pack(side="right", padx=(0, 12))
        ttk.Label(top, text="Книги", font=self.title_font).pack(side="left")
        switch = ttk.Frame(self.shell)
        switch.pack(anchor="w", pady=(12, 0))
        self._lang_var.set(self.language)
        state = "disabled" if self._adding else "normal"
        for lang in languages():
            ttk.Radiobutton(
                switch,
                text=lang.label,
                value=lang.code,
                variable=self._lang_var,
                command=self._on_language,
                state=state,
            ).pack(side="left", padx=(0, 12))
        ttk.Label(
            self.shell,
            text=f"Слова которые я знаю: {known_count(self.user_id, self.language)}",
        ).pack(anchor="w", pady=(12, 0))
        add = ttk.Frame(self.shell)
        add.pack(anchor="w", pady=(12, 0))
        ttk.Button(
            add,
            text="Добавление…" if self._adding else "Добавить книгу",
            command=self._choose_book,
            state=state,
        ).pack(side="left")
        if self._upload_error:
            ttk.Label(self.shell, text=self._upload_error).pack(anchor="w", pady=(8, 0))
        ttk.Separator(self.shell, orient="horizontal").pack(fill="x", pady=(12, 0))
        holder, body = self._scroll()
        holder.pack(fill="both", expand=True, pady=(8, 0))
        found = books(self.language)
        if not found:
            ttk.Label(body, text="Книг пока нет.").pack(anchor="w")
        else:
            self._book_table(body, found)
        if self._syncing() or self._adding:
            self.root.after(300, lambda epoch=epoch: self._poll(epoch))

    def _book_table(self, body, found: list[str]) -> None:
        table = ttk.Frame(body)
        table.pack(fill="x")
        table.columnconfigure(0, weight=1)
        headers = ("Книга", "Знакомые", "Незнакомые", "")
        for column, text in enumerate(headers):
            ttk.Label(table, text=text, font=self.bold_font).grid(
                row=0, column=column, sticky="w", padx=(0, 16), pady=(0, 4)
            )
        ttk.Separator(table, orient="horizontal").grid(
            row=1, column=0, columnspan=4, sticky="ew"
        )
        grid_row = 2
        for book in found:
            known, unknown = book_counts(self.user_id, book)
            pending, running, notice = self._book_flags(book)
            ttk.Label(table, text=book_title(book)).grid(
                row=grid_row, column=0, sticky="w", padx=(0, 16), pady=6
            )
            ttk.Label(table, text=str(known)).grid(
                row=grid_row, column=1, sticky="w", padx=(0, 16)
            )
            ttk.Label(table, text=str(unknown)).grid(
                row=grid_row, column=2, sticky="w", padx=(0, 16)
            )
            actions = ttk.Frame(table)
            actions.grid(row=grid_row, column=3, sticky="w", pady=4)
            ttk.Button(
                actions,
                text="Учить слова",
                command=lambda book=book: self._open_learn(book),
            ).pack(side="left")
            if pending and not running:
                ttk.Button(
                    actions,
                    text="Синхронизировать",
                    command=lambda book=book: self._start_sync(book),
                ).pack(side="left", padx=(8, 0))
            if running or notice:
                var = tk.StringVar(value=notice or self._progress_text(book))
                self._status[book] = var
                ttk.Label(actions, textvariable=var, wraplength=280, justify="left").pack(
                    side="left", padx=(8, 0)
                )
            grid_row += 1
            ttk.Separator(table, orient="horizontal").grid(
                row=grid_row, column=0, columnspan=4, sticky="ew"
            )
            grid_row += 1

    def _book_flags(self, book: str) -> tuple[bool, bool, str]:
        pending = bool(words_to_sync(book))
        with self._lock:
            running = book in self._running
            if running:
                return pending, True, ""
            if pending:
                return True, False, self._notices.get(book, "")
            self._notices.pop(book, None)
        return False, False, ""

    def _progress_text(self, book: str) -> str:
        with self._lock:
            index, total = self._progress.get(book, (0, 0))
        if total:
            return f"Синхронизация… {index}/{total}"
        return "Синхронизация…"

    def _row_status(self, book: str) -> str:
        with self._lock:
            running = book in self._running
            notice = self._notices.get(book, "")
        if running:
            return self._progress_text(book)
        if not notice:
            return ""
        try:
            still = bool(words_to_sync(book))
        except sqlite3.OperationalError:
            return notice
        return notice if still else ""

    def _on_language(self) -> None:
        code = self._lang_var.get()
        if self._adding or code == self.language:
            return
        if not any(lang.code == code for lang in languages()):
            return
        self.language = code
        self._post(self._show_books)

    def _choose_book(self) -> None:
        if self._adding or self.user_id is None:
            return
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Добавить книгу",
            filetypes=(("Текст", "*.txt"), ("Все файлы", "*.*")),
        )
        if not selected:
            return
        self._import_path(Path(selected))

    def _import_path(self, path: Path) -> None:
        if self._adding:
            return
        self._adding = True
        self._upload_error = ""
        language = self.language
        self._post(self._show_books)

        def work() -> None:
            error = ""
            try:
                add_book(path.name, path.read_bytes(), language)
            except BookDecodeError:
                error = "Файл должен быть в UTF-8 или Windows-1251"
            except OSError as caught:
                error = caught.strerror or "Не удалось прочитать файл"
            except (Exception, SystemExit) as caught:
                error = str(caught) or type(caught).__name__

            def done() -> None:
                if not self._alive:
                    return
                self._adding = False
                self._upload_error = error
                if self._screen == "books":
                    self._show_books()

            self._post(done)

        threading.Thread(target=work, daemon=True).start()

    def _start_sync(self, book: str) -> None:
        if not words_to_sync(book):
            with self._lock:
                self._notices.pop(book, None)
            self._post(self._show_books)
            return
        with self._lock:
            if book in self._running:
                return
            self._running.add(book)
            self._notices.pop(book, None)
            self._progress[book] = (0, 0)

        def on_progress(index: int, total: int, word: str) -> None:
            del word
            with self._lock:
                self._progress[book] = (index, total)

        def work() -> None:
            try:
                result = sync_book(book, on_progress=on_progress)
            except (Exception, SystemExit) as caught:
                result = SyncResult("error", 0, str(caught) or type(caught).__name__)

            def done() -> None:
                if not self._alive:
                    return
                with self._lock:
                    self._running.discard(book)
                    self._progress.pop(book, None)
                    if result.status == "rate_limit":
                        self._notices[book] = "Достигнут рейт лимит"
                    elif result.status == "error":
                        self._notices[book] = result.detail or "Ошибка"
                    else:
                        self._notices.pop(book, None)
                if self._screen == "books":
                    self._show_books()

            self._post(done)

        threading.Thread(target=work, daemon=True).start()
        self._post(self._show_books)

    def _syncing(self) -> bool:
        with self._lock:
            return bool(self._running)

    def _poll(self, epoch: int) -> None:
        if not self._alive or self._screen != "books" or epoch != self._epoch:
            return
        try:
            for book, var in self._status.items():
                var.set(self._row_status(book))
        except sqlite3.OperationalError:
            pass
        if self._alive and (self._syncing() or self._adding):
            self.root.after(300, lambda epoch=epoch: self._poll(epoch))

    def _open_learn(self, book: str) -> None:
        if self.user_id is None or not is_book(book):
            self._post(self._show_books)
            return
        self.lessons.begin(self.user_id, book, unknown_words(self.user_id, book))
        self._post(lambda book=book: self._show_learn(book))

    def _show_learn(self, book: str) -> None:
        if self.user_id is None:
            self._show_login()
            return
        self._screen = "learn"
        self._book = book
        self._epoch += 1
        self._clear()
        title = book_title(book)
        self._title(title)
        ttk.Label(self.shell, text=title, font=self.title_font).pack(anchor="w")
        ttk.Separator(self.shell, orient="horizontal").pack(fill="x", pady=(12, 0))
        word, index, total = self.lessons.current(self.user_id, book)
        bar = ttk.Frame(self.shell)
        back = ttk.Button(bar, text="К списку", command=lambda: self._post(self._show_books))
        back.pack(side="right")
        if word is not None:
            prev = ttk.Button(
                bar,
                text="← Назад",
                command=lambda: self._shift(-1),
                state="disabled" if index == 0 else "normal",
            )
            nxt = ttk.Button(
                bar,
                text="Дальше →",
                command=lambda: self._shift(1),
                state="disabled" if index >= total - 1 else "normal",
            )
            got = ttk.Button(bar, text="Выучил", command=self._mark_learned)
            prev.pack(side="left")
            nxt.pack(side="left", padx=(8, 0))
            got.pack(side="left", padx=(8, 0))
            self._bind_learn_keys(prev)
            self._bind_learn_keys(nxt)
            self._bind_learn_keys(got)
            self._bind_learn_keys(back)
        # Pack the bar before the expanding page so its height is reserved.
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        holder, body = self._scroll()
        holder.pack(fill="both", expand=True, pady=(12, 0))
        if word is None:
            ttk.Label(body, text="Незнакомых слов не осталось.").pack(anchor="w")
            self.shell.focus_set()
            return
        ttk.Label(body, text=word, font=self.word_font, wraplength=640).pack(anchor="w")
        ttk.Label(body, text=f"{index + 1} из {total}").pack(anchor="w", pady=(4, 0))
        bases, glosses = _learn_parts(word, language_of(book))
        self._lines(body, "Начальная форма", bases, "Начальной формы пока нет.")
        self._lines(body, "Определения", glosses, "Определения пока нет.")
        self._examples(body, sentences_of(book, word), word)
        self.shell.focus_set()
        self._fit_texts()

    def _lines(self, parent, title: str, lines: list[str], empty: str) -> None:
        ttk.Label(parent, text=title, font=self.section_font).pack(
            anchor="w", pady=(16, 4)
        )
        if not lines:
            ttk.Label(parent, text=empty, wraplength=640, justify="left").pack(anchor="w")
            return
        for line in lines:
            ttk.Label(
                parent, text=f"• {line}", wraplength=640, justify="left"
            ).pack(anchor="w", pady=1)

    def _examples(self, parent, sentences: list[str], word: str) -> None:
        ttk.Label(parent, text="Примеры", font=self.section_font).pack(
            anchor="w", pady=(16, 4)
        )
        text = tk.Text(
            parent,
            wrap="word",
            height=1,
            width=40,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=self.body_font,
            takefocus=False,
        )
        text.tag_configure("hit", font=self.bold_font)
        if not sentences:
            text.insert("1.0", "Примеров нет.")
        else:
            for index, sentence in enumerate(sentences):
                if index:
                    text.insert("end", "\n")
                text.insert("end", "• ")
                self._insert_marked(text, sentence, word)
        self._style_text(text)
        text.pack(anchor="w", fill="x")
        self._bind_learn_keys(text)
        self._bind_wheel(text)
        self._texts.append(text)

    def _insert_marked(self, text, sentence: str, word: str) -> None:
        last = 0
        for match in TOKEN.finditer(sentence):
            text.insert("end", sentence[last : match.start()])
            token = match.group()
            if token.lower() == word:
                text.insert("end", token, "hit")
            else:
                text.insert("end", token)
            last = match.end()
        text.insert("end", sentence[last:])

    def _style_text(self, text) -> None:
        ink = self._lookup("TLabel", "foreground")
        paper = self._surface()
        # Tk 9 Text dropped -disabledforeground. Passing it aborts the lesson
        # screen before the study buttons are built.
        allowed = set(text.configure())
        options = {}
        if "state" in allowed:
            options["state"] = "disabled"
        if paper and "background" in allowed:
            options["background"] = paper
        if ink and "foreground" in allowed:
            options["foreground"] = ink
        if ink and "disabledforeground" in allowed:
            options["disabledforeground"] = ink
        if options:
            text.configure(**options)

    def _mark_learned(self) -> None:
        if self.user_id is None or self._book is None:
            return
        word, _index, _total = self.lessons.current(self.user_id, self._book)
        if word is None:
            return
        language = language_of(self._book)
        mark_known(self.user_id, word, language)
        self.lessons.forget(self.user_id, word, language)
        self._schedule_learn()

    def _shift(self, step: int) -> None:
        if self._screen != "learn" or self.user_id is None or self._book is None:
            return
        if not self.lessons.move(self.user_id, self._book, step):
            return
        self._schedule_learn()

    def _schedule_learn(self) -> None:
        if self._learn_pending or self._book is None:
            return
        self._learn_pending = True
        book = self._book

        def go() -> None:
            self._learn_pending = False
            if not self._alive or self._screen != "learn" or self._book != book:
                return
            self._show_learn(book)

        self._post(go)

    def _on_left(self, event) -> str | None:
        return self._arrow(event, -1)

    def _on_right(self, event) -> str | None:
        return self._arrow(event, 1)

    def _arrow(self, event, step: int) -> str | None:
        if self._screen != "learn" or self._typing(event):
            return None
        self._shift(step)
        return "break"

    def _on_escape(self, _event) -> str | None:
        if self._screen != "learn":
            return None
        self._post(self._show_books)
        return "break"

    def _typing(self, event) -> bool:
        widget = event.widget
        if isinstance(widget, tk.Entry):
            return True
        if isinstance(widget, tk.Text) and str(widget.cget("state")) != "disabled":
            return True
        return False

    def _bind_learn_keys(self, widget) -> None:
        widget.bind("<Left>", self._on_left)
        widget.bind("<Right>", self._on_right)
        widget.bind("<KP_Left>", self._on_left)
        widget.bind("<KP_Right>", self._on_right)

    def _scroll(self):
        holder = ttk.Frame(self.shell)
        canvas = tk.Canvas(
            holder, highlightthickness=0, borderwidth=0, yscrollincrement=1
        )
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def fit_scroll(_event=None) -> None:
            box = canvas.bbox("all")
            if box is not None:
                canvas.configure(scrollregion=box)

        def fit_width(event) -> None:
            canvas.itemconfigure(window, width=event.width)
            self._fit_texts()

        body.bind("<Configure>", fit_scroll)
        canvas.bind("<Configure>", fit_width)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        paper = self._surface()
        if paper:
            try:
                canvas.configure(background=paper)
            except tk.TclError:
                pass
        self._wheel_target = canvas
        self._bind_wheel(canvas)
        return holder, body

    def _surface(self) -> str:
        return self._lookup("TFrame", "background") or str(self.root.cget("background") or "")

    def _lookup(self, style: str, option: str) -> str:
        try:
            return str(ttk.Style().lookup(style, option) or "")
        except tk.TclError:
            return ""

    def _bind_wheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._wheel)
        widget.bind("<Button-4>", self._wheel_up)
        widget.bind("<Button-5>", self._wheel_down)
        try:
            widget.bind("<TouchpadScroll>", self._touchpad)
        except tk.TclError:
            return

    def _wheel_pixels(self, event) -> int:
        # One wheel notch. yscrollincrement is 1, so this is a pixel count.
        if getattr(event, "num", None) == 4:
            return -48
        if getattr(event, "num", None) == 5:
            return 48
        if event.delta == 0:
            return 0
        if abs(event.delta) >= 120:
            return int(-event.delta / 120) * 48
        return -48 if event.delta > 0 else 48

    def _wheel(self, event):
        if self._wheel_target is None:
            return None
        pixels = self._wheel_pixels(event)
        if not pixels:
            return None
        self._wheel_target.yview_scroll(pixels, "units")
        return "break"

    def _wheel_up(self, event):
        return self._wheel(event)

    def _wheel_down(self, event):
        return self._wheel(event)

    def _touchpad(self, event):
        if self._wheel_target is None:
            return None
        # The scrollbar binding already moved the canvas.
        if event.widget.winfo_class() in ("Scrollbar", "TScrollbar"):
            return None
        try:
            _delta_x, delta_y = self.root.tk.splitlist(
                self.root.tk.call("tk::PreciseScrollDeltas", event.delta)
            )
            pixels = int(self.root.tk.call("tk::ScaleNum", -int(delta_y)))
        except (tk.TclError, ValueError):
            return None
        if pixels:
            self._wheel_target.yview_scroll(pixels, "units")
        return "break"

    def _fit_texts(self) -> None:
        for text in self._texts:
            if not text.winfo_exists():
                continue
            width = text.master.winfo_width()
            if width < 80:
                continue
            measure = self.body_font.measure("0") or 8
            text.configure(width=max(int(width / measure) - 2, 20))
            text.update_idletasks()
            try:
                counted = text.count("1.0", "end-1c", "displaylines")
            except tk.TclError:
                counted = None
            if counted:
                lines = max(int(counted[0]), 1)
            else:
                lines = max(int(str(text.index("end-1c")).split(".")[0]), 1)
            text.configure(height=lines)

    def _default_button(self, button) -> None:
        try:
            button.configure(default="active")
        except tk.TclError:
            return


def run() -> None:
    if tk is None:
        raise SystemExit(_tk_message())
    Desktop().run()
