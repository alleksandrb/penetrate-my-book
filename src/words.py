import re
from collections import Counter

from config.parse import SENTENCE_LIMIT

# Letters, with an apostrophe or a hyphen inside. Digits ride along so a
# mixed token like "13-year-old" or "24th" can be dropped whole.
TOKEN = re.compile(r"(?<![\w])'?[^\W_]+(?:['-][^\W_]+)*'?(?![\w])", re.UNICODE)
_PAGE = re.compile(r"\d+")
_END = re.compile(r"""[.!?]+["'”’“„«»‹›)\]]*""")


def abbrev_pattern(abbreviations: str, break_on_i: bool) -> re.Pattern[str]:
    initial = r"(?!I\.)[A-Z]" if break_on_i else r"[A-Z]"
    return re.compile(rf"(?<![\w])(?:(?i:{abbreviations})|{initial})\.")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"-\n+", "-", text)
    kept = [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not _PAGE.fullmatch(line.strip())
    ]
    return " ".join(kept)


def _inside_abbrev(text: str, dot: int, pattern: re.Pattern[str]) -> bool:
    start = max(0, dot - 20)
    window = text[start : dot + 8]
    for match in pattern.finditer(window):
        if start + match.start() <= dot < start + match.end():
            return True
    return False


def _sentences(text: str, pattern: re.Pattern[str]) -> list[str]:
    parts = []
    start = 0
    for match in _END.finditer(text):
        punct = match.group()
        if punct.startswith(".") and not punct.startswith("..") and _inside_abbrev(
            text, match.start(), pattern
        ):
            continue
        sentence = text[start : match.end()].strip()
        if sentence:
            parts.append(sentence)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def read(
    text: str, abbreviations: str, quotes: str, break_on_i: bool = False
) -> tuple[dict[str, int], dict[str, list[str]]]:
    pattern = abbrev_pattern(abbreviations, break_on_i)
    quote_table = str.maketrans("", "", quotes)
    counts: Counter[str] = Counter()
    sentences: dict[str, list[str]] = {}
    for sentence in _sentences(normalize(text), pattern):
        plain = " ".join(sentence.translate(quote_table).split())
        for token in TOKEN.findall(sentence):
            if any(char.isdigit() for char in token):
                continue
            word = token.lower()
            counts[word] += 1
            if not plain:
                continue
            bucket = sentences.setdefault(word, [])
            if plain in bucket or len(bucket) >= SENTENCE_LIMIT:
                continue
            bucket.append(plain)
    return dict(counts), sentences
