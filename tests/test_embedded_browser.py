from __future__ import annotations

import unittest

from collectors.embedded_browser import (
    BrowserBridge,
    get_bridge,
    get_or_create_bridge,
    remove_bridge,
)


class EmbeddedBrowserBridgeTest(unittest.TestCase):
    def test_screenshot_publish_and_read(self) -> None:
        bridge = BrowserBridge("task-1", url="https://example.com")
        self.assertIsNone(bridge.latest_screenshot())
        self.assertEqual(bridge.screenshot_seq, 0)

        bridge.publish_screenshot(b"frame-1")
        self.assertEqual(bridge.latest_screenshot(), b"frame-1")
        self.assertEqual(bridge.screenshot_seq, 1)

        bridge.publish_screenshot(b"frame-2")
        self.assertEqual(bridge.latest_screenshot(), b"frame-2")
        self.assertEqual(bridge.screenshot_seq, 2)

    def test_command_queue_drains_once(self) -> None:
        bridge = BrowserBridge("task-2")
        bridge.submit_command("click", x=10, y=20)
        bridge.submit_command("type", text="hello")

        pending = bridge.drain_commands()
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0].action, "click")
        self.assertEqual(pending[0].kwargs, {"x": 10, "y": 20})
        self.assertEqual(pending[1].kwargs, {"text": "hello"})

        # Draining again returns nothing new until more commands arrive.
        self.assertEqual(bridge.drain_commands(), [])

    def test_solved_and_error_state(self) -> None:
        bridge = BrowserBridge("task-3")
        self.assertFalse(bridge.is_solved)
        bridge.mark_solved()
        self.assertTrue(bridge.is_solved)

        self.assertEqual(bridge.error, "")
        bridge.mark_error("timed out")
        self.assertEqual(bridge.error, "timed out")

    def test_registry_get_or_create_and_remove(self) -> None:
        task_id = "task-registry-1"
        self.addCleanup(remove_bridge, task_id)

        self.assertIsNone(get_bridge(task_id))
        bridge = get_or_create_bridge(task_id, url="https://example.com")
        self.assertIs(get_bridge(task_id), bridge)

        # Re-requesting the same live task_id returns the same instance.
        same_bridge = get_or_create_bridge(task_id)
        self.assertIs(same_bridge, bridge)

        remove_bridge(task_id)
        self.assertIsNone(get_bridge(task_id))

    def test_registry_recreates_after_close(self) -> None:
        task_id = "task-registry-2"
        self.addCleanup(remove_bridge, task_id)

        bridge = get_or_create_bridge(task_id)
        bridge.close()
        self.assertTrue(bridge.is_closed)

        new_bridge = get_or_create_bridge(task_id)
        self.assertIsNot(new_bridge, bridge)
        self.assertFalse(new_bridge.is_closed)


if __name__ == "__main__":
    unittest.main()
