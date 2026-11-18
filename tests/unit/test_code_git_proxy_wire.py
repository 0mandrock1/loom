"""What git itself does with the proxy settings Loom hands it, run with the real binary
against a forward proxy the test starts: the first request carries the identity and the
password, the repository's own token stays inside the tunnel, and nothing is stored."""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from server.code.app.index import git_proxy, gitrepo, sandbox
from server.code.app.index.git_proxy import NetworkGit
from server.code.domain.index import tier_health
from server.settings import settings
from tests.unit.git_repo import git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

IDENTITY = "acme.widgets-indexer"
TOKEN = "tok/en+with:odd chars%@"
ENCODED = quote(TOKEN, safe="")
BASIC = base64.b64encode(f"{IDENTITY}:{TOKEN}".encode()).decode()
# git never resolves the name: with a proxy it asks the proxy to connect.
REMOTE = "https://git.invalid/acme/widgets.git"


class _RefusingProxy:
    """A forward proxy on a free loopback port that records each request head and
    answers it with `status`."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.heads: list[list[str]] = []
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> _RefusingProxy:
        self._server = await asyncio.start_server(self._answer, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        self.heads.append(head.decode("latin-1").split("\r\n"))
        challenge = 'Proxy-Authenticate: Basic realm="proxy"\r\n' if self.status == 407 else ""
        answer = f"HTTP/1.1 {self.status} Refused\r\n{challenge}Content-Length: 0\r\n\r\n"
        writer.write(answer.encode())
        await writer.drain()
        writer.close()


def _set_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str) -> None:
    token_file = tmp_path / "proxy-token"
    token_file.write_text(TOKEN + "\n")
    monkeypatch.setattr(settings, "code_git_proxy_url", url)
    monkeypatch.setattr(settings, "code_git_proxy_token_file", str(token_file))


@pytest.mark.parametrize("status", [403, 407])
async def test_git_authenticates_to_the_proxy_on_its_first_request(
    status: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mirror = tmp_path / "mirror.git"
    git(tmp_path, "init", "--bare", "--quiet", str(mirror))
    started: list[tuple[str, ...]] = []
    spawn = asyncio.create_subprocess_exec

    async def _recorded(*argv: str, **kw: Any) -> asyncio.subprocess.Process:
        started.append(argv)
        return await spawn(*argv, **kw)

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", _recorded)
    async with _RefusingProxy(status) as proxy:
        _set_proxy(monkeypatch, tmp_path, proxy.url)
        with pytest.raises(gitrepo.GitError) as failed:
            await gitrepo.fetch(
                str(mirror), REMOTE, "main", token="repo-token", proxy_identity=IDENTITY
            )
    assert proxy.heads, "git made no request to the proxy"
    for head in proxy.heads:
        assert head[0] == "CONNECT git.invalid:443 HTTP/1.1"
        (credentials,) = [h for h in head if h.lower().startswith("proxy-authorization:")]
        scheme, pair = credentials.split(":", 1)[1].split()
        assert scheme == "Basic" and base64.b64decode(pair).decode() == f"{IDENTITY}:{TOKEN}"
        # the repository's token is for the remote, inside the tunnel
        assert not [h for h in head if h.lower().startswith("authorization:")]
    (argv,) = started
    assert not [arg for arg in argv if TOKEN in arg or ENCODED in arg or BASIC in arg]
    error = str(failed.value)
    assert TOKEN not in error and ENCODED not in error and BASIC not in error
    assert tier_health.classify(error) == "egress"
    assert "proxy" not in (mirror / "config").read_text().lower()


async def test_a_clone_made_with_the_proxy_settings_stores_none_of_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--quiet")
    (source / "a.txt").write_text("a\n")
    git(source, "add", "a.txt")
    git(source, "commit", "--quiet", "-m", "one")
    _set_proxy(monkeypatch, tmp_path, "http://proxy.example:3128")
    # A local source needs no proxy: the settings ride along unused, as on a network clone.
    env, _secrets = await git_proxy.job_env(
        gitrepo.auth_env("", str(source)), NetworkGit(identity=IDENTITY)
    )
    clone = tmp_path / "clone.git"
    argv = ["git", "clone", "--bare", "--quiet", *gitrepo.upload_pack_args(str(source))]
    done = await asyncio.to_thread(
        subprocess.run, [*argv, str(source), str(clone)], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    kept = b"".join(p.read_bytes() for p in clone.rglob("*") if p.is_file())
    assert (clone / "config").is_file()
    for secret in (TOKEN, ENCODED, "proxy.example"):
        assert secret.encode() not in kept
