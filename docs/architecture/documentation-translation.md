# Documentation translation requests

The translation generator splits prepared English text into bounded requests, then checks
the assembled translation before writing it. English remains the source of truth.

## Request planning

`english_requests` assigns heading anchors with one page-wide counter. It prefers safe
second-level heading cuts and packs oversized sections into source slices of at most
16 KiB. The slices reconstruct the anchored English exactly when joined with newlines.

The planner uses Markdown token source ranges for list items and table rows. Fenced code,
inline code, complete HTML elements and comments remain together. A table header stays
with its delimiter. An indivisible oversized unit refuses before the page's first model
call. Cost estimation uses the same request list as translation.

## Assembled validation

The assembled page passes the existing content invariants and table checks before its
output directory or file is created. Table checks use both the documentation site's
Markdown parser and CommonMark with its table extension: their container rules differ.
They retain table groups, rows, cells, alignment and whether a cell is empty. Site list,
admonition and code extensions participate in the check.

The parser packages are pinned in the translation requirements and development extra.
The site parser pin matches the documentation builder. A source-byte limit does not
guarantee that a translated reply fits the output-token limit. Truncated replies remain
failures, and structural checks do not establish translation quality or prose completeness.

## Invariants

- No source slice exceeds the bound; all source bytes and page-wide anchors survive assembly.
- An indivisible unit refuses before provider admission.
- Provider settings, output-token limits, retries and cumulative financial checks govern
  every request independently of how the page is split.
- Output is written only after assembled validation succeeds.

## Source entry points

- `scripts/i18n/translate.py`: `english_requests`, `request_chars`, `translate_page`.
- `scripts/i18n/chunks.py`: protected ranges, source cuts and table structure checks.
- `scripts/i18n/requirements.in`: translation runtime dependencies.
