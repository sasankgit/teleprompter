#!/usr/bin/env python3
"""Teleprompter that publishes selected text and follows clipboard changes."""

import argparse
import ctypes
import os
import json
import threading
import time
import tkinter as tk
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import uiautomation as auto

from ui import (
    END_MARKER_GAP,
    SIZE_MAX,
    SIZE_MIN,
    SPEED_MAX,
    SPEED_MIN,
    SignboardUI,
    clamp,
)


SEPARATOR = "      \u2022      "
POLL_INTERVAL_MS = 30
HOVER_REPEAT_MS = 150
HOVER_SPEED_STEP = 10
DEFAULT_API_URL = "https://cherub-yo-yo-daffodil.ngrok-free.dev/v1/chat/completions"
DEFAULT_API_KEY = "sk-qwen36-kaggle-custom"


def read_text(path):
    with open(path, "r", encoding="utf-8") as file:
        lines = [line.strip() for line in file.read().splitlines() if line.strip()]
    if not lines:
        return f"{os.path.basename(path)} is empty"
    return SEPARATOR.join(lines)


class AutoSignboard:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.paused = False
        self.pos = 0.0
        self.text_width = 1
        self.finished = False
        self.started = False
        self.direction = -1
        self.last = time.time()
        self.text = ""
        self.rebuild_job = None
        self.speed_hover_job = None
        self.speed_hover_delta = 0
        self.last_clipboard_text = self.read_clipboard(root)
        self.last_selection = None
        self.pending_selection = None
        self.pending_since = 0.0
        self.request_in_flight = False
        self.mouse_was_down = False
        self.stream_text = ""

        self.ui = SignboardUI(root, self, args, auto_controls=True)
        self.speed_var = self.ui.speed_var
        self.size_var = self.ui.size_var
        self.reload()
        self.root.after(POLL_INTERVAL_MS, self.poll_sources)
        self.root.after(16, self.tick)

    @staticmethod
    def read_clipboard(root):
        try:
            return root.clipboard_get()
        except tk.TclError:
            return None

    def get_selected_text(self):
        control = auto.GetFocusedControl()
        for _ in range(8):
            if control is None:
                break
            try:
                text_pattern = control.GetPattern(auto.PatternId.TextPattern)
            except auto.comtypes.COMError:
                text_pattern = None
            if text_pattern is not None:
                ranges = text_pattern.GetSelection()
                selected_ranges = []
                for text_range in ranges:
                    selected_range = text_range.GetText(-1).strip()
                    if selected_range:
                        selected_ranges.append(selected_range)
                selected = "\n".join(selected_ranges)
                if selected:
                    return selected
            control = control.GetParentControl()
        return None

    def save_and_reload(self, text):
        Path(self.args.file).write_text(text, encoding="utf-8")
        self.reload()

    def publish_selection(self, text):
        self.submit_text(text, "Selected text")

    def submit_text(self, text, source):
        text = text.strip()
        if not text or self.request_in_flight:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.save_and_reload(text)
        self.last_clipboard_text = text
        self.last_selection = text
        self.stream_text = ""
        self.ui.status_var.set(f"Sending {source.lower()} to interview server…")
        self.request_in_flight = True
        threading.Thread(
            target=self.request_answer, args=(text,), daemon=True
        ).start()

    def request_answer(self, text):
        payload = {
            "model": "qwen3.6-35b-a3b",
            "messages": [{"role": "user", "content": text}],
            "temperature": 0.25,
            "max_tokens": 220,
            "stream": True,
        }
        request = Request(
            self.args.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.args.api_key}",
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.args.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    piece = delta.get("content", "")
                    if piece:
                        self.stream_text += piece
                        self.root.after(0, self.update_stream, self.stream_text)
            if not self.stream_text.strip():
                raise ValueError("The server returned an empty answer")
            self.root.after(0, self.finish_stream)
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, IndexError,
                ValueError, json.JSONDecodeError) as exc:
            message = f"Interview server error: {exc}"
            self.root.after(0, self.finish_request, message, False)

    def show_answer(self, answer):
        try:
            self.save_and_reload(answer)
            self.last_clipboard_text = self.read_clipboard(self.root)
            self.last_selection = None
            self.ui.status_var.set("Answer received from interview server.")
        except (OSError, tk.TclError) as exc:
            self.ui.status_var.set(f"Could not display server answer: {exc}")
        finally:
            self.request_in_flight = False

    def update_stream(self, answer):
        try:
            Path(self.args.file).write_text(answer, encoding="utf-8")
            self.text = answer
            self.finished = False
            self.pos = float(self.ui.canvas_width())
            self.rebuild()
            self.ui.status_var.set("Streaming answer from interview server…")
        except (OSError, tk.TclError) as exc:
            self.ui.status_var.set(f"Could not display streamed answer: {exc}")

    def finish_stream(self):
        self.last_clipboard_text = self.read_clipboard(self.root)
        self.last_selection = None
        self.request_in_flight = False
        self.ui.status_var.set("Answer received from interview server.")

    def finish_request(self, message, _success):
        self.request_in_flight = False
        self.ui.status_var.set(message)

    def poll_sources(self):
        button_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        released = self.mouse_was_down and not button_down
        self.mouse_was_down = button_down
        if released and not self.request_in_flight:
            try:
                selection = self.get_selected_text()
                if selection and selection != self.last_selection:
                    self.publish_selection(selection)
            except (auto.comtypes.COMError, OSError, tk.TclError) as exc:
                self.ui.status_var.set(f"Could not publish selected text: {exc}")

        clipboard_text = self.read_clipboard(self.root)
        if clipboard_text and clipboard_text != self.last_clipboard_text:
            try:
                if not self.request_in_flight:
                    self.submit_text(clipboard_text, "Copied text")
            except OSError as exc:
                self.ui.status_var.set(f"Could not submit copied text: {exc}")

        self.root.after(POLL_INTERVAL_MS, self.poll_sources)

    def start_speed_hover(self, direction):
        if self.speed_hover_job is not None:
            return
        self.speed_hover_delta = direction
        self.nudge(self.speed_var, direction * 20, SPEED_MIN, SPEED_MAX)
        self.speed_hover_job = self.root.after(
            HOVER_REPEAT_MS, self.repeat_speed_hover
        )

    def repeat_speed_hover(self):
        self.speed_hover_job = None
        self.nudge(
            self.speed_var,
            self.speed_hover_delta * HOVER_SPEED_STEP,
            SPEED_MIN,
            SPEED_MAX,
        )
        self.speed_hover_job = self.root.after(
            HOVER_REPEAT_MS, self.repeat_speed_hover
        )

    def stop_speed_hover(self):
        if self.speed_hover_job is not None:
            self.root.after_cancel(self.speed_hover_job)
            self.speed_hover_job = None

    def nudge(self, variable, delta, low, high):
        variable.set(clamp(variable.get() + delta, low, high))
        if variable is self.size_var:
            self.schedule_rebuild()

    def on_canvas_configure(self, event):
        if not self.started:
            self.pos = float(event.width)
            self.started = True
        self.schedule_rebuild()

    def schedule_rebuild(self):
        if self.rebuild_job is not None:
            self.root.after_cancel(self.rebuild_job)
        self.rebuild_job = self.root.after(80, self.rebuild)

    def rebuild(self):
        self.rebuild_job = None
        self.ui.configure_font(self.size_var.get())
        self.text_width = max(1, self.ui.measure_text(self.text))
        if self.finished:
            self.pos = self.get_finished_text_position(
                self.ui.canvas_width() / 2
            )
        self.ui.set_text(self.text)
        self.update_canvas_positions()

    def update_canvas_positions(self):
        self.ui.update_canvas_positions(self.pos, self.get_end_marker_x())

    def reload(self):
        try:
            self.text = read_text(self.args.file)
        except FileNotFoundError:
            self.text = f"File not found: {self.args.file}"
        self.finished = False
        self.pos = float(self.ui.canvas_width())
        self.rebuild()

    def toggle_pause(self):
        self.paused = not self.paused

    def toggle_fullscreen(self):
        self.root.attributes(
            "-fullscreen", not bool(self.root.attributes("-fullscreen"))
        )

    def start_over(self):
        self.direction = -1
        self.paused = False
        self.last = time.time()
        self.reload()

    def reverse_direction(self):
        self.direction *= -1
        self.finished = False
        self.paused = False
        self.last = time.time()

    def tick(self):
        now = time.time()
        dt = now - self.last
        self.last = now
        if not self.paused and not self.finished:
            self.pos += self.direction * self.speed_var.get() * dt
            marker_x = self.get_end_marker_x()
            center_x = self.ui.canvas_width() / 2
            if self.direction * (marker_x - center_x) >= 0:
                self.pos = self.get_finished_text_position(center_x)
                self.finished = True
        self.update_canvas_positions()
        self.root.after(16, self.tick)

    def get_end_marker_x(self):
        if self.direction < 0:
            return self.pos + self.text_width + END_MARKER_GAP
        return self.pos - END_MARKER_GAP

    def get_finished_text_position(self, center_x):
        if self.direction < 0:
            return center_x - self.text_width - END_MARKER_GAP
        return center_x + END_MARKER_GAP


def main():
    if os.name != "nt":
        raise SystemExit("playerauto.py requires Windows UI Automation.")

    parser = argparse.ArgumentParser(
        description="Teleprompter that follows selected and clipboard text"
    )
    parser.add_argument("--file", default="input.txt", help="text file to update")
    parser.add_argument(
        "--api-url", default=os.environ.get("MOCKINTERVIEW_API_URL", DEFAULT_API_URL),
        help="Kaggle interview server chat completions URL",
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("MOCKINTERVIEW_API_KEY", DEFAULT_API_KEY),
        help="API key configured in the Kaggle notebook",
    )
    parser.add_argument("--timeout", type=float, default=180,
                        help="server request timeout in seconds")
    parser.add_argument(
        "--speed",
        type=float,
        default=850,
        help=f"scroll speed in pixels/second ({SPEED_MIN}-{SPEED_MAX})",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=60,
        help=f"font size ({SIZE_MIN}-{SIZE_MAX})",
    )
    args = parser.parse_args()
    args.speed = clamp(int(args.speed), SPEED_MIN, SPEED_MAX)
    args.size = clamp(args.size, SIZE_MIN, SIZE_MAX)

    root = SignboardUI.create_root()
    AutoSignboard(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
