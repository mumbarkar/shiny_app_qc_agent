import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import tool_set
from playwright.sync_api import sync_playwright


class WalkthroughTests(unittest.TestCase):
    def test_comprehensive_run_walks_tabs_and_captures_before_advancing(self):
        root = {"key": "root", "text": "Root", "panel_id": "root-panel"}
        sibling = {"key": "sibling", "text": "Sibling", "panel_id": "sibling-panel"}
        child = {"key": "child", "text": "Child", "panel_id": "child-panel"}
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.screenshot.return_value = b"mock-png"
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=MagicMock(return_value=browser)))
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        captured = {}
        events = []
        active_tab = {"text": None}

        def save_report(results, app_name):
            captured["results"] = json.loads(results)
            captured["app_name"] = app_name
            return "mock-report.html"

        def descriptors(_page, parent_panel=None):
            if parent_panel is None:
                return [root, sibling]
            return [child] if parent_panel["key"] == root["key"] else []

        def activate(_page, tab):
            events.append(("activate", tab["text"]))
            active_tab["text"] = tab["text"]

        def wait_for_settle(_page, timeout, panel_id):
            events.append(("settled", active_tab["text"]))

        def capture_screenshot(**kwargs):
            events.append(("screenshot", active_tab["text"]))
            self.assertEqual(kwargs["type"], "png")
            self.assertTrue(kwargs["full_page"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], tool_set.SCREENSHOT_TIMEOUT)
            return b"mock-png"

        page.screenshot.side_effect = capture_screenshot
        with patch.object(tool_set, "sync_playwright", return_value=manager), patch.object(
            tool_set, "_wait_for_root_tabs"
        ), patch.object(tool_set, "_visible_tab_descriptors", side_effect=descriptors), patch.object(
            tool_set, "_activate_tab", side_effect=activate
        ), patch.object(tool_set, "_wait_for_tab_settle", side_effect=wait_for_settle), patch.object(
            tool_set, "_shiny_connection_snapshot",
            return_value={"status": "unknown", "disconnect_count": 0, "disconnected": False},
        ), patch.object(
            tool_set, "_is_tab_panel_active", return_value=False
        ), patch.object(
            tool_set, "_visible_control_inventory", side_effect=AssertionError("must not inspect controls")
        ), patch.object(
            tool_set, "_collect_visible_shiny_errors", side_effect=AssertionError("must not inspect elements")
        ), patch.object(tool_set, "generate_test_report", side_effect=save_report):
            result = tool_set.run_comprehensive_shiny_tests("https://example.test", "Teal")

        self.assertIn("mock-report.html", result)
        browser.close.assert_called_once_with()
        self.assertEqual(page.screenshot.call_count, 3)
        rows = captured["results"][0]["tabs_tested"]
        self.assertEqual([row["text"] for row in rows], ["Root", "Child", "Sibling"])
        self.assertEqual([row["status"] for row in rows], ["success"] * 3)
        self.assertEqual([row["screenshot_base64"] for row in rows], ["bW9jay1wbmc="] * 3)
        self.assertEqual(events, [
            ("activate", "Root"), ("settled", "Root"), ("screenshot", "Root"),
            ("activate", "Root"), ("settled", "Root"), ("activate", "Child"),
            ("settled", "Child"), ("screenshot", "Child"),
            ("activate", "Sibling"), ("settled", "Sibling"), ("screenshot", "Sibling"),
        ])
        browser.new_page.assert_called_once_with()
        playwright.chromium.launch.assert_called_once_with(headless=False)
        self.assertEqual([call.args for call in page.locator.call_args_list], [("body",)])
        page.wait_for_timeout.assert_not_called()

    def test_screenshot_timeout_is_capped_by_remaining_tab_runtime(self):
        with patch.object(tool_set, "GLOBAL_RUN_TIMEOUT", 5000), patch.object(
            tool_set, "SCREENSHOT_TIMEOUT", 90000
        ), patch.object(tool_set.time, "perf_counter", return_value=12.5):
            self.assertEqual(tool_set._remaining_timeout_ms(15.0, timeout_cap=90000), 2500)

        with patch.object(tool_set.time, "perf_counter", return_value=16.0):
            with self.assertRaisesRegex(TimeoutError, "Per-tab render deadline"):
                tool_set._remaining_timeout_ms(15.0)

    def test_tab_discovery_is_hierarchical(self):
        markup = """
        <style>.tab-pane:not(.active) { display: none; }</style>
        <nav>
          <a data-toggle="tab" href="#root-panel">Root</a>
          <a data-toggle="tab" href="#sibling-panel">Sibling</a>
        </nav>
        <section id="root-panel" class="tab-pane active">
          <nav class="nested"><a data-toggle="tab" href="#child-panel">Child</a></nav>
          <section id="child-panel" class="tab-pane active"></section>
        </section>
        <section id="sibling-panel" class="tab-pane"></section>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(markup)
            tool_set._wait_for_root_tabs(page)
            roots = tool_set._visible_tab_descriptors(page)
            self.assertEqual([tab["text"] for tab in roots], ["Root", "Sibling"])
            self.assertEqual(
                [tab["text"] for tab in tool_set._visible_tab_descriptors(page, parent_panel=roots[0])],
                ["Child"],
            )
            browser.close()

    def test_settle_waits_for_root_busy_and_external_computing_indicator(self):
        markup = """
        <style>.tab-pane:not(.active) { display: none; }</style>
        <div id="computing">Computing ...</div>
                <section id="panel" class="tab-pane active">
                        <div class="shiny-bound-output recalculating" id="output">Starting</div>
                        <img id="delayed-plot" alt="Delayed plot" style="width: 1px; height: 1px">
                </section>
        <script>
          document.documentElement.classList.add('shiny-busy');
          setTimeout(() => {
            document.documentElement.classList.remove('shiny-busy');
            document.querySelector('#computing').remove();
            document.querySelector('#output').textContent = 'Rendered output';
          }, 1000);
                    setTimeout(() => {
                        document.querySelector('#delayed-plot').src =
                            'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
                    }, 1800);
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(markup)
            started = time.perf_counter()
            tool_set._wait_for_tab_settle(page, timeout=5000, panel_id="panel")
            elapsed = time.perf_counter() - started
            self.assertGreaterEqual(elapsed, 3.0)
            self.assertEqual(page.locator("#output").inner_text(), "Rendered output")
            self.assertEqual(page.locator("#delayed-plot").evaluate("el => el.naturalWidth"), 1)
            self.assertTrue(page.locator("#output").evaluate("el => el.classList.contains('recalculating')"))
            browser.close()

    def test_settle_waits_for_visible_recalculating_plot_output(self):
        markup = """
        <section id="panel" class="tab-pane active">
            <div id="plot_out_main" class="shiny-html-output shiny-bound-output recalculating" style="width: 672px"></div>
        </section>
        <script>
          setTimeout(() => {
            const plot = document.querySelector('#plot_out_main');
            plot.classList.remove('recalculating');
            plot.style.height = '400px';
            plot.innerHTML = '<svg><circle cx="10" cy="10" r="8"></circle></svg>';
          }, 1200);
        </script>
        """
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(markup)
            started = time.perf_counter()
            tool_set._wait_for_tab_settle(page, timeout=5000, panel_id="panel")
            elapsed = time.perf_counter() - started
            plot_state = page.locator("#plot_out_main").evaluate(
                "el => [el.className, el.clientHeight, el.childElementCount]"
            )
            self.assertGreaterEqual(elapsed, 2.5)
            self.assertNotIn("recalculating", plot_state[0])
            self.assertEqual(plot_state[1:], [400, 1])
            browser.close()

    def test_navigation_failure_generates_report_and_closes_browser(self):
        page = MagicMock()
        page.goto.side_effect = RuntimeError("temporary DNS failure")
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=MagicMock(return_value=browser)))
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        captured = {}

        def save_report(results, _app_name):
            captured["results"] = json.loads(results)
            return "failed-run.html"

        with patch.object(tool_set, "sync_playwright", return_value=manager), patch.object(
            tool_set, "generate_test_report", side_effect=save_report
        ), patch.object(tool_set, "_visible_control_inventory") as inventory:
            result = tool_set.run_comprehensive_shiny_tests("https://example.test", "Teal")
        self.assertIn("failed-run.html", result)
        self.assertEqual(captured["results"][0]["status"], "failed")
        self.assertIn("temporary DNS failure", " ".join(captured["results"][0]["errors"]))
        inventory.assert_not_called()
        browser.close.assert_called_once_with()

    def test_screenshot_failure_is_reported_and_walker_continues(self):
        first = {"key": "first", "text": "First", "panel_id": "first-panel"}
        second = {"key": "second", "text": "Second", "panel_id": "second-panel"}
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=200)
        page.screenshot.side_effect = [RuntimeError("capture blocked"), b"second-png"]
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=MagicMock(return_value=browser)))
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        captured = {}

        def save_report(results, _app_name):
            captured["results"] = json.loads(results)
            return "screenshots.html"

        with patch.object(tool_set, "sync_playwright", return_value=manager), patch.object(
            tool_set, "_wait_for_root_tabs"
        ), patch.object(tool_set, "_visible_tab_descriptors", side_effect=[[first, second], [], []]), patch.object(
            tool_set, "_activate_tab"
        ), patch.object(tool_set, "_wait_for_tab_settle"), patch.object(
            tool_set, "_shiny_connection_snapshot",
            return_value={"status": "unknown", "disconnect_count": 0, "disconnected": False},
        ), patch.object(
            tool_set, "_is_tab_panel_active", return_value=False
        ), patch.object(tool_set, "generate_test_report", side_effect=save_report):
            tool_set.run_comprehensive_shiny_tests("https://example.test", "Teal")
        rows = captured["results"][0]["tabs_tested"]
        self.assertEqual([row["text"] for row in rows], ["First", "Second"])
        self.assertEqual([row["status"] for row in rows], ["screenshot_failed", "success"])
        self.assertEqual(rows[1]["screenshot_base64"], "c2Vjb25kLXBuZw==")
        browser.close.assert_called_once_with()

    def test_capture_retries_after_a_disconnect_during_screenshot(self):
        page = MagicMock()
        page.screenshot.side_effect = [b"discarded-png", b"stable-png"]
        snapshots = [
            {"status": "connected", "disconnect_count": 0, "disconnected": False},
            {"status": "disconnected", "disconnect_count": 1, "disconnected": True},
            {"status": "connected", "disconnect_count": 1, "disconnected": False},
            {"status": "connected", "disconnect_count": 1, "disconnected": False},
        ]
        events = []

        def snapshot(_page):
            events.append("connection-check")
            return snapshots.pop(0)

        def settle(_page, timeout, panel_id):
            events.append("settled")

        with patch.object(tool_set, "_shiny_connection_snapshot", side_effect=snapshot), patch.object(
            tool_set, "_wait_for_tab_settle", side_effect=settle
        ), patch.object(tool_set, "_remaining_timeout_ms", return_value=5000):
            screenshot = tool_set._capture_tab_screenshot(page, "Matrix", "matrix-panel", 100.0)

        self.assertEqual(screenshot, b"stable-png")
        self.assertEqual(page.screenshot.call_count, 2)
        self.assertEqual(events, [
            "connection-check", "connection-check", "settled", "connection-check", "connection-check"
        ])

    def test_persistent_disconnect_prevents_screenshot(self):
        page = MagicMock()
        disconnected = {"status": "disconnected", "disconnect_count": 1, "disconnected": True}
        with patch.object(tool_set, "_shiny_connection_snapshot", return_value=disconnected), patch.object(
            tool_set, "_wait_for_tab_settle", side_effect=TimeoutError("Shiny server disconnected")
        ), patch.object(tool_set, "_remaining_timeout_ms", return_value=5000):
            with self.assertRaisesRegex(TimeoutError, "Shiny server disconnected"):
                tool_set._capture_tab_screenshot(page, "Matrix", "matrix-panel", 100.0)
        page.screenshot.assert_not_called()

    def test_settle_waits_for_shiny_reconnection(self):
        markup = '<section id="panel" class="tab-pane active"></section>'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            tool_set._install_shiny_connection_monitor(page)
            page.goto("data:text/html," + markup)
            page.evaluate("""() => {
                const handlers = {};
                window.jQuery = () => ({
                    on: (name, handler) => { handlers[name] = handler; }
                });
                window.jQuery.trigger = name => handlers[name]?.();
            }""")
            page.wait_for_function("() => window.__qcShinyConnection.jqueryAttached")
            page.evaluate("""() => {
                window.jQuery.trigger('shiny:disconnected');
                setTimeout(() => window.jQuery.trigger('shiny:connected'), 400);
            }""")
            started = time.perf_counter()
            tool_set._wait_for_tab_settle(page, timeout=4000, panel_id="panel")
            self.assertGreaterEqual(time.perf_counter() - started, 1.8)
            self.assertEqual(tool_set._shiny_connection_snapshot(page)["status"], "connected")
            self.assertTrue(page.evaluate("() => window.__qcShinyConnection.jqueryAttached"))
            browser.close()

    def test_timeout_defaults_allow_90_seconds_for_each_tab_render_and_capture(self):
        self.assertEqual(tool_set.SHINY_LOAD_TIMEOUT, 90000)
        self.assertEqual(tool_set.SHINY_READY_TIMEOUT, 60000)
        self.assertEqual(tool_set.TAB_RENDER_TIMEOUT, 90000)
        self.assertEqual(tool_set.SCREENSHOT_TIMEOUT, 90000)
        self.assertGreater(tool_set.GLOBAL_RUN_TIMEOUT, tool_set.SHINY_LOAD_TIMEOUT)

    def test_report_embeds_screenshots_and_escapes_tab_labels(self):
        results = [{
            "smoke_test": True,
            "status": "success",
            "url": "https://example.test",
            "timestamp": "2026-09-26T12:00:00",
            "start_time": "2026-09-26T12:00:00",
            "end_time": "2026-09-26T12:00:02.500000",
            "total_tabs": 1,
            "root_tabs": 1,
            "elapsed_seconds": 2.5,
            "navigation_seconds": 0.5,
            "tabs_tested": [{
                "text": "<Overview>",
                "status": "success",
                "elapsed_seconds": 1.25,
                "screenshot_base64": "cG5n",
            }],
        }]
        report_file = mock_open()
        with patch("builtins.open", report_file):
            tool_set.generate_test_report(json.dumps(results), "Teal Exploratory App")
        generated_html = report_file().write.call_args.args[0]
        self.assertIn('src="data:image/png;base64,cG5n"', generated_html)
        self.assertIn("&lt;Overview&gt;", generated_html)
        self.assertNotIn("Per-Tab Visible Control Inventory", generated_html)
        self.assertNotIn("Slider Testing Results", generated_html)
        self.assertNotIn("Radio Button Testing Results", generated_html)
        self.assertIn("SUCCESS", generated_html)
        self.assertIn("<strong>Start Time:</strong>", generated_html)
        self.assertIn("<strong>End Time:</strong>", generated_html)
        self.assertIn("<strong>Total Time for Testing:</strong> 2.5 s", generated_html)
        self.assertIn("Elapsed (s)", generated_html)
        self.assertIn("Tab Screenshots", generated_html)


if __name__ == "__main__":
    unittest.main()
