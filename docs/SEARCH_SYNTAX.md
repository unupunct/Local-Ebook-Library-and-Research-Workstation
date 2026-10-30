# Search syntax

Research Mode and the global search box use SQLite FTS5 keyword search. It finds the words you type; it does **not** understand meaning or synonyms (see SEMANTIC_SEARCH.md).

| You type | Meaning |
|---|---|
| `soil compost` | both words anywhere in the same passage (implicit AND) |
| `"companion planting"` | the exact phrase |
| `tomato OR basil` | either word (`OR`, `AND`, `NOT` must be UPPER-CASE) |
| `mulch NOT clay`, `mulch -clay` | passages with mulch but without clay |
| `(tomato OR basil) AND "companion planting"` | grouping |
| `compost*` | prefix: compost, composting, compostable… |

* Case-insensitive. Diacritics are ignored: `grădină` finds `gradina` and vice versa (Romanian ș/ş/ț/ţ variants match too).
* A "passage" is one PDF page chunk or EPUB chapter chunk (about 3000 characters).
* `NOT` needs a positive term (`NOT clay` alone is rejected). `NOT` cannot be mixed into an `OR` group.
* Lower-case `and`, `or`, `not` are ordinary words.
* Everything typed is quoted before it reaches FTS5, so special characters can never change the structure of the query or the SQL. Syntax errors produce a message (unbalanced quote/parenthesis, dangling operator, nothing searchable).
* Filters (tag incl. child tags, author, genre, year, language, format, collection, source, folder, added date, index status, or a single book) combine with the query using AND.
