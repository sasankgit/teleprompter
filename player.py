#!/usr/bin/env python3
"""
Scrolling text signboard: reads input.txt and scrolls it across the window
in one spot, so you can read it without moving your eyes.

Run:     python led_signboard.py
         python led_signboard.py --file notes.txt --speed 200 --size 60

Controls (sliders at the bottom, or keys):
    Speed        Up / Down arrows
    Text size    + / -
    Space  pause / resume     R  reload the file     F  fullscreen     Esc  quit
"""

import argparse
import math
import os
import time
import tkinter as tk
import tkinter.font as tkfont

BG_COLOR = "#000000"
TEXT_COLOR = "#ffffff"
PANEL_BG = "#161616"
PANEL_FG = "#dddddd"
SEPARATOR = "      \u2022      "   # shown between lines

SPEED_MIN, SPEED_MAX = 20, 1000    # pixels per second
SIZE_MIN, SIZE_MAX = 16, 200       # font size in points


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
    if not lines:
        return f"{os.path.basename(path)} is empty"
    return SEPARATOR.join(lines)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Signboard:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.paused = False
        self.pos = 0.0
        self.unit_w = 1
        self.last = time.time()
        self.text = ""
        self.rebuild_job = None

        self.speed_var = tk.IntVar(value=clamp(int(args.speed), SPEED_MIN, SPEED_MAX))
        self.size_var = tk.IntVar(value=clamp(args.size, SIZE_MIN, SIZE_MAX))

        self.font = tkfont.Font(family="Helvetica", size=self.size_var.get(), weight="bold")

        root.title("Text Signboard")
        root.configure(bg=BG_COLOR)
        root.geometry("1000x300")

        self.canvas = tk.Canvas(root, bg=BG_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.item = self.canvas.create_text(0, 0, text="", anchor="w",
                                            fill=TEXT_COLOR, font=self.font)
        self.build_panel()

        self.canvas.bind("<Configure>", lambda e: self.schedule_rebuild())
        root.bind("<space>", lambda e: self.toggle_pause())
        root.bind("<Up>", lambda e: self.nudge(self.speed_var, +20, SPEED_MIN, SPEED_MAX))
        root.bind("<Down>", lambda e: self.nudge(self.speed_var, -20, SPEED_MIN, SPEED_MAX))
        for k in ("<plus>", "<equal>", "<KP_Add>"):
            root.bind(k, lambda e: self.nudge(self.size_var, +4, SIZE_MIN, SIZE_MAX))
        for k in ("<minus>", "<KP_Subtract>"):
            root.bind(k, lambda e: self.nudge(self.size_var, -4, SIZE_MIN, SIZE_MAX))
        root.bind("<r>", lambda e: self.reload())
        root.bind("<f>", lambda e: self.toggle_fullscreen())
        root.bind("<Escape>", lambda e: root.destroy())

        self.reload()
        self.tick()

    # ---------- control panel ----------
    def build_panel(self):
        panel = tk.Frame(self.root, bg=PANEL_BG)
        panel.pack(fill="x", side="bottom")

        def slider(label, var, lo, hi, on_change):
            tk.Label(panel, text=label, bg=PANEL_BG, fg=PANEL_FG).pack(
                side="left", padx=(10, 4), pady=6)
            s = tk.Scale(panel, from_=lo, to=hi, orient="horizontal", variable=var,
                         length=200, bg=PANEL_BG, fg=PANEL_FG, troughcolor="#333333",
                         highlightthickness=0, takefocus=0, command=on_change)
            s.pack(side="left", padx=(0, 10))
            # give keyboard focus back to the window so hotkeys keep working
            s.bind("<ButtonRelease-1>", lambda e: self.root.focus_set())

        slider("Speed", self.speed_var, SPEED_MIN, SPEED_MAX, lambda v: None)
        slider("Text size", self.size_var, SIZE_MIN, SIZE_MAX,
               lambda v: self.schedule_rebuild())

    def nudge(self, var, delta, lo, hi):
        var.set(clamp(var.get() + delta, lo, hi))
        if var is self.size_var:
            self.schedule_rebuild()

    # ---------- text layout ----------
    def schedule_rebuild(self):
        """Debounce so dragging the slider / resizing doesn't rebuild constantly."""
        if self.rebuild_job is not None:
            self.root.after_cancel(self.rebuild_job)
        self.rebuild_job = self.root.after(80, self.rebuild)

    def rebuild(self):
        self.rebuild_job = None
        self.font.configure(size=self.size_var.get())
        unit = self.text + SEPARATOR
        self.unit_w = max(1, self.font.measure(unit))
        width = max(self.canvas.winfo_width(), 1)
        # repeat the text enough times to always fill the window while looping
        copies = math.ceil(width / self.unit_w) + 1
        self.canvas.itemconfigure(self.item, text=unit * copies)
        self.canvas.coords(self.item, self.pos, self.canvas.winfo_height() / 2)

    # ---------- actions ----------
    def reload(self):
        try:
            self.text = read_text(self.args.file)
        except FileNotFoundError:
            self.text = f"File not found: {self.args.file}"
        self.pos = float(self.canvas.winfo_width())   # start entering from the right
        self.rebuild()

    def toggle_pause(self):
        self.paused = not self.paused

    def toggle_fullscreen(self):
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    # ---------- animation ----------
    def tick(self):
        now = time.time()
        dt = now - self.last
        self.last = now
        if not self.paused:
            self.pos -= self.speed_var.get() * dt
            if self.pos <= -self.unit_w:       # one full repeat has scrolled past
                self.pos += self.unit_w
        self.canvas.coords(self.item, self.pos, self.canvas.winfo_height() / 2)
        self.root.after(16, self.tick)


def main():
    p = argparse.ArgumentParser(description="Scrolling text signboard for a text file")
    p.add_argument("--file", default="input.txt", help="text file to display")
    p.add_argument("--speed", type=float, default=150,
                   help=f"scroll speed in pixels/second ({SPEED_MIN}-{SPEED_MAX})")
    p.add_argument("--size", type=int, default=60,
                   help=f"font size ({SIZE_MIN}-{SIZE_MAX})")
    args = p.parse_args()

    if not os.path.exists(args.file):
        print(f"Note: {args.file} not found; create it next to this script.")

    root = tk.Tk()
    Signboard(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()