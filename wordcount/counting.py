"""Counting rules for the registered English sample documents."""

import re
from pathlib import Path

RULE_VERSION = "english-v1"
DOCUMENTS = {
    "alice": "alice_in_wonderland.txt",
    "sherlock": "sherlock_holmes.txt",
    "war_and_peace": "war_and_peace.txt",
}
DATA_DIRECTORY = Path(__file__).resolve().parent.parent / "data"
TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
START = re.compile(
    r"^\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b[^\r\n]*",
    re.MULTILINE | re.IGNORECASE,
)
END = re.compile(
    r"^\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK\b[^\r\n]*",
    re.MULTILINE | re.IGNORECASE,
)


def normalize_keyword(keyword: str) -> str:
    """Accept one ASCII-letter token, optionally with internal apostrophes."""
    if not isinstance(keyword, str):
        raise ValueError("Keyword must be a string.")
    normalized = keyword.strip().replace("\u2019", "'")
    if TOKEN.fullmatch(normalized) is None:
        raise ValueError("Keyword must be one word with optional internal apostrophes.")
    return normalized.lower()


def book_content(text: str) -> str:
    """Remove recognized Gutenberg wrappers; leave ordinary text unchanged.

    Text between the markers may include a title or contents page. This is
    intentional: the markers define our reproducible counting boundary.
    Incomplete or reversed wrappers are rejected rather than silently counted.
    """
    text = text.lstrip("\ufeff")
    start = START.search(text)
    end = END.search(text)
    if start is None and end is None:
        return text
    if start is None or end is None or end.start() < start.end():
        raise ValueError("Document has incomplete or invalid Gutenberg markers.")
    return text[start.end():end.start()]


def count_text(text: str, keyword: str) -> int:
    """Count whole tokens after applying the documented normalization."""
    keyword = normalize_keyword(keyword)
    text = text.replace("\u2019", "'")
    return sum(match.group().lower() == keyword for match in TOKEN.finditer(text))


def load_document(document_id: str, data_directory: Path = DATA_DIRECTORY) -> str:
    """Resolve an allowlisted ID; never treat a client ID as a path."""
    if not isinstance(document_id, str) or document_id not in DOCUMENTS:
        raise ValueError("Unknown document ID. Choose alice, sherlock, or war_and_peace.")
    path = Path(data_directory) / DOCUMENTS[document_id]
    return book_content(path.read_text(encoding="utf-8-sig"))


def count_document(document_id: str, keyword: str) -> int:
    """Count a keyword in one of the registered documents."""
    keyword = normalize_keyword(keyword)
    return count_text(load_document(document_id), keyword)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Count a keyword in a registered book.")
    parser.add_argument("document", choices=DOCUMENTS)
    parser.add_argument("keyword")
    args = parser.parse_args()
    try:
        count = count_document(args.document, args.keyword)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"document={args.document} keyword={normalize_keyword(args.keyword)} count={count}")


if __name__ == "__main__":
    main()
