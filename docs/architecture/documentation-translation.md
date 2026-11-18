# Documentation translation requests

The translation generator splits prepared English text into bounded requests, then checks
the assembled translation before writing it. English remains the source of truth.

## Request planning

`english_requests` assigns heading anchors with one page-wide counter. It prefers safe
second-level heading cuts and packs oversized sections into source slices of at most
16 KiB. The slices reconstruct the anchored English exactly when joined with newlines.

The planner uses CommonMark token source ranges for list items, code blocks and table rows.
Fenced and indented code, inline code, complete HTML elements and comments remain together.
A table header stays with its delimiter. An indivisible oversized unit refuses before the
page's first model call. Cost estimation uses the same request list as translation.

The site's list extension accepts two-space continuations, including ordered items.
Its parser has no source maps, so an indentation scan protects those items alongside
the CommonMark ranges. Both dialects' items remain indivisible.

Some site-extension tables are indented code in CommonMark. The code range remains
indivisible, so an oversized block with that ambiguity refuses before provider admission.
Moving the table outside its extension container resolves the ambiguity in English.

## Assembled validation

The assembled page passes the existing content invariants and table checks before its
output directory or file is created. Table checks use both the documentation site's
Markdown parser and CommonMark with its table extension: their container rules differ.
They retain table groups, rows, cells, alignment and whether a cell is empty. Site list,
admonition and code extensions participate in the check.
Cell text is measured after serialization and raw HTML restoration so empty markup cannot
hide missing text and escaped literal tags remain text.

The parser packages are pinned in the translation requirements and development extra.
The site parser pin matches the documentation builder. A source-byte limit does not
guarantee that a translated reply fits the output-token limit. Truncated replies remain
failures, and structural checks do not establish translation quality or prose completeness.

## Source spelling contracts

Label validation distinguishes source spelling from ordinary descriptions. Source-specific
name declarations protect exact product and project spans. A filename label must match
its source link target; the conventional `README` label may omit the `.md` suffix.
Operation field declarations protect returned string vocabularies and one documented
field formula. A string-typed field whose whole description is quoted identifier
alternatives also declares literal values. Quotation, capitalization and filename-like
spelling alone grant no exemption.

These declarations require source evidence: package or project identities, explicit
terminology, real file targets, or the operation's wire contract. Changes to those source
declarations require checking the attestation and its focused regression cases. Ordinary
qualifiers around a protected span remain subject to label validation.

The existing site and CommonMark parsers supply table cells, including escaped and inline
code pipes. Each bounded request is checked in its complete English container, without
sending that surrounding text to the provider. Site-only cells without source maps are
checked at page assembly. Retries, assembly and stored-page checks share the same rule:
protected spans must retain their spelling, occurrence order and source label or field.
This is a finite source contract, not a language classifier or a semantic review.

## Invariants

- No source slice exceeds the bound; all source bytes and page-wide anchors survive assembly.
- An indivisible unit refuses before provider admission.
- Provider settings, output-token limits, retries and cumulative financial checks govern
  every request independently of how the page is split.
- Output is written only after assembled validation succeeds.

## Source entry points

- `scripts/i18n/translate.py`: `english_requests`, `request_chars`, `translate_page`.
- `scripts/i18n/chunks.py`: protected ranges, source cuts and parsed table cells.
- `scripts/i18n/context.py`: source spelling declarations and residual label projection.
- `scripts/i18n/requirements.in`: translation runtime dependencies.
