#!/usr/bin/env python3
"""Teleprompter that publishes selected text and follows clipboard changes."""

import argparse
import os
import time
import tkinter as tk
from pathlib import Path

import uiautomation as auto

from player import read_text
from ui import (
    END_MARKER_GAP,
    SIZE_MAX,
    SIZE_MIN,
    SPEED_MAX,
    SPEED_MIN,
    SignboardUI,
    clamp,
)


POLL_INTERVAL_MS = 250
SELECTION_STABLE_SECONDS = 0.35
HOVER_REPEAT_MS = 150
HOVER_SPEED_STEP = 10


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
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.save_and_reload(text)
        self.last_clipboard_text = text
        self.last_selection = text
        self.ui.status_var.set("Selected text copied to clipboard and loaded.")

    def poll_sources(self):
        try:
            selection = self.get_selected_text()
        except auto.comtypes.COMError as exc:
            self.ui.status_var.set(f"Selection capture error: {exc}")
        else:
            now = time.monotonic()
            if selection != self.pending_selection:
                self.pending_selection = selection
                self.pending_since = now
            elif (
                selection
                and selection != self.last_selection
                and now - self.pending_since >= SELECTION_STABLE_SECONDS
            ):
                try:
                    self.publish_selection(selection)
                except (OSError, tk.TclError) as exc:
                    self.ui.status_var.set(
                        f"Could not publish selected text: {exc}"
                    )

        clipboard_text = self.read_clipboard(self.root)
        if clipboard_text and clipboard_text != self.last_clipboard_text:
            try:
                self.save_and_reload(clipboard_text)
            except OSError as exc:
                self.ui.status_var.set(
                    f"Could not update {self.args.file}: {exc}"
                )
            else:
                self.last_clipboard_text = clipboard_text
                self.ui.status_var.set("Clipboard text loaded into the prompter.")

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
