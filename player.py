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
END_MARKER_SIZE = 12
END_MARKER_GAP = 24


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
        self.text_width = 1
        self.finished = False
        self.started = False
        self.direction = -1
        self.last = time.time()
        self.text = ""
        self.rebuild_job = None
        self.drag_origin = None
        self.action_menu_hide_job = None

        self.speed_var = tk.IntVar(value=clamp(int(args.speed), SPEED_MIN, SPEED_MAX))
        self.size_var = tk.IntVar(value=clamp(args.size, SIZE_MIN, SIZE_MAX))

        self.font = tkfont.Font(family="Helvetica", size=self.size_var.get(), weight="bold")

        root.title("Text Signboard")
        root.configure(bg=BG_COLOR)
        root.geometry("1000x300")
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.50)

        self.build_panel()
        self.add_drag_handle()
        self.add_action_menu()

        self.canvas = tk.Canvas(root, bg=BG_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.item = self.canvas.create_text(0, 0, text="", anchor="w",
                                            fill=TEXT_COLOR, font=self.font)
        self.end_marker = self.canvas.create_oval(
            0, 0, END_MARKER_SIZE, END_MARKER_SIZE,
            fill="#ff0000", outline="#ff0000",
        )

        self.canvas.bind("<Configure>", self.on_canvas_configure)
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
        self.panel = panel
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

    def add_drag_handle(self):
        handle = tk.Button(
            self.panel,
            text="Move",
            bg="#333333",
            fg=PANEL_FG,
            activebackground="#555555",
            activeforeground=PANEL_FG,
            relief="flat",
            takefocus=0,
            cursor="fleur",
            padx=6,
            pady=2,
        )
        handle.pack(side="right", padx=6, pady=4)
        handle.bind("<ButtonPress-1>", self.start_window_drag)
        handle.bind("<B1-Motion>", self.drag_window)
        handle.bind("<ButtonRelease-1>", self.stop_window_drag)

    def add_action_menu(self):
        self.action_button = tk.Button(
            self.panel,
            text="Actions",
            bg="#333333",
            fg=PANEL_FG,
            activebackground="#555555",
            activeforeground=PANEL_FG,
            relief="flat",
            takefocus=0,
            padx=6,
        )
        self.action_button.pack(side="right", padx=6, pady=4)
        self.action_button.bind("<Enter>", self.show_action_menu)
        self.action_button.bind("<Leave>", self.schedule_hide_action_menu)

        self.action_menu = tk.Toplevel(self.root)
        self.action_menu.withdraw()
        self.action_menu.overrideredirect(True)
        self.action_menu.attributes("-topmost", True)
        self.action_menu.attributes("-alpha", 0.50)
        self.action_menu.configure(bg=PANEL_BG)
        self.action_menu.transient(self.root)

        menu_frame = tk.Frame(self.action_menu, bg=PANEL_BG, padx=4, pady=4)
        menu_frame.grid(row=0, column=0)
        for column in range(3):
            menu_frame.grid_columnconfigure(column, weight=1)

        options = (
            ("Start over", 0, 1, self.start_over),
            ("Reverse", 1, 0, self.reverse_direction),
            ("Actions", 1, 1, None),
            ("", 1, 2, None),
            ("", 2, 1, None),
        )
        self.action_menu_widgets = []
        for label, row, column, command in options:
            button = tk.Button(
                menu_frame,
                text=label,
                bg="#333333",
                fg=PANEL_FG,
                activebackground="#555555",
                activeforeground=PANEL_FG,
                relief="flat",
                takefocus=0,
                padx=6,
                state="normal" if command is not None or label else "disabled",
                command=command,
            )
            button.grid(row=row, column=column, padx=3, pady=3, sticky="nsew")
            button.bind("<Enter>", self.cancel_hide_action_menu)
            button.bind("<Leave>", self.schedule_hide_action_menu)
            self.action_menu_widgets.append(button)
        self.action_menu.bind("<Enter>", self.cancel_hide_action_menu)
        self.action_menu.bind("<Leave>", self.schedule_hide_action_menu)

    def show_action_menu(self, _event=None):
        self.cancel_hide_action_menu()
        self.action_menu.update_idletasks()
        width = self.action_menu.winfo_reqwidth()
        height = self.action_menu.winfo_reqheight()
        x = self.action_button.winfo_rootx() + self.action_button.winfo_width() // 2 - width // 2
        y = self.action_button.winfo_rooty() + self.action_button.winfo_height() // 2 - height // 2
        x = clamp(x, 0, self.root.winfo_screenwidth() - width)
        y = clamp(y, 0, self.root.winfo_screenheight() - height)
        self.action_menu.geometry(f"+{x}+{y}")
        self.action_menu.deiconify()
        self.action_menu.lift()

    def schedule_hide_action_menu(self, _event=None):
        self.cancel_hide_action_menu()
        self.action_menu_hide_job = self.root.after(
            250, self.action_menu.withdraw
        )

    def cancel_hide_action_menu(self, _event=None):
        if self.action_menu_hide_job is not None:
            self.root.after_cancel(self.action_menu_hide_job)
            self.action_menu_hide_job = None

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

    def start_window_drag(self, event):
        self.drag_origin = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
        )

    def drag_window(self, event):
        if self.drag_origin is None:
            return
        pointer_x, pointer_y, window_x, window_y = self.drag_origin
        x = window_x + event.x_root - pointer_x
        y = window_y + event.y_root - pointer_y
        self.root.geometry(f"+{x}+{y}")

    def stop_window_drag(self, _event):
        self.drag_origin = None

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

    def on_canvas_configure(self, event):
        if not self.started:
            self.pos = float(event.width)
            self.started = True
        self.schedule_rebuild()

    def rebuild(self):
        self.rebuild_job = None
        self.font.configure(size=self.size_var.get())
        self.text_width = max(1, self.font.measure(self.text))
        if self.finished:
            self.pos = self.get_finished_text_position(
                self.canvas.winfo_width() / 2
            )
        self.canvas.itemconfigure(self.item, text=self.text)
        self.update_canvas_positions()

    def update_canvas_positions(self):
        center_y = self.canvas.winfo_height() / 2
        self.canvas.coords(self.item, self.pos, center_y)
        marker_x = self.get_end_marker_x()
        marker_radius = END_MARKER_SIZE / 2
        self.canvas.coords(
            self.end_marker,
            marker_x - marker_radius,
            center_y - marker_radius,
            marker_x + marker_radius,
            center_y + marker_radius,
        )

    # ---------- actions ----------
    def reload(self):
        try:
            self.text = read_text(self.args.file)
        except FileNotFoundError:
            self.text = f"File not found: {self.args.file}"
        self.finished = False
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
            if not self.finished:
                self.pos += self.direction * self.speed_var.get() * dt
                marker_x = self.get_end_marker_x()
                center_x = self.canvas.winfo_width() / 2
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