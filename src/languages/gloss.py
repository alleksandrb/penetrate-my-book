import json
import re

_OF = re.compile(r"\bof\s+([A-Za-z][A-Za-z'-]*)\s*$")
_STALE = {"obsolete", "archaic"}
_STALE_LEAD = re.compile(r"^\((?:obsolete|archaic)\b", re.IGNORECASE)


def _base(word: str, sense: dict) -> str:
    if "form of" in (sense.get("tags") or []):
        match = _OF.search(sense.get("definition") or "")
        if match:
            return match.group(1).lower()
    return word


def _stale(tags) -> bool:
    return any(str(tag).lower() in _STALE for tag in tags or [])


def _stale_sense(sense: dict) -> bool:
    if _stale(sense.get("tags")):
        return True
    return bool(_STALE_LEAD.match((sense.get("definition") or "").lstrip()))


def _collapse(rows: list[tuple]) -> list[tuple]:
    grouped: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for word, count, pos, base, meaning in rows:
        key = (word, count, pos, base)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(meaning)
    collapsed = []
    for key in order:
        meanings = grouped[key]
        if len(meanings) > 1:
            text = " ".join(
                f"{index}. {item}" for index, item in enumerate(meanings, start=1)
            )
        else:
            text = meanings[0]
        collapsed.append((*key, text))
    return collapsed


def glosses(word: str, response: str | None) -> list[tuple[str, str, str]]:
    if not response:
        return []
    entries = json.loads(response).get("entries") or []
    rows = []
    for entry in entries:
        if _stale(entry.get("tags")):
            continue
        pos = entry.get("partOfSpeech") or ""
        for sense in entry.get("senses") or []:
            if _stale_sense(sense):
                continue
            meaning = (sense.get("definition") or "").strip()
            if meaning:
                rows.append((word, 0, pos, _base(word, sense), meaning))
    return [
        (pos, base, meaning)
        for _word, _count, pos, base, meaning in _collapse(rows)
    ]
