import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from src.store import save_api_response

API = "https://freedictionaryapi.com/api/v1/entries/{language}/{word}?translations=false"
# Cloudflare rejects urllib's default User-Agent (error 1010).
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "penetrate-my-book/1.0",
}
_WRITE = threading.Lock()


class RateLimit(Exception):
    pass


def fetch_entry(word: str, language: str) -> str:
    url = API.format(
        language=urllib.parse.quote(language, safe=""),
        word=urllib.parse.quote(word, safe=""),
    )
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 429:
            error.read()
            raise RateLimit
        if error.code == 404:
            return error.read().decode("utf-8") or json.dumps({"error": "not found"})
        error.read()
        raise


def save_entry(word: str, body: str, language: str) -> None:
    with _WRITE:
        save_api_response(word, body, language)
