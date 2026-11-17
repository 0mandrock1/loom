"""Data-only validation, pending output reuse, and dependency-safe retirement."""

from __future__ import annotations

import posixpath
import re
import subprocess
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

from scripts.i18n import translate as t

MAX_PAGE_BYTES = 16 * 1024 * 1024


def source_path(source: str) -> bool:
    parts = PurePosixPath(source).parts
    return (
        bool(parts)
        and not re.search(r"[\x00-\x1f\x7f\\]", source)
        and not source.startswith("/")
        and ".." not in parts
        and str(PurePosixPath(source)) == source
        and (source == "README.md" or (parts[0] == "docs" and source.endswith(".md")))
    )


def all_files() -> list[str]:
    return sorted(
        p.relative_to(t.ROOT).as_posix()
        for p in (t.ROOT / t.OUT_DIR).rglob("*")
        if p.is_file() or p.is_symlink()
    )


def publication_path(path: str) -> str:
    return path.replace("i18n/.pending/", "i18n/", 1) if path.startswith("i18n/.pending/") else path


def checkpoint_path(path: str) -> str:
    return "i18n/.pending/" + path.removeprefix("i18n/")


def output_files() -> list[str]:
    return [p for p in all_files() if not p.startswith("i18n/.pending/")]


def checkpoint_files() -> list[str]:
    return [p for p in all_files() if p.startswith("i18n/.pending/")]


def regular(path: str) -> bool:
    file = t.ROOT / path
    return (
        file.resolve().is_relative_to(t.ROOT.resolve())
        and file.is_file()
        and not any(p.is_symlink() for p in (file, *file.parents))
    )


def owner(path: str, text: str) -> tuple[str, t.Language] | None:
    path = publication_path(path)
    stamp = t.STAMP.match(text)
    if not stamp or not source_path(stamp[1]):
        return None
    for lang in t.LANGUAGES:
        if path == t.target_path(lang.code, stamp[1]):
            return stamp[1], lang
    return None


def strip_language_line(text: str) -> str:
    return t.LANGUAGE_LINE.sub("<!-- languages -->", text)


def canonical_links(text: str, current: str) -> str:
    """Compare translated and English fallback links as the same source target."""

    def fix(prose: str) -> str:
        def replace(match: re.Match[str]) -> str:
            prefix, link = match.groups()
            if re.match(r"[a-z]+:|#|/", link):
                return match[0]
            path, _, anchor = link.partition("#")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(current), unquote(path)))
            parts = resolved.split("/", 2)
            if (
                len(parts) == 3
                and parts[0] == t.OUT_DIR
                and parts[1] in {lg.code for lg in t.LANGUAGES}
            ):
                resolved = parts[2]
            image = prefix == 'src="' or (prefix == "](" and t._is_image(prose, match.start()))
            suffix = f"#{anchor}" if anchor else ""
            return (
                f"{prefix}{t.REPO_URL}/{'raw' if image else 'blob'}/main/{quote(resolved)}{suffix}"
            )

        return t.LINK.sub(replace, prose)

    return t.in_prose(text, fix)


def candidate_problems(path: str, text: str, *, current: bool = True) -> list[str]:
    path = publication_path(path)
    owned = owner(path, text)
    if owned is None:
        return [f"{path}: not a generated page with an exact source/language mapping"]
    source, lang = owned
    stamp = t.STAMP.match(text)
    assert stamp
    model = t.os.environ.get("LOOM_I18N_MODEL", t.DEFAULT_MODEL)
    if current and (source not in t.sources() or stamp[2] != t.blob(source) or stamp[3] != model):
        return [f"{path}: pending output is stale"]
    expected_header = t.header(source, lang, stamp[3], stamp[2])
    if not text.startswith(expected_header):
        return [f"{path}: stamp or banner differs"]
    if not current:
        return []
    english = strip_language_line((t.ROOT / source).read_text())
    english = "\n".join(t.english_requests(english))
    body = strip_language_line(text[len(expected_header) :])
    english = canonical_links(english, source)
    body = canonical_links(body, path)
    problems = t.differences(english, body)
    return [f"{path}: {problem}" for problem in problems]


def link_failures(extra: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    pages = {
        p: (t.ROOT / p).read_text() for p in output_files() if regular(p) and p.endswith(".md")
    }
    pages.update(extra or {})
    problems = []
    for path, text in pages.items():
        for match in t.prose_links(text):
            link, _, anchor = match[2].partition("#")
            if re.match(r"[a-z]+:", link):
                continue
            target = (
                posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(link)))
                if link
                else path
            )
            if target not in pages and not regular(target):
                problems.append((path, target, f"{path} links to {match[2]}, which does not exist"))
            elif anchor:
                body = pages[target] if target in pages else (t.ROOT / target).read_text()
                if f'<a id="{unquote(anchor)}"></a>' not in body:
                    problems.append(
                        (path, target, f"{path} links to {match[2]}, which has no such anchor")
                    )
    return problems


def link_problems(extra: dict[str, str] | None = None) -> list[str]:
    return [problem for _, _, problem in link_failures(extra)]


def require_owned_outputs() -> None:
    problems = []
    for path in all_files():
        if not regular(path):
            problems.append(f"{path}: refusing nonregular output")
        elif owner(path, (t.ROOT / path).read_text()) is None:
            problems.append(f"{path}: refusing output without generator ownership")
    if problems:
        raise SystemExit("\n".join(problems))


def present_problems() -> list[str]:
    problems = []
    inventory = frozenset(t.sources())
    model = t.os.environ.get("LOOM_I18N_MODEL", t.DEFAULT_MODEL)
    for path in all_files():
        if not regular(path) or not path.endswith(".md"):
            problems.append(f"{path}: not a regular generated Markdown page")
            continue
        text = (t.ROOT / path).read_text()
        stamp = t.STAMP.match(text)
        current = bool(
            stamp and stamp[1] in inventory and stamp[2] == t.blob(stamp[1]) and stamp[3] == model
        )
        problems += candidate_problems(path, text, current=current)
    problems += link_problems()
    problems += t.untranslated_pages()
    problems += [
        f"{path}'s language line differs from the generated one"
        for path in t.language_lines_differ()
    ]
    return problems


def retire_obsolete() -> tuple[int, int]:
    """Remove only owned obsolete pages with no inbound dependency from retained output."""
    obsolete = t.orphans()
    candidates = {p for p in obsolete if regular(p) and owner(p, (t.ROOT / p).read_text())}
    inbound: dict[str, set[str]] = {p: set() for p in candidates}
    for path in output_files():
        if not regular(path) or not path.endswith(".md"):
            continue
        for match in t.prose_links((t.ROOT / path).read_text()):
            link = match[2].partition("#")[0]
            if re.match(r"[a-z]+:|/", link):
                continue
            target = (
                posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(link)))
                if link
                else path
            )
            if target in inbound:
                inbound[target].add(path)
    while blocked := {p for p in candidates if inbound[p] - candidates}:
        candidates -= blocked
    for path in sorted(candidates):
        (t.ROOT / path).unlink()
    return len(candidates), len(obsolete) - len(candidates)


def current_checkpoints() -> dict[str, str]:
    ready = {}
    for path in checkpoint_files():
        if regular(path):
            text = (t.ROOT / path).read_text()
            if not candidate_problems(path, text):
                ready[publication_path(path)] = text
    return ready


def stage(path: str, text: str) -> None:
    problems = candidate_problems(path, text)
    if problems:
        raise t.TranslationDiffers("; ".join(problems))
    destination = t.ROOT / checkpoint_path(publication_path(path))
    if any(parent.is_symlink() for parent in (destination, *destination.parents)):
        raise SystemExit(f"{path}: refusing symlink destination")
    if (
        destination.exists()
        and owner(destination.relative_to(t.ROOT).as_posix(), destination.read_text()) is None
    ):
        raise SystemExit(f"{path}: refusing checkpoint without generator ownership")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)


def activate_ready() -> int:
    """Install an overlay only when all present inbound and outbound links remain valid."""
    require_owned_outputs()
    ready = current_checkpoints()
    while ready:
        blocked = set()
        for source, target, _ in link_failures(ready):
            if target in ready and target != source:
                blocked.add(target)
            elif source in ready:
                blocked.add(source)
        if not blocked:
            break
        for path in blocked:
            del ready[path]
    for path, text in ready.items():
        destination = t.ROOT / path
        if any(parent.is_symlink() for parent in (destination, *destination.parents)):
            raise SystemExit(f"{path}: refusing symlink destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
        (t.ROOT / checkpoint_path(path)).unlink()
    for path in checkpoint_files():
        if regular(path):
            text = (t.ROOT / path).read_text()
            if owner(path, text) and candidate_problems(path, text):
                (t.ROOT / path).unlink()
    print(f"published={len(ready)} checkpointed={len(checkpoint_files())}")
    return len(ready)


def import_pending(ref: str) -> int:
    """Read only blob objects, never branch code; current main output wins."""
    require_owned_outputs()
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise SystemExit("pending ref must be an exact commit SHA")
    tree = subprocess.run(
        ["git", "ls-tree", "-rz", ref, "--", t.OUT_DIR], cwd=t.ROOT, check=True, capture_output=True
    ).stdout
    proposed: dict[str, str] = {}
    model = t.os.environ.get("LOOM_I18N_MODEL", t.DEFAULT_MODEL)
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, path_bytes = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = path_bytes.decode()
        if mode != "100644" or kind != "blob":
            raise SystemExit(f"{path}: pending output is not a regular blob")
        data = subprocess.run(
            ["git", "cat-file", "blob", oid], cwd=t.ROOT, check=True, capture_output=True
        ).stdout
        if len(data) > MAX_PAGE_BYTES:
            raise SystemExit(f"{path}: pending page exceeds size limit")
        text = data.decode()
        owned = owner(path, text)
        if owned is None:
            raise SystemExit(f"{path}: refusing unknown pending output")
        source, lang = owned
        path = publication_path(path)
        if source not in t.sources():
            continue
        if regular(path) and (t.ROOT / path).read_text().startswith(
            t.header(source, lang, model, t.blob(source))
        ):
            continue
        problems = candidate_problems(path, text)
        if problems:
            print("pending output rejected: " + "; ".join(problems))
        else:
            proposed[path] = text
    for path, text in proposed.items():
        stage(path, text)
    activate_ready()
    print(f"reused={len(proposed)}")
    return len(proposed)
