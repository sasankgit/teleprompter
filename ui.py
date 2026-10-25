"""Window and control UI for the scrolling signboard."""

import bisect
import ctypes
import os
import re
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

BG_COLOR = "#000000"
TEXT_COLOR = "#ffffff"
PANEL_BG = "#161616"
PANEL_FG = "#dddddd"
SPEED_MIN, SPEED_MAX = 20, 1000
SIZE_MIN, SIZE_MAX = 16, 200
END_MARKER_SIZE = 12
END_MARKER_GAP = 24

# The text is drawn as many small canvas items instead of one huge item.
# Tk on Windows cannot draw a single text item that is tens of thousands of
# pixels wide, so only the chunks that are on screen get drawn.
CHUNK_CHARS = 120
VISIBLE_MARGIN_PX = 300

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
CLICK_THROUGH_POLL_MS = 20

TOKEN_RE = re.compile(r"\S+\s*|\s+")


def clamp(value, low, high):
    return max(low, min(high, value))


class SignboardUI:
    @staticmethod
    def create_root():
        return tk.Tk()

    def __init__(self, root, controller, args, auto_controls=False):
        self.root = root
        self.controller = controller
        self.auto_controls = auto_controls
        self.drag_origin = None
        self.action_menu_hide_job = None
        self.click_through_job = None
        self.root_click_through = None
        self.menu_click_through = None
        self.speed_var = tk.IntVar(
            value=clamp(int(args.speed), SPEED_MIN, SPEED_MAX)
        )
        self.size_var = tk.IntVar(value=clamp(args.size, SIZE_MIN, SIZE_MAX))
        self.font = tkfont.Font(
            family="Helvetica", size=self.size_var.get(), weight="bold"
        )

        root.title("Text Signboard")
        root.configure(bg=BG_COLOR)
        root.geometry("1000x300")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.60)

        self.panel = tk.Frame(root, bg=PANEL_BG)
        self.panel.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="")
        self.interactive_widgets = []
        self.build_controls()
        self.add_drag_handle()
        self.add_action_menu()

        self.canvas = tk.Canvas(root, bg=BG_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Windowed text rendering state.
        self.text = ""
        self.layout_key = None
        self.chunks = []
        self.chunk_starts = []
        self.chunk_widths = []
        self.total_width = 0
        self.text_items = []
        self.item_chunk = []

        self.end_marker = self.canvas.create_oval(
            0, 0, END_MARKER_SIZE, END_MARKER_SIZE,
            fill="#ff0000", outline="#ff0000",
        )
        self.canvas.bind("<Configure>", controller.on_canvas_configure)

        self.bind_hotkeys()

        root.bind("<Destroy>", self.on_root_destroy, add="+")
        root.after_idle(self.show_controls)

    def bind_hotkeys(self):
        bindings = (
            ("<space>", lambda: self.controller.toggle_pause()),
            ("<Up>", lambda: self.controller.nudge(
                self.speed_var, 20, SPEED_MIN, SPEED_MAX
            )),
            ("<Down>", lambda: self.controller.nudge(
                self.speed_var, -20, SPEED_MIN, SPEED_MAX
            )),
            ("<plus>", lambda: self.controller.nudge(
                self.size_var, 4, SIZE_MIN, SIZE_MAX
            )),
            ("<equal>", lambda: self.controller.nudge(
                self.size_var, 4, SIZE_MIN, SIZE_MAX
            )),
            ("<KP_Add>", lambda: self.controller.nudge(
                self.size_var, 4, SIZE_MIN, SIZE_MAX
            )),
            ("<minus>", lambda: self.controller.nudge(
                self.size_var, -4, SIZE_MIN, SIZE_MAX
            )),
            ("<KP_Subtract>", lambda: self.controller.nudge(
                self.size_var, -4, SIZE_MIN, SIZE_MAX
            )),
            ("<r>", lambda: self.controller.reload()),
            ("<f>", lambda: self.controller.toggle_fullscreen()),
            ("<Escape>", lambda: self.root.destroy()),
        )
        for sequence, callback in bindings:
            handler = lambda _event, action=callback: action()
            self.root.bind(sequence, handler)

    def build_controls(self):
        if self.auto_controls:
            tk.Label(
                self.panel, text="Speed", bg=PANEL_BG, fg=PANEL_FG
            ).pack(side="left", padx=(10, 4), pady=6)
            self.add_speed_button("Slower", -1)
            self.add_speed_button("Faster", 1)
            self.add_slider(
                "", self.speed_var, SPEED_MIN, SPEED_MAX, lambda _value: None
            )
        else:
            self.add_slider(
                "Speed", self.speed_var, SPEED_MIN, SPEED_MAX,
                lambda _value: None,
            )

        self.add_slider(
            "Text size", self.size_var, SIZE_MIN, SIZE_MAX,
            lambda _value: self.controller.schedule_rebuild(),
        )

        if self.auto_controls:
            tk.Label(
                self.panel,
                textvariable=self.status_var,
                bg=PANEL_BG,
                fg="#aaaaaa",
                anchor="w",
                width=10,
                wraplength=90,
            ).pack(side="right", padx=4)

    def add_speed_button(self, label, delta):
        button = tk.Button(
            self.panel,
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
        self.interactive_widgets.append(button)
        button.bind(
            "<Enter>",
            lambda _event, direction=delta:
                self.controller.start_speed_hover(direction),
        )
        button.bind(
            "<Leave>", lambda _event: self.controller.stop_speed_hover()
        )

    def add_slider(self, label, variable, low, high, on_change):
        if label:
            tk.Label(
                self.panel, text=label, bg=PANEL_BG, fg=PANEL_FG
            ).pack(side="left", padx=(10, 4), pady=6)
        scale = tk.Scale(
            self.panel,
            from_=low,
            to=high,
            orient="horizontal",
            variable=variable,
            length=200,
            bg=PANEL_BG,
            fg=PANEL_FG,
            troughcolor="#333333",
            highlightthickness=0,
            takefocus=0,
            command=on_change,
        )
        scale.pack(side="left", padx=(0, 10))
        self.interactive_widgets.append(scale)
        scale.bind(
            "<ButtonRelease-1>", lambda _event: self.root.focus_set()
        )

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
        self.interactive_widgets.append(handle)
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
        self.interactive_widgets.append(self.action_button)
        self.action_button.bind("<Enter>", self.show_action_menu)
        self.action_button.bind("<Leave>", self.schedule_hide_action_menu)

        self.action_menu = tk.Toplevel(self.root)
        self.action_menu.withdraw()
        self.action_menu.overrideredirect(True)
        self.action_menu.attributes("-topmost", True)
        self.action_menu.attributes("-alpha", 0.60)
        self.action_menu.configure(bg=PANEL_BG)

        menu_frame = tk.Frame(self.action_menu, bg=PANEL_BG, padx=4, pady=4)
        menu_frame.grid(row=0, column=0)
        for column in range(3):
            menu_frame.grid_columnconfigure(column, weight=1)

        options = (
            ("Start over", 0, 1, self.controller.start_over),
            ("Reverse", 1, 0, self.controller.reverse_direction),
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

    def show_controls(self):
        self.root.deiconify()
        self.root.lift()
        if os.name == "nt":
            self.update_click_through()

    @staticmethod
    def set_window_style(window, click_through):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_ancestor = user32.GetAncestor
        get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        get_ancestor.restype = wintypes.HWND
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get_window_long = user32.GetWindowLongPtrW
            set_window_long = user32.SetWindowLongPtrW
            window_long_type = ctypes.c_ssize_t
        else:
            get_window_long = user32.GetWindowLongW
            set_window_long = user32.SetWindowLongW
            window_long_type = ctypes.c_long
        get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_window_long.restype = window_long_type
        set_window_long.argtypes = [
            ctypes.c_void_p, ctypes.c_int, window_long_type
        ]
        set_window_long.restype = window_long_type
        hwnd = get_ancestor(window.winfo_id(), 2)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        style = get_window_long(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED
        if click_through:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.set_last_error(0)
        set_window_long(hwnd, GWL_EXSTYLE, style)
        if ctypes.get_last_error():
            raise ctypes.WinError()

    @staticmethod
    def point_over_widgets(widgets, x, y):
        return any(
            widget.winfo_exists()
            and widget.winfo_rootx() <= x < widget.winfo_rootx() + widget.winfo_width()
            and widget.winfo_rooty() <= y < widget.winfo_rooty() + widget.winfo_height()
            for widget in widgets
        )

    def update_click_through(self):
        if not self.root.winfo_exists():
            return
        point = wintypes.POINT()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = ctypes.c_int
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError()

        self.root_click_through = self.update_window_click_through(
            self.root,
            self.interactive_widgets,
            self.root_click_through,
            point.x,
            point.y,
        )
        if self.action_menu.state() == "normal":
            self.menu_click_through = self.update_window_click_through(
                self.action_menu,
                self.action_menu_widgets,
                self.menu_click_through,
                point.x,
                point.y,
            )
        self.click_through_job = self.root.after(
            CLICK_THROUGH_POLL_MS, self.update_click_through
        )

    def update_window_click_through(self, window, widgets, current, x, y):
        click_through = not self.point_over_widgets(widgets, x, y)
        if click_through != current:
            self.set_window_style(window, click_through)
        return click_through

    def show_action_menu(self, _event=None):
        self.cancel_hide_action_menu()
        self.action_menu.update_idletasks()
        width = self.action_menu.winfo_reqwidth()
        height = self.action_menu.winfo_reqheight()
        x = (
            self.action_button.winfo_rootx()
            + self.action_button.winfo_width() // 2
            - width // 2
        )
        y = (
            self.action_button.winfo_rooty()
            + self.action_button.winfo_height() // 2
            - height // 2
        )
        x = clamp(x, 0, self.root.winfo_screenwidth() - width)
        y = clamp(y, 0, self.root.winfo_screenheight() - height)
        self.action_menu.geometry(f"+{x}+{y}")
        if os.name == "nt":
            self.set_window_style(self.action_menu, True)
            self.menu_click_through = True
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

    def on_root_destroy(self, event):
        if event.widget is not self.root:
            return
        for job_name in ("action_menu_hide_job", "click_through_job"):
            job = getattr(self, job_name)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, job_name, None)

    # ------------------------------------------------------------------
    # Windowed text rendering
    # ------------------------------------------------------------------
    @staticmethod
    def split_chunks(text):
        """Split text into ~CHUNK_CHARS pieces, preferring word boundaries."""
        chunks = []
        current = []
        current_len = 0
        for token in TOKEN_RE.findall(text):
            # A single very long "word" is cut into fixed-size slices.
            pieces = (
                [token[i:i + CHUNK_CHARS]
                 for i in range(0, len(token), CHUNK_CHARS)]
                if len(token) > CHUNK_CHARS
                else [token]
            )
            for piece in pieces:
                if current and current_len + len(piece) > CHUNK_CHARS:
                    chunks.append("".join(current))
                    current = []
                    current_len = 0
                current.append(piece)
                current_len += len(piece)
        if current:
            chunks.append("".join(current))
        return chunks

    def layout_text(self, text):
        """Measure every chunk once; cached per (text, font size)."""
        key = (text, self.font.cget("size"))
        if key == self.layout_key:
            return
        self.layout_key = key
        self.chunks = self.split_chunks(text)
        self.chunk_widths = [self.font.measure(chunk) for chunk in self.chunks]
        self.chunk_starts = []
        x = 0
        for width in self.chunk_widths:
            self.chunk_starts.append(x)
            x += width
        self.total_width = x
        # Force every pooled item to be re-assigned on the next draw.
        self.item_chunk = [None] * len(self.text_items)

    def set_text(self, text):
        self.text = text
        self.layout_text(text)

    def canvas_width(self):
        return self.canvas.winfo_width()

    def measure_text(self, text):
        self.layout_text(text)
        return self.total_width

    def ensure_text_items(self, count):
        while len(self.text_items) < count:
            item = self.canvas.create_text(
                0, 0, text="", anchor="w", fill=TEXT_COLOR, font=self.font,
                state="hidden",
            )
            self.text_items.append(item)
            self.item_chunk.append(None)

    def update_canvas_positions(self, text_x, marker_x):
        center_y = self.canvas.winfo_height() / 2
        width = self.canvas.winfo_width()

        # Visible range expressed in text coordinates.
        low = -text_x - VISIBLE_MARGIN_PX
        high = -text_x + width + VISIBLE_MARGIN_PX
        visible = []
        if self.chunks:
            first = max(0, bisect.bisect_right(self.chunk_starts, low) - 1)
            index = first
            while index < len(self.chunks) and self.chunk_starts[index] <= high:
                if self.chunk_starts[index] + self.chunk_widths[index] >= low:
                    visible.append(index)
                index += 1

        self.ensure_text_items(len(visible))
        for slot, chunk_index in enumerate(visible):
            item = self.text_items[slot]
            if self.item_chunk[slot] != chunk_index:
                self.canvas.itemconfigure(
                    item,
                    text=self.chunks[chunk_index],
                    font=self.font,
                    state="normal",
                )
                self.item_chunk[slot] = chunk_index
            self.canvas.coords(
                item, text_x + self.chunk_starts[chunk_index], center_y
            )
        for slot in range(len(visible), len(self.text_items)):
            if self.item_chunk[slot] is not None:
                self.canvas.itemconfigure(self.text_items[slot], state="hidden")
                self.item_chunk[slot] = None

        # The end marker is tiny; skip drawing it while it is far off screen.
        marker_radius = END_MARKER_SIZE / 2
        if -50 <= marker_x <= width + 50:
            self.canvas.itemconfigure(self.end_marker, state="normal")
            self.canvas.coords(
                self.end_marker,
                marker_x - marker_radius,
                center_y - marker_radius,
                marker_x + marker_radius,
                center_y + marker_radius,
            )
        else:
            self.canvas.itemconfigure(self.end_marker, state="hidden")

    def configure_font(self, size):
        self.font.configure(size=size)

    def destroy(self):
        if self.action_menu.winfo_exists():
            self.action_menu.destroy()