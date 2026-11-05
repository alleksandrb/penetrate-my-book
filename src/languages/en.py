from src.languages.base import Language


class English(Language):
    code = "en"
    label = "Английский"
    abbreviations = "Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|Prof"
    break_on_i = True

    def can_sync(self, word: str) -> bool:
        return "'" not in word and "-" not in word


ENGLISH = English()
