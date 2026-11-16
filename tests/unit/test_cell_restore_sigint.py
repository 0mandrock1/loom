"""The public CLI names an unknown outcome after the real Runner SIGINT boundary."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import textwrap


async def test_sigint_prints_retry_diagnostic_before_exiting() -> None:
    script = textwrap.dedent("""
        import argparse
        import asyncio
        import httpx
        from server.cli import cell_restore
        from server.core import ch

        async def no_io(*args, **kwargs):
            pass

        async def work(*args, **kwargs):
            print('RESTORE_READY', flush=True)
            await asyncio.Future()

        cell_restore._args_problem = lambda args: ''
        cell_restore.cell_cli.operator_router = lambda args: httpx.URL('https://router.test')
        cell_restore._connect = no_io
        cell_restore.disconnect = no_io
        cell_restore.Restore.run = work
        ch.get_client = lambda: object()
        cell_restore.cmd_restore(argparse.Namespace(cell='first', wait=10))
    """)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        env={**os.environ, "LOOM_ENV_FILE": ""},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"RESTORE_READY\n"
        process.send_signal(signal.SIGINT)
        _, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode != 0
        assert b"outcome may be unknown" in stderr
        assert b"rerun the same command before starting the cell" in stderr
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
