"""Translation requests bound source bytes without breaking Markdown units."""

from collections import Counter

import pytest

from scripts.i18n import translate as t

MAXIMUM = 16_384


def assert_bounded(text: str) -> list[str]:
    requests = t.english_requests(text)
    assert "\n".join(requests) == t.anchor_headings(text, Counter())
    assert all(len(part.encode()) <= MAXIMUM for part in requests)
    return requests


def test_one_large_section_splits_at_complete_table_rows() -> None:
    header = "| Loom | Loom |\n| --- | --- |\n"
    text = "# Loom\n\n## Loom\n\n" + header
    text += "".join(f"| Loom {i} | `Loom` |\n" for i in range(1800))
    requests = assert_bounded(text)
    assert len(requests) > 2
    assert any(header in part for part in requests)
    assert all(not part.startswith("| --- |") for part in requests)


def test_a_short_multibyte_page_still_obeys_the_byte_bound() -> None:
    text = "# Loom\n\n" + ("메모리" * 400 + "\n\n") * 6
    assert len(text) < t.WHOLE_PAGE_CHARS
    assert len(assert_bounded(text)) > 1


def test_slices_keep_code_html_and_anchor_heading_pairs_together() -> None:
    fence = "```python\nvalue = 12\n\n# Loom\n```"
    inline = "`one\n\ntwo`"
    html = '<details title="a>\n\nb"><summary>Loom</summary>\n\nLoom\n</details>'
    comment = "<!-- Loom\n\nLoom -->"
    blocks = [fence, inline, html, comment, "## Next"]
    text = "# Loom\n\n" + "\n\n".join(("Loom " * 900 + "\n\n" + block) for block in blocks)
    requests = assert_bounded(text)
    assert len(requests) > 1
    for block in blocks[:-1]:
        assert any(block in part for part in requests)
    assert any('<a id="next"></a>\n\n## Next' in part for part in requests)


def test_multiline_list_continuation_never_becomes_a_separate_request() -> None:
    item = "- Loom " + "x" * 4000 + "\n\n  continued " + "y" * 4000 + "\n"
    text = "# Loom\n\n" + "Loom\n\n" * 1800 + item + "\n- next\n"
    requests = assert_bounded(text)
    assert any(item.rstrip("\n") in part for part in requests)


def test_html_crossing_a_heading_stays_in_one_request() -> None:
    html = "<details>\n\n## Loom\n\nLoom\n</details>\n"
    text = "# Loom\n\n" + "Loom\n\n" * 3000 + html
    requests = assert_bounded(text)
    assert any(t.anchor_headings(html, Counter({"loom": 1})) in part for part in requests)


def test_table_without_outer_pipes_splits_between_complete_rows() -> None:
    header = "Loom | Loom\n--- | ---\n"
    text = header + "Loom | Loom\n" * 2000
    requests = assert_bounded(text)
    assert len(requests) > 1
    assert requests[0].startswith(header)


@pytest.mark.parametrize(
    "unit",
    [
        "```\n" + "x" * MAXIMUM + "\n```\n",
        "<details>\n" + "x" * MAXIMUM + "\n</details>\n",
        "- Loom " + "x" * 9000 + "\n\n  continued " + "y" * 9000 + "\n",
        "- " + "x" * 9000 + "\n\n\t" + "y" * 9000 + "\n",
    ],
    ids=["fence", "html", "list", "tab-list"],
)
def test_indivisible_oversize_input_stops_before_any_provider_call(monkeypatch, unit) -> None:
    calls = []

    def unexpected_call(*args):
        calls.append(args)
        raise AssertionError("indivisible oversized input reached the provider")

    monkeypatch.setattr(t, "prepare", lambda source, lang: (unit, ""))
    monkeypatch.setattr(t, "call", unexpected_call)
    with pytest.raises(t.TranslationDiffers, match="indivisible"):
        t.translate_page("docs/example.md", t.LANGUAGES[0], None, t.Spend(t.DEFAULT_MODEL, 1))
    assert calls == []


def test_request_estimate_and_calls_use_the_same_slices_and_usage(monkeypatch) -> None:
    text = "# Loom\n\n" + "Loom\n\n" * 6000
    calls = []

    def reply(client, model, system, messages):
        source = messages[0]["content"]
        assert len(source.encode()) <= MAXIMUM
        calls.append(source)
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": source}],
            "usage": {"input_tokens": 7, "output_tokens": 11},
        }

    monkeypatch.setattr(t, "call", reply)
    monkeypatch.setattr(t, "prepare", lambda source, lang: (text, ""))
    lang = t.LANGUAGES[0]
    spend = t.Spend(t.DEFAULT_MODEL, 1)
    requests = assert_bounded(text)
    result = "\n".join(t.translate_text(part, lang, None, spend) for part in requests)
    assert result == "\n".join(requests)
    assert calls == requests
    assert spend.input_tokens == 7 * len(requests)
    assert spend.output_tokens == 11 * len(requests)
    system = t.SYSTEM.format(language=lang.english, style=lang.style)
    assert t.request_chars("docs/example.md", lang) == sum(len(p) + len(system) for p in calls)


def test_assembled_table_layout_is_verified_before_writing(monkeypatch, tmp_path) -> None:
    source = "# Loom\n\n| Loom | Loom |\n| --- | --- |\n| Loom | Loom |\n"
    monkeypatch.setattr(t, "ROOT", tmp_path)
    monkeypatch.setattr(t, "prepare", lambda path, lang: (source, ""))
    monkeypatch.setattr(t, "blob", lambda path: "0" * 40)
    monkeypatch.setattr(
        t, "translate_text", lambda part, *args: part + "\n| Loom | Loom | Loom |\n"
    )
    with pytest.raises(t.TranslationDiffers, match="table"):
        t.translate_page("docs/example.md", t.LANGUAGES[0], None, t.Spend(t.DEFAULT_MODEL, 1))
    assert not (tmp_path / "i18n").exists()


def test_assembled_table_without_outer_pipes_rejects_row_loss(monkeypatch, tmp_path) -> None:
    source = "Loom | Loom\n--- | ---\nLoom | Loom\n"
    monkeypatch.setattr(t, "ROOT", tmp_path)
    monkeypatch.setattr(t, "prepare", lambda path, lang: (source, ""))
    monkeypatch.setattr(t, "blob", lambda path: "0" * 40)
    monkeypatch.setattr(t, "translate_text", lambda *args: "Loom | Loom\n--- | ---\n")
    with pytest.raises(t.TranslationDiffers, match="table"):
        t.translate_page("docs/example.md", t.LANGUAGES[0], None, t.Spend(t.DEFAULT_MODEL, 1))
    assert not (tmp_path / "i18n").exists()
