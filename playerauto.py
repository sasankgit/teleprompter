#!/usr/bin/env python3
"""Teleprompter that publishes selected text and follows clipboard changes."""

import argparse
import os
import time
import tkinter as tk
from pathlib import Path

import uiautomation as auto

from player import SIZE_MAX, SIZE_MIN, SPEED_MAX, SPEED_MIN, Signboard, clamp


POLL_INTERVAL_MS = 250
SELECTION_STABLE_SECONDS = 0.35
HOVER_REPEAT_MS = 150
HOVER_SPEED_STEP = 10


class AutoSignboard(Signboard):
    def __init__(self, root, args):
        self.last_clipboard_text = self.read_clipboard(root)
        self.last_selection = None
        self.pending_selection = None
        self.pending_since = 0.0
        self.speed_hover_job = None
        self.speed_hover_delta = 0
        super().__init__(root, args)
        self.root.after(POLL_INTERVAL_MS, self.poll_sources)

    @staticmethod
    def read_clipboard(root):
        try:
            return root.clipboard_get()
        except tk.TclError:
            return None

    def build_panel(self):
        panel = tk.Frame(self.root, bg="#161616")
        self.panel = panel
        panel.pack(fill="x", side="bottom")

        tk.Label(panel, text="Speed", bg="#161616", fg="#dddddd").pack(
            side="left", padx=(10, 4), pady=6
        )

        def add_speed_button(label, delta):
            button = tk.Button(
                panel,
                text=label,
                bg="#333333",
                fg="#ffffff",
                activebackground="#555555",
                activeforeground="#ffffff",
                relief="flat",
                takefocus=0,
                padx=8,
            )
            button.pack(side="left", padx=2)
            button.bind(
                "<Enter>",
                lambda _event, direction=delta: self.start_speed_hover(direction),
            )
            button.bind("<Leave>", lambda _event: self.stop_speed_hover())

        def add_slider(label, variable, low, high, on_change):
            if label:
                tk.Label(panel, text=label, bg="#161616", fg="#dddddd").pack(
                    side="left", padx=(10, 4), pady=6
                )
            scale = tk.Scale(
                panel,
                from_=low,
                to=high,
                orient="horizontal",
                variable=variable,
                length=200,
                bg="#161616",
                fg="#dddddd",
                troughcolor="#333333",
                highlightthickness=0,
                takefocus=0,
                command=on_change,
            )
            scale.pack(side="left", padx=(0, 10))
            scale.bind("<ButtonRelease-1>", lambda _event: self.root.focus_set())

        add_speed_button("Slower", -1)
        add_speed_button("Faster", 1)
        add_slider("", self.speed_var, SPEED_MIN, SPEED_MAX, lambda _value: None)
        add_slider(
            "Text size",
            self.size_var,
            SIZE_MIN,
            SIZE_MAX,
            lambda _value: self.schedule_rebuild(),
        )
        self.status_var = tk.StringVar(
            value="Select text in another app or copy text to update the prompter."
        )
        tk.Label(
            panel,
            textvariable=self.status_var,
            bg="#161616",
            fg="#aaaaaa",
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=8)

    def adjust_speed(self, amount):
        self.speed_var.set(
            clamp(self.speed_var.get() + amount, SPEED_MIN, SPEED_MAX)
        )

    def start_speed_hover(self, direction):
        if self.speed_hover_job is not None:
            return
        self.speed_hover_delta = direction
        self.adjust_speed(direction * 20)
        self.speed_hover_job = self.root.after(
            HOVER_REPEAT_MS, self.repeat_speed_hover
        )

    def repeat_speed_hover(self):
        self.speed_hover_job = None
        self.adjust_speed(self.speed_hover_delta * HOVER_SPEED_STEP)
        self.speed_hover_job = self.root.after(
            HOVER_REPEAT_MS, self.repeat_speed_hover
        )

    def stop_speed_hover(self):
        if self.speed_hover_job is not None:
            self.root.after_cancel(self.speed_hover_job)
            self.speed_hover_job = None

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
        self.status_var.set("Selected text copied to clipboard and loaded.")

    def poll_sources(self):
        try:
            selection = self.get_selected_text()
        except auto.comtypes.COMError as exc:
            self.status_var.set(f"Selection capture error: {exc}")
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
                    self.status_var.set(f"Could not publish selected text: {exc}")

        clipboard_text = self.read_clipboard(self.root)
        if clipboard_text and clipboard_text != self.last_clipboard_text:
            try:
                self.save_and_reload(clipboard_text)
            except OSError as exc:
                self.status_var.set(f"Could not update {self.args.file}: {exc}")
            else:
                self.last_clipboard_text = clipboard_text
                self.status_var.set("Clipboard text loaded into the prompter.")

        self.root.after(POLL_INTERVAL_MS, self.poll_sources)


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
        default=150,
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

    root = tk.Tk()
    AutoSignboard(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
