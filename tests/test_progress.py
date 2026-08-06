from __future__ import annotations

from contextvars import copy_context
import io
import sys
import threading
import unittest

from bmlsub.progress import (
    NullProgressReporter,
    ProgressEvent,
    TerminalProgressReporter,
    finish_progress_task,
    get_progress_reporter,
    get_progress_task,
    progress_reporter,
    progress_task,
    reset_progress_reporter,
    set_progress_reporter,
)
from bmlsub.production.execution import parse_ffmpeg_progress_time_ms
from bmlsub.execution.process_runner import ProcessRunner


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


class NonTTYBuffer(io.StringIO):
    def isatty(self):
        return False


def event(state="running", **overrides):
    values = {
        "phase": "delivery", "step": "encode", "label": "Encode MP4",
        "state": state,
    }
    values.update(overrides)
    return ProgressEvent(**values)


class ProgressTests(unittest.TestCase):
    def test_non_tty_terminal_reporter_is_noop(self):
        output = NonTTYBuffer()
        reporter = TerminalProgressReporter(output)
        reporter.report(event(current=1, total=2))
        reporter.report(event("completed"))
        self.assertEqual(output.getvalue(), "")

    def test_unknown_total_shows_spinner_and_elapsed_without_percentage(self):
        output = TTYBuffer()
        times = iter((10.0, 12.0))
        reporter = TerminalProgressReporter(
            output, clock=lambda: next(times), refresh_interval=0,
        )
        reporter.report(event(detail="reading source"))
        reporter.report(event(current=4, unit="frames"))
        rendered = output.getvalue()
        self.assertIn("[|] Encode MP4 elapsed 00:00 - reading source", rendered)
        self.assertIn("[/] Encode MP4 4 frames elapsed 00:02", rendered)
        self.assertNotIn("%", rendered)

    def test_known_total_shows_bounded_percentage(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, enabled=True, clock=lambda: 1.0, refresh_interval=0)
        reporter.report(event(current=5, total=10, unit="frames"))
        self.assertIn("5/10 frames", output.getvalue())
        self.assertIn("50.0%", output.getvalue())
        self.assertIn("[=========>----------]", output.getvalue())

        reporter.report(event(current=12, total=10))
        self.assertIn("100.0%", output.getvalue())

    def test_dynamic_lines_are_truncated_to_terminal_width(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, enabled=True, width=40, refresh_interval=0, clock=lambda: 1.0)
        reporter.report(event(current=5, total=10, detail="这是一个很长的输出文件名.mp4"))
        rendered = output.getvalue().replace("\r", "")
        self.assertLessEqual(len(rendered.rstrip()), 40)
        self.assertTrue(rendered.rstrip().endswith("..."))

    def test_dynamic_progress_is_throttled_by_default(self):
        output = TTYBuffer()
        now = iter((0.0, 1.0, 6.9, 7.0))
        reporter = TerminalProgressReporter(output, enabled=True, clock=lambda: next(now))
        for current in range(1, 5):
            reporter.report(event(current=current, total=10))
        self.assertEqual(output.getvalue().count("\r"), 2)

    def test_terminal_states_emit_stable_sanitized_lines(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, clock=lambda: 1.0, refresh_interval=0)
        reporter.report(event("completed", detail="output ready"))
        reporter.report(event("reused", label="Reuse\nMKV"))
        reporter.report(event("failed", detail="encoder\nfailed"))
        self.assertEqual(
            output.getvalue(),
            "[completed] Encode MP4 - output ready\n"
            "[reused] Reuse MKV\n"
            "[failed] Encode MP4 - encoder failed\n",
        )

    def test_context_reporter_restores_previous_value(self):
        original = get_progress_reporter()
        selected = NullProgressReporter()
        with progress_reporter(selected) as active:
            self.assertIs(active, selected)
            self.assertIs(get_progress_reporter(), selected)
            self.assertIs(copy_context().run(get_progress_reporter), selected)
        self.assertIs(get_progress_reporter(), original)

        token = set_progress_reporter(selected)
        self.assertIs(get_progress_reporter(), selected)
        reset_progress_reporter(token)
        self.assertIs(get_progress_reporter(), original)

    def test_shared_terminal_reporter_serializes_thread_writes(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, clock=lambda: 1.0, refresh_interval=0)
        barrier = threading.Barrier(3)

        def complete(index):
            barrier.wait()
            reporter.report(event("completed", step=str(index), label=f"Task {index}"))

        threads = [threading.Thread(target=complete, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertCountEqual(
            output.getvalue().splitlines(),
            ["[completed] Task 0", "[completed] Task 1"],
        )

    def test_event_rejects_invalid_progress_values(self):
        with self.assertRaises(ValueError):
            event(label="")
        with self.assertRaises(ValueError):
            event(current=-1)
        with self.assertRaises(ValueError):
            event(total=float("nan"))

    def test_progress_task_reports_completion_and_failure(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, enabled=True, clock=lambda: 1.0, refresh_interval=0)
        with progress_task(
            phase="publish", step="r2", label="Upload R2",
            reporter=reporter, refresh_interval=10,
        ) as task:
            task.update(current=5, total=10, unit="bytes")
            task.finish("reused", detail="already uploaded")
        self.assertIn("5/10 bytes", output.getvalue())
        self.assertIn("[reused] Upload R2 - already uploaded\n", output.getvalue())

        with self.assertRaisesRegex(RuntimeError, "offline"):
            with progress_task(
                phase="publish", step="anibt", label="Publish Anibt",
                reporter=reporter, refresh_interval=10,
            ):
                raise RuntimeError("offline")
        self.assertIn("[failed] Publish Anibt - offline\n", output.getvalue())

    def test_progress_task_can_disable_heartbeat_for_nested_progress(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, enabled=True, clock=lambda: 1.0, refresh_interval=0)
        with progress_task(
            phase="delivery", step="outer", label="Outer", reporter=reporter,
            heartbeat=False,
        ) as task:
            self.assertIsNone(task._thread)
            task.update(current=1, total=2)
        self.assertIn("[completed] Outer\n", output.getvalue())
        self.assertIsNone(get_progress_task())

    def test_finish_progress_task_maps_pipeline_statuses(self):
        output = TTYBuffer()
        reporter = TerminalProgressReporter(output, enabled=True, clock=lambda: 1.0, refresh_interval=0)
        with progress_task(
            phase="delivery", step="encode", label="Encode", reporter=reporter,
            refresh_interval=10,
        ) as task:
            self.assertEqual(finish_progress_task(task, {"status": "skipped"}), "reused")
        self.assertIn("[reused] Encode\n", output.getvalue())

    def test_ffmpeg_progress_parser_uses_real_output_time(self):
        self.assertEqual(parse_ffmpeg_progress_time_ms("out_time_us=1250000"), 1250)
        self.assertEqual(parse_ffmpeg_progress_time_ms("out_time_ms=2500000"), 2500)
        self.assertEqual(parse_ffmpeg_progress_time_ms("out_time=00:01:02.500000"), 62500)
        self.assertIsNone(parse_ffmpeg_progress_time_ms("progress=continue"))
        self.assertIsNone(parse_ffmpeg_progress_time_ms("out_time_us=invalid"))

    def test_process_runner_streams_stderr_lines_and_keeps_stdout(self):
        lines = []
        result = ProcessRunner().run_streaming(
            [
                sys.executable, "-c",
                "import sys; print('out_time_us=1000', file=sys.stderr); print('ok')",
            ],
            stderr_line_callback=lines.append,
        )
        self.assertEqual(lines, ["out_time_us=1000"])
        self.assertEqual(result.stdout_text().strip(), "ok")


if __name__ == "__main__":
    unittest.main()
