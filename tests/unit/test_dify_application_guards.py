"""Pure guard regressions; no runtime, Docker, network or real signal operation."""

import signal
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipInfo

import httpx

from scripts.gates import dify_application_scenario as scenario
from scripts.gates import dify_application_smoke as smoke
from tests.unit.clock_fake import ManualClock


class DifyApplicationGuards(unittest.TestCase):
    def test_starting_fixture_can_reset_before_healthy_within_same_budget(self):
        client = Mock()
        client.get.side_effect = [
            httpx.ReadError("reset"),
            httpx.Response(503),
            httpx.Response(200),
        ]
        clock = ManualClock()
        result = smoke.wait_fixture_ready(
            client, "http://127.0.0.1/ping", 3, now=clock.monotonic, pause=clock.sleep
        )
        self.assertEqual(result, [{"error_type": "ReadError"}, {"status": 503}, {"status": 200}])
        self.assertEqual(clock.t, 2)
        self.assertEqual(client.get.call_count, 3)

    def test_persistent_fixture_reset_exhausts_original_readiness_budget(self):
        client = Mock()
        client.get.side_effect = httpx.ReadError("reset")
        clock = ManualClock()
        with self.assertRaisesRegex(RuntimeError, "readiness failed.*ReadError"):
            smoke.wait_fixture_ready(
                client, "http://127.0.0.1/ping", 2, now=clock.monotonic, pause=clock.sleep
            )
        self.assertEqual(clock.t, 2)
        self.assertEqual(client.get.call_count, 2)

    def test_fixture_permission_refusal_is_terminal_without_another_poll(self):
        client = Mock()
        client.get.return_value = httpx.Response(403)
        clock = ManualClock()
        with self.assertRaisesRegex(RuntimeError, "permission refused"):
            smoke.wait_fixture_ready(
                client, "http://127.0.0.1/ping", 2, now=clock.monotonic, pause=clock.sleep
            )
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(clock.t, 0)

    def test_credentials_stay_in_memory_and_are_redacted_without_stdout(self):
        runtime = smoke.Runtime.__new__(smoke.Runtime)
        runtime.secrets = []
        with patch("builtins.print") as output:
            self.assertEqual(runtime.secret("test-credential"), "test-credential")
        output.assert_not_called()
        self.assertEqual(runtime.redact("value=test-credential"), "value=[REDACTED]")

    def test_uncovered_image_volume_gets_explicit_owned_compose_volume(self):
        service = {"volumes": [{"target": "/var/lib/postgresql/data/pgdata"}]}
        image = {"Config": {"Volumes": {"/var/lib/postgresql/data": {}}}}
        mounts, volumes = smoke.image_volume_mounts("db_postgres", service, image)
        self.assertEqual(
            mounts,
            [
                {
                    "type": "volume",
                    "source": "image-db_postgres-0",
                    "target": "/var/lib/postgresql/data",
                }
            ],
        )
        self.assertEqual(volumes, {"image-db_postgres-0": {}})
        self.assertEqual(service["volumes"], [{"target": "/var/lib/postgresql/data/pgdata"}])

    def test_covered_image_volume_needs_no_extra_mount(self):
        service = {"volumes": [{"target": "/data"}]}
        image = {"Config": {"Volumes": {"/data": {}}}}
        self.assertEqual(smoke.image_volume_mounts("redis", service, image), ([], {}))

    def test_invalid_image_volume_target_refuses(self):
        for target in ("/", "relative", "/data/../foreign"):
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(RuntimeError, "target is invalid"),
            ):
                smoke.image_volume_mounts("service", {}, {"Config": {"Volumes": {target: {}}}})

    def test_init_password_respects_pinned_schema(self):
        for _ in range(8):
            password = smoke.initial_password()
            self.assertEqual(len(password), 27)
            self.assertTrue(password.startswith("Aa7"))

    def test_remote_current_context_refuses_before_bound_operation(self):
        resolve = Mock(return_value="ssh://remote-daemon")
        with self.assertRaisesRegex(RuntimeError, "Effective Docker context"):
            smoke.verify_docker_selection({}, resolve)
        resolve.assert_called_once_with()

    def test_docker_selector_overrides_refuse_before_context_lookup(self):
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"):
            with self.subTest(name=name):
                resolve = Mock()
                with self.assertRaisesRegex(RuntimeError, "overrides"):
                    smoke.verify_docker_selection({name: "foreign"}, resolve)
                resolve.assert_not_called()

    def test_verified_endpoint_is_bound_to_every_docker_operation(self):
        endpoint = smoke.verify_docker_selection({}, lambda: smoke.LOCAL_DOCKER)
        for command in (["container", "inspect", "owned"], ["container", "rm", "owned"]):
            self.assertEqual(
                smoke.docker_arguments(command, endpoint),
                ["docker", "--host", smoke.LOCAL_DOCKER, *command],
            )
        with self.assertRaisesRegex(RuntimeError, "unverified"):
            smoke.docker_arguments(["container", "rm", "owned"], "ssh://remote")

    def test_same_archive_count_cannot_replace_license_with_excluded_test(self):
        members = [ZipInfo(name) for name in smoke.EXPECTED_PLUGIN_FILES if name != "LICENSE"]
        members.append(ZipInfo("tests/native_concurrency_case.py"))
        self.assertEqual(len(members), 19)
        with self.assertRaisesRegex(RuntimeError, "reviewed set"):
            smoke.validate_archive_members(members)

    def test_archive_rejects_duplicate_and_directory_members(self):
        valid = [ZipInfo(name) for name in smoke.EXPECTED_PLUGIN_FILES]
        smoke.validate_archive_members(valid)
        for extra in (ZipInfo("LICENSE"), ZipInfo("tools/")):
            with self.subTest(extra=extra.filename), self.assertRaises(RuntimeError):
                smoke.validate_archive_members([*valid, extra])

    def test_native_graph_keeps_application_user_in_form_configuration(self):
        graph = scenario.build_graph(
            {"start": {}, "end": {}},
            {"id": "discovered-provider", "name": "loom"},
            "recall",
            "application-user",
            {"query": "aisle seats"},
        )
        tool = next(node["data"] for node in graph["nodes"] if node["id"] == "tool")
        self.assertEqual(tool["provider_id"], "discovered-provider")
        self.assertEqual(
            tool["tool_configurations"]["user_id"],
            {"type": "constant", "value": "application-user"},
        )
        self.assertNotIn("user_id", tool["tool_parameters"])
        self.assertEqual(tool["tool_parameters"]["query"]["value"], "aisle seats")
        self.assertEqual(tool["tool_configurations"]["top_k"]["value"], 5)

    @staticmethod
    def saved_session():
        return {"pid": 41, "start": 8, "members": {41: (8, 41, 41, 1000)}}

    def stop_mocked(self, saved, snapshot, send):
        smoke.stop_owned_session(
            saved,
            observe=lambda _: dict(snapshot),
            current_birth=lambda pid: snapshot.get(pid, ())[:3],
            send=send,
            pause=Mock(side_effect=AssertionError("Unexpected wait")),
            now=lambda: 0,
            uid=1000,
        )

    def test_reused_session_leader_is_never_signalled(self):
        send = Mock()
        with self.assertRaisesRegex(RuntimeError, "leader generation"):
            self.stop_mocked(self.saved_session(), {41: (99, 41, 41, 1000)}, send)
        send.assert_not_called()

    def test_unrecorded_orphan_is_never_adopted_or_signalled(self):
        send = Mock()
        with self.assertRaisesRegex(RuntimeError, "Orphan member generation"):
            self.stop_mocked(self.saved_session(), {42: (9, 41, 41, 1000)}, send)
        send.assert_not_called()

    def test_exact_live_leader_allows_owned_members_and_saves_their_generations(self):
        saved = self.saved_session()
        snapshot = {41: (8, 41, 41, 1000), 42: (9, 41, 41, 1000)}
        send = Mock(side_effect=lambda pid, _: snapshot.pop(pid))
        self.stop_mocked(saved, snapshot, send)
        self.assertEqual(
            send.call_args_list,
            [unittest.mock.call(41, signal.SIGTERM), unittest.mock.call(42, signal.SIGTERM)],
        )
        self.assertEqual(saved["members"][42], (9, 41, 41, 1000))

    def test_exact_saved_orphan_remains_owned_without_leader(self):
        saved = self.saved_session()
        saved["members"][42] = (9, 41, 41, 1000)
        snapshot = {42: (9, 41, 41, 1000)}
        send = Mock(side_effect=lambda pid, _: snapshot.pop(pid))
        self.stop_mocked(saved, snapshot, send)
        send.assert_called_once_with(42, signal.SIGTERM)

    def test_completed_empty_command_retires_from_persisted_ownership(self):
        runtime = smoke.Runtime.__new__(smoke.Runtime)
        entry = {"child": SimpleNamespace(pid=41), "birth": 8, "members": {41: (8, 41, 41, 1000)}}
        runtime.processes = [entry]
        runtime.persist = Mock()
        with patch.object(smoke, "read_session", return_value={}):
            runtime.retire(entry)
        self.assertEqual(runtime.processes, [])
        runtime.persist.assert_called_once_with()

    def test_completed_command_cannot_retire_over_reused_leader(self):
        runtime = smoke.Runtime.__new__(smoke.Runtime)
        entry = {"child": SimpleNamespace(pid=41), "birth": 8, "members": {41: (8, 41, 41, 1000)}}
        runtime.processes = [entry]
        runtime.persist = Mock()
        with patch.object(smoke, "read_session", return_value={41: (99, 41, 41, 1000)}):
            with self.assertRaisesRegex(RuntimeError, "leader generation"):
                runtime.retire(entry)
        runtime.persist.assert_not_called()

    def test_monitor_failure_arriving_during_join_invalidates_final_success(self):
        runtime = SimpleNamespace(finished=Mock(), monitor_error=None)
        runtime.monitor_thread = Mock()
        runtime.monitor_thread.is_alive.return_value = False
        runtime.monitor_thread.join.side_effect = lambda **_: setattr(
            runtime, "monitor_error", "late reserve failure"
        )
        smoke.stop_monitor(runtime)
        self.assertEqual(smoke.final_failure(None, runtime.monitor_error), "late reserve failure")
        runtime.finished.set.assert_called_once_with()
        runtime.monitor_thread.join.assert_called_once_with(timeout=5)

    def test_live_monitor_after_join_fails_closed(self):
        runtime = SimpleNamespace(finished=Mock(), monitor_error=None, monitor_thread=Mock())
        runtime.monitor_thread.is_alive.return_value = True
        with self.assertRaisesRegex(RuntimeError, "monitor did not stop"):
            smoke.stop_monitor(runtime)
        self.assertEqual(smoke.final_failure("cleanup failed", None), "cleanup failed")


if __name__ == "__main__":
    unittest.main()
