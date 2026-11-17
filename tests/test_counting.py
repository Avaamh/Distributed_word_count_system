"""Correctness tests independent of any particular downloaded book edition."""

import tempfile
import unittest
from pathlib import Path

from wordcount.counting import (
    book_content, count_text, load_document, normalize_keyword,
)


class CountingTests(unittest.TestCase):
    def test_case_and_punctuation(self):
        self.assertEqual(count_text("Cat, cat! CAT?", "cAt"), 3)

    def test_substrings_do_not_match(self):
        self.assertEqual(count_text("cat cats concatenate bobcat", "cat"), 1)

    def test_straight_and_curly_apostrophes(self):
        self.assertEqual(count_text("Don't stop. Don\u2019t go. don", "DON\u2019T"), 2)
        self.assertEqual(count_text("don't don", "don"), 1)

    def test_hyphens_and_digits_separate_tokens(self):
        self.assertEqual(count_text("cat-like cat2 cat_cat", "cat"), 4)

    def test_missing_word_and_empty_text(self):
        self.assertEqual(count_text("A small document", "missing"), 0)
        self.assertEqual(count_text("", "cat"), 0)

    def test_keyword_whitespace(self):
        self.assertEqual(normalize_keyword("  CAT\n"), "cat")

    def test_invalid_keywords(self):
        for keyword in ("", " ", "two words", "cat!", "123", "cat-like", None, 7):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    normalize_keyword(keyword)

    def test_plain_text_is_preserved(self):
        self.assertEqual(book_content("Ordinary text\n"), "Ordinary text\n")

    def test_old_and_new_gutenberg_markers(self):
        for article in ("THIS", "THE"):
            with self.subTest(article=article):
                text = (
                    "\ufeffcat in header\r\n"
                    f"*** START OF {article} PROJECT GUTENBERG EBOOK EXAMPLE ***\r\n"
                    "Cat, cat!\r\n"
                    f"*** END OF {article} PROJECT GUTENBERG EBOOK EXAMPLE ***\r\n"
                    "cat in license"
                )
                self.assertEqual(count_text(book_content(text), "cat"), 2)

    def test_incomplete_and_reversed_markers_are_rejected(self):
        start = "*** START OF THE PROJECT GUTENBERG EBOOK EXAMPLE ***"
        end = "*** END OF THE PROJECT GUTENBERG EBOOK EXAMPLE ***"
        for text in (start + "\ncontent", "content\n" + end, end + "\n" + start):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    book_content(text)

    def test_document_ids_are_allowlisted(self):
        for document_id in ("../README.md", "/etc/passwd", "unknown", None, []):
            with self.subTest(document_id=document_id):
                with self.assertRaises(ValueError):
                    load_document(document_id)

    def test_loads_registered_utf8_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alice_in_wonderland.txt"
            path.write_text("\ufeffAlice, ALICE!", encoding="utf-8")
            self.assertEqual(count_text(load_document("alice", Path(directory)), "alice"), 2)


if __name__ == "__main__":
    unittest.main()
