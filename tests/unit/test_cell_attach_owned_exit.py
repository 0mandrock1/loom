"""Owned attach contexts finish before an error reaches the command boundary."""

import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("owner", ["lock", "mode"])
@pytest.mark.parametrize("secondary", ["RuntimeError", "SystemExit", "KeyboardInterrupt"])
def test_actual_owned_context_keeps_first_failure(owner, secondary):
    program = textwrap.dedent(
        """
        import argparse
        import asyncio
        import sys
        from unittest.mock import AsyncMock
        import asyncpg
        from server.cli.cell_attach import Run
        from server.cli import cell_attach_pg as apg
        from server.core import pg
        from server.settings import settings

        owner, kind = sys.argv[1:]
        original = ValueError('operation failed')
        secondary = {'RuntimeError': RuntimeError, 'SystemExit': SystemExit,
                     'KeyboardInterrupt': KeyboardInterrupt}[kind]('exit failed')
        events = []
        class Conn:
            def __init__(self):
                self.closed = False
                self.closes = 0
            def is_closed(self):
                return self.closed
            async def close(self, **kwargs):
                self.closes += 1
                events.append('close')
                self.closed = True
                if self.closes == 2:
                    raise secondary
            def terminate(self):
                self.closed = True
                events.append('abort')

        async def main():
            conn = Conn()
            async def raw(*args, **kwargs): return conn
            async def checked(*args): pass
            async def fail(*args): raise original
            asyncpg.connect = raw
            pg._check_deployment = checked
            pg.init_connection = checked
            settings.pg_dsn = 'postgresql://unused/test'
            router = AsyncMock()
            router.call.return_value = {'state': 'attaching'}
            run = Run(None, argparse.Namespace(cell='test', wait=0), router, lambda _: None)
            run.row = {'deployment_id': 'test'}
            run.mode = 'recording'
            run.confirm_lock = AsyncMock()
            run.ready_for_commit = AsyncMock(return_value=[])
            async def open_lock():
                run._lock_cm = pg.dedicated()
                run.lock_conn = await run._lock_cm.__aenter__()
            run.open_lock = open_lock
            run.read_own_row = AsyncMock(side_effect=original)
            apg._mode_transaction = fail
            try:
                await (run.run() if owner == 'lock' else run.commit_mode())
            except BaseException as error:
                assert error is original, (type(error).__name__, events)
                assert events.count('close') == 2, events
                await asyncio.sleep(0)
                print('original-after-owned-exit')
                return
            raise AssertionError('operation failure was swallowed')
        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program, owner, secondary],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "original-after-owned-exit" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_failure", [False, True])
async def test_mode_failure_context_receives_the_actual_exception(monkeypatch, factory_failure):
    import argparse
    from unittest.mock import AsyncMock

    from server.cli import cell_attach_pg as apg
    from server.cli.cell_attach import Run
    from server.core import pg

    original = ValueError("mode failed")
    observed = []
    conn = AsyncMock()
    conn.is_closed = lambda: False
    conn.terminate = lambda: None

    class Context:
        async def __aenter__(self):
            return conn

        def __aexit__(self, kind, error, traceback):
            observed.append((kind, error, traceback))
            if factory_failure:
                raise RuntimeError("exit construction failed")
            return AsyncMock(side_effect=RuntimeError("exit failed"))()

    monkeypatch.setattr(pg, "dedicated", lambda **_: Context())
    monkeypatch.setattr(apg, "commit_enforced", AsyncMock(side_effect=original))
    run = Run(None, argparse.Namespace(cell="test", wait=0), AsyncMock(), lambda _: None)
    run.row = {"deployment_id": "test"}
    run.mode = "recording"
    with pytest.raises(ValueError) as result:
        await run.commit_mode()
    assert result.value is original
    assert len(observed) == 1
    assert observed[0][0] is ValueError and observed[0][1] is original
    assert observed[0][2] is not None


@pytest.mark.asyncio
async def test_successful_mode_work_still_fails_when_required_close_fails(monkeypatch):
    import argparse
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from server.cli import cell_attach_pg as apg
    from server.cli.cell_attach import Run
    from server.core import pg

    original = OSError("public close failed")
    conn = AsyncMock()
    conn.is_closed = lambda: False
    conn.terminate = lambda: None
    conn.close.side_effect = original

    @asynccontextmanager
    async def dedicated(**_):
        yield conn

    monkeypatch.setattr(pg, "dedicated", dedicated)

    async def committed(*args):
        args[-1]("unknown")

    monkeypatch.setattr(apg, "commit_enforced", committed)
    run = Run(None, argparse.Namespace(cell="test", wait=0), AsyncMock(), lambda _: None)
    run.row = {"deployment_id": "test"}
    with pytest.raises(OSError) as result:
        await run.commit_mode()
    assert result.value is original
