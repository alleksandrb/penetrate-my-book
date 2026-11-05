from src.languages.gloss import glosses
from src.words import read


class Language:
    code = ""
    label = ""
    abbreviations = ""
    quotes = "\"“”«»"
    break_on_i = False

    def read(self, text: str):
        return read(text, self.abbreviations, self.quotes, self.break_on_i)

    def can_sync(self, word: str) -> bool:
        raise NotImplementedError

    def definitions(self, word: str, response: str | None):
        return glosses(word, response)
