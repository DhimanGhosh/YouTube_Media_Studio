"""Shared tolerant text keys for music search and corroborated recording matching."""

from __future__ import annotations

import re
import unicodedata


def clean_song_title(value: str, album_name: str = "") -> str:
    """Remove explicit quoted storefront release annotations, retaining version labels."""
    def remove_annotation(match: re.Match[str]) -> str:
        annotation_album = next(group for group in match.groups() if group is not None)
        if album_name and music_words(annotation_album) != music_words(album_name):
            return match.group(0)
        return " "
    cleaned = re.sub(
        r'''[\[(]\s*from\s+(?:"([^"]+)"|'([^']+)'|“([^”]+)”|‘([^’]+)’)\s*[\])]''',
        remove_annotation, value, flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*[\[(]\s*(male|female)(?:\s+version)?\s*[\])]\s*$",
        lambda match: f" - {match.group(1).title()}", cleaned, flags=re.IGNORECASE,
    )
    return " ".join(cleaned.split())


def music_words(value: str) -> tuple[str, ...]:
    """Fold accents, punctuation, and common doubled transliteration letters."""
    text = "".join(
        character for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    words = []
    for word in re.findall(r"[^\W_]+", text):
        word = re.sub(r"([aeiou])\1+", r"\1", word)
        if len(word) > 3:
            word = re.sub(r"([bcdfghjklmnpqrstvwxyz])\1+", r"\1", word)
        words.append(word)
    return tuple(words)


def one_edit_apart(left: str, right: str) -> bool:
    """Allow one insertion, deletion, substitution, or adjacent transposition."""
    if abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return True
    if len(left) == len(right):
        different = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        return len(different) == 1 or (
            len(different) == 2 and different[1] == different[0] + 1
            and left[different[0]] == right[different[1]]
            and left[different[1]] == right[different[0]]
        )
    shorter, longer = sorted((left, right), key=len)
    for index, character in enumerate(shorter):
        if character != longer[index]:
            return shorter[index:] == longer[index + 1:]
    return True


def matches_music_query(query: tuple[str, ...], text: str) -> bool:
    """Match all search words while keeping short words out of unrelated substrings."""
    available = music_words(text)
    for needle in query:
        if any(
            needle == word
            or (needle in word and (len(needle) > 2 or len(query) == 1))
            or (len(needle) >= 5 and len(word) >= 5 and not needle.isdigit()
                and not word.isdigit() and one_edit_apart(needle, word))
            for word in available
        ):
            continue
        return False
    return True
