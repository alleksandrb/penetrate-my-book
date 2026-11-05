from src.languages.base import Language


class German(Language):
    code = "de"
    label = "Немецкий"
    abbreviations = r"Dr|Prof|bzw|usw|ca|Nr|z\.B|d\.h|u\.a"
    quotes = "\"“”„«»‹›"

    def can_sync(self, word: str) -> bool:
        return "'" not in word and "-" not in word


GERMAN = German()
