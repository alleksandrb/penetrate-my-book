from src.languages.de import GERMAN
from src.languages.en import ENGLISH

_ALL = (ENGLISH, GERMAN)


def languages():
    return _ALL


def codes() -> set[str]:
    return {lang.code for lang in _ALL}


def get(code: str):
    for lang in _ALL:
        if lang.code == code:
            return lang
    raise SystemExit(f"unknown language: {code}")
