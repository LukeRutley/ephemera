import json
import unittest
from types import SimpleNamespace

import app as ephemera_app


class StreamingHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        ephemera_app.app.config["TESTING"] = True
        ephemera_app.THREADS.clear()
        ephemera_app.write_runtime_content("")
        self.client = ephemera_app.app.test_client()
        self.original_schedule_memories_refresh = ephemera_app.schedule_memories_refresh
        ephemera_app.schedule_memories_refresh = lambda _message: None

    def tearDown(self) -> None:
        ephemera_app.schedule_memories_refresh = self.original_schedule_memories_refresh
        ephemera_app.THREADS.clear()
        ephemera_app.write_runtime_content("")

    def test_streaming_preview_waits_for_completed_block(self) -> None:
        preview, boundary = ephemera_app.build_streaming_html_preview(
            "<!DOCTYPE html><html><body><main><h1>Hello</h1><p"
        )

        self.assertGreater(boundary, 0)
        self.assertIn("<h1>Hello</h1>", preview)
        self.assertNotIn("<p", preview)
        self.assertTrue(preview.endswith("</main></body></html>"))

    def test_send_stream_returns_incremental_html_then_final_state(self) -> None:
        final_html = (
            "<!DOCTYPE html><html><body><main><h1>Hello</h1><p>World</p></main></body></html>"
        )

        def fake_iter_tool_response_events(*_args, **_kwargs):
            yield {"type": "status", "message": "Generating your page…"}
            yield {
                "type": "text_delta",
                "delta": "<!DOCTYPE html><html><body><main><h1>Hello</h1><p",
            }
            yield {"type": "text_delta", "delta": ">World</p></main></body></html>"}
            return SimpleNamespace(output_text=final_html, output=[])

        original_iter_tool_response_events = ephemera_app.iter_tool_response_events
        ephemera_app.iter_tool_response_events = fake_iter_tool_response_events
        try:
            response = self.client.post("/send-stream", json={"message": "Build a page"})
        finally:
            ephemera_app.iter_tool_response_events = original_iter_tool_response_events

        self.assertEqual(response.status_code, 200)

        events = [
            json.loads(line)
            for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]

        self.assertEqual(events[0]["type"], "status")
        self.assertEqual(events[1]["type"], "html")
        self.assertIn("<h1>Hello</h1>", events[1]["html"])
        self.assertNotIn("<p>World</p>", events[1]["html"])
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["state"]["html"], final_html)
        self.assertEqual(events[-1]["state"]["messages"], ["Build a page"])
        self.assertEqual(ephemera_app.read_runtime_content(), final_html)


if __name__ == "__main__":
    unittest.main()
