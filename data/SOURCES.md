# Sample text sources

The UTF-8 text files are downloaded from GITenberg's mirrors of Project Gutenberg editions. Keep the original notices inside the files. These third-party texts are not original project code.

| Local file | Work | Author | Mirror |
|---|---|---|---|
| `alice_in_wonderland.txt` | Alice's Adventures in Wonderland | Lewis Carroll | https://github.com/GITenberg/Alice-s-Adventures-in-Wonderland_11/blob/master/11-0.txt |
| `sherlock_holmes.txt` | The Adventures of Sherlock Holmes | Arthur Conan Doyle | https://github.com/GITenberg/The-Adventures-of-Sherlock-Holmes_1661/blob/master/1661.txt |
| `war_and_peace.txt` | War and Peace | Leo Tolstoy; translation by Louise and Aylmer Maude | https://github.com/GITenberg/War-and-Peace_2600/blob/master/2600-0.txt |

The counting module excludes the recognized Gutenberg start and end marker lines and everything outside them. Material inside the markers, including titles and contents, is included. The cache uses the SHA-256 of that extracted, decoded content plus the counting-rule version and normalized keyword.

For benchmark reproducibility, retain the downloaded edition and record its hashes with the run configuration. Do not silently replace these files between experiment configurations.
