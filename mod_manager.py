# -*- coding: utf-8 -*-
"""
恋爱活动（Koikatsu）Mod 管理器
------------------------------------------------
流程：扫描压缩包 -> 7-Zip 解压到临时目录 -> 按扩展名分类 -> 移动到指定目录

默认规则：
    *.zipmod  ->  Mods 目录
    *.png     ->  卡片 / 图片目录
    其它文件  ->  可选，输出到「其它」目录

界面：原生 tkinter 窗口 + Canvas 自绘控件（现代简约 · 中性黑白灰）
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

# --------------------------------------------------------------------------
# 路径 / 常量
# --------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
WORK_DIR = APP_DIR / "_work"
TEMP_DIR = WORK_DIR / "temp"
BACKUP_DIR = WORK_DIR / "backup"
MANIFEST_PATH = WORK_DIR / "last_run.json"

ARCHIVE_EXTS = {".rar", ".zip", ".7z", ".001"}

# --- 配色：中性极简（黑白灰 + 状态色）---
PAGE = "#F5F5F6"        # 页面底色
CARD = "#FFFFFF"        # 卡片
LINE = "#E9E9EC"        # 分隔线 / 卡片描边
LINE2 = "#DCDCE1"       # 控件描边
TRACK = "#F1F1F3"       # 轨道 / 浅底
HOVER = "#F5F5F6"       # 悬停底
TEXT = "#1A1A1E"        # 主文字
TEXT2 = "#6E6E78"       # 次要文字
TEXT3 = "#A5A5AE"       # 弱化文字
DARK = "#1A1A1E"        # 主按钮
DARK_H = "#34343B"
DARK_D = "#4A4A52"
GREEN = "#16A34A"
RED = "#DC2626"
AMBER = "#D97706"

FONT = "Microsoft YaHei UI"

DEFAULT_CONFIG = {
    "watch_dir": "",
    "zipmod_dir": "",
    "png_dir": "",
    "misc_dir": "",
    "archive_dir": "",
    "seven_zip": "",
    "keep_archive": True,
    "copy_other": False,
    "clean_temp": True,
    "recursive": False,
    "conflict": "rename",       # rename | skip | overwrite
    "dry_run": False,
}

SCALE = 1.0


def u(n):
    """按屏幕 DPI 缩放尺寸"""
    return max(1, int(round(n * SCALE)))


def detect_dpi():
    try:
        from ctypes import windll
        dc = windll.user32.GetDC(0)
        dpi = windll.gdi32.GetDeviceCaps(dc, 88)   # LOGPIXELSX
        windll.user32.ReleaseDC(0, dc)
        return dpi or 96
    except Exception:
        return 96


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def find_seven_zip(hint: str = "") -> str:
    """定位 7z.exe"""
    cands = []
    if hint:
        cands.append(hint)
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "7-Zip", "7z.exe"))
    cands.append(r"C:\Program Files\7-Zip\7z.exe")
    cands.append(r"C:\Program Files (x86)\7-Zip\7z.exe")
    w = shutil.which("7z")
    if w:
        cands.append(w)
    for c in cands:
        try:
            if c and os.path.isfile(c):
                return c
        except Exception:
            continue
    return ""


def seven_zip_version(path: str) -> str:
    try:
        out = subprocess.run([path], capture_output=True, creationflags=0x08000000)
        txt = (out.stdout or b"").decode("utf-8", "replace")
        for line in txt.splitlines():
            if line.strip().startswith("7-Zip"):
                parts = line.strip().split()
                if len(parts) > 1:
                    return parts[1]
    except Exception:
        pass
    return ""


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def unique_path(p: Path) -> Path:
    """若目标已存在，追加 _1 / _2 ..."""
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 1
    while True:
        cand = p.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in name).strip(" .")
    return out[:80] or "archive"


def mix(c1: str, c2: str, t: float) -> str:
    """两个 #rrggbb 之间线性插值"""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def extract_archive(seven_zip: str, archive: Path, dest: Path):
    """解压到 dest，返回 (成功?, 错误信息)"""
    dest.mkdir(parents=True, exist_ok=True)

    if archive.suffix.lower() == ".zip":
        try:
            import zipfile
            with zipfile.ZipFile(archive) as z:
                z.extractall(dest)
            return True, ""
        except Exception:
            pass  # 交给 7-Zip 兜底

    if not seven_zip:
        return False, "未找到 7-Zip，无法解压（请在设置中指定 7z.exe）"

    cmd = [seven_zip, "x", "-y", "-sccUTF-8", f"-o{dest}", "--", str(archive)]
    try:
        p = subprocess.run(cmd, capture_output=True, creationflags=0x08000000)
    except Exception as e:
        return False, f"调用 7-Zip 失败：{e}"

    if p.returncode != 0:
        raw = (p.stdout or b"") + (p.stderr or b"")
        msg = raw.decode("utf-8", "replace").strip()
        lines = [ln for ln in msg.splitlines() if ln.strip()]
        tail = " / ".join(lines[-3:]) if lines else f"7-Zip 退出码 {p.returncode}"
        return False, tail
    return True, ""


def iter_files(root: Path):
    """递归遍历，跳过 macOS 垃圾文件"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("__MACOSX", ".git")]
        for fn in filenames:
            if fn in (".DS_Store", "Thumbs.db") or fn.startswith("._"):
                continue
            yield Path(dirpath) / fn


def rr_points(x1, y1, x2, y2, r):
    """圆角矩形的平滑多边形顶点"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


# --------------------------------------------------------------------------
# 自绘控件
# --------------------------------------------------------------------------
class Card(tk.Canvas):
    """圆角白卡片，内容放进 .body

    fill=True 时卡片高度由外部布局决定，body 撑满卡片（用于列表 / 日志）
    fill=False 时卡片高度跟随 body 内容（用于表单区）
    """

    def __init__(self, parent, pad=14, radius=12, page=PAGE, fill=False):
        # 非填充卡片给一个「能露出 body 顶边」的初始高度：
        # 画布若完全不含窗口项，Tk 不会映射它，body 永远拿不到尺寸，
        # _on_body 也就永不触发 —— 高度会卡死。
        seed = u(8) if fill else u(pad * 2 + 4)
        super().__init__(parent, bg=page, highlightthickness=0, bd=0, height=seed)
        self._pad = u(pad)
        self._radius = u(radius)
        self._fill = fill
        self._h = 0
        self.body = tk.Frame(self, bg=CARD)
        self._win = self.create_window(self._pad, self._pad,
                                       window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_resize)
        self.body.bind("<Configure>", self._on_body)

    def _on_body(self, e):
        if self._fill:
            return
        want = e.height + self._pad * 2
        if abs(want - self._h) > 1:
            self._h = want
            self.configure(height=want)

    def _on_resize(self, e):
        w, h = e.width, e.height
        if w < 4 or h < 4:
            return
        self.delete("shape")
        self.create_polygon(rr_points(0, 0, w - 1, h - 1, self._radius),
                            smooth=True, splinesteps=24,
                            fill=CARD, outline=LINE, width=1, tags="shape")
        self.tag_lower("shape")
        self.itemconfigure(self._win, width=max(1, w - self._pad * 2))
        if self._fill:
            self.itemconfigure(self._win, height=max(1, h - self._pad * 2))


class ScrollArea(tk.Frame):
    """纵向滚动容器。内容装得下时完全不显示滚动条，装不下才出现细滚动条。"""

    def __init__(self, parent, page=PAGE):
        super().__init__(parent, bg=page)
        self._page = page
        self._bar_on = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, bg=page, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(self, orient="vertical",
                                 style="Thin.Vertical.TScrollbar",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)

        self.inner = tk.Frame(self.canvas, bg=page)
        self._win = self.canvas.create_window(u(0), u(0), window=self.inner,
                                              anchor="nw")
        self.inner.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        # 内容高度与宽度无关（卡片行高固定），所以显示滚动条不会引起震荡
        self.canvas.itemconfigure(self._win, width=max(1, e.width))

    def _on_scroll_set(self, first, last):
        need = not (float(first) <= 0.0 and float(last) >= 1.0)
        if need and not self._bar_on:
            self.bar.grid(row=0, column=1, sticky="ns", padx=(u(6), 0))
            self._bar_on = True
            self._bind_wheel()
        elif not need and self._bar_on:
            self.bar.grid_forget()
            self._bar_on = False
        self.bar.set(first, last)

    def _bind_wheel(self):
        def walk(w):
            w.bind("<MouseWheel>", self._on_wheel)
            for child in w.winfo_children():
                walk(child)
        walk(self.inner)

    def _on_wheel(self, e):
        if self._bar_on:
            self.canvas.yview_scroll(int(-e.delta / 120), "units")


def draw_icon(c, name, cx, cy, s, color, width=None):
    """在画布上画线性图标（s 为图标边长）"""
    h = s / 2.0
    w = width or max(1.2, u(1.3))

    def line(x1, y1, x2, y2):
        c.create_line(cx + x1, cy + y1, cx + x2, cy + y2,
                      fill=color, width=w, capstyle="round")

    if name == "folder":
        c.create_polygon(cx - h, cy + h * 0.72, cx - h, cy - h * 0.70,
                         cx - h * 0.22, cy - h * 0.70, cx + h * 0.10, cy - h * 0.28,
                         cx + h, cy - h * 0.28, cx + h, cy + h * 0.72,
                         fill="", outline=color, width=w, joinstyle="round")
    elif name == "plus":
        line(-h * 0.78, 0, h * 0.78, 0)
        line(0, -h * 0.78, 0, h * 0.78)
    elif name == "x":
        line(-h * 0.66, -h * 0.66, h * 0.66, h * 0.66)
        line(h * 0.66, -h * 0.66, -h * 0.66, h * 0.66)
    elif name == "stop":
        c.create_polygon(rr_points(cx - h * 0.62, cy - h * 0.62,
                                   cx + h * 0.62, cy + h * 0.62, u(2)),
                         smooth=True, splinesteps=12,
                         fill="", outline=color, width=w)
    elif name == "play":
        c.create_polygon(cx - h * 0.46, cy - h * 0.70,
                         cx + h * 0.74, cy,
                         cx - h * 0.46, cy + h * 0.70,
                         fill=color, outline="", joinstyle="round")
    elif name == "undo":
        c.create_arc(cx - h * 0.72, cy - h * 0.72, cx + h * 0.72, cy + h * 0.72,
                     start=25, extent=275, style="arc", outline=color, width=w)
        c.create_polygon(cx - h * 0.98, cy - h * 0.12,
                         cx - h * 0.34, cy - h * 0.34,
                         cx - h * 0.72, cy + h * 0.36,
                         fill=color, outline="")
    elif name == "trash":
        line(-h * 0.80, -h * 0.50, h * 0.80, -h * 0.50)
        c.create_polygon(rr_points(cx - h * 0.58, cy - h * 0.34,
                                   cx + h * 0.58, cy + h * 0.82, u(2)),
                         smooth=True, splinesteps=10,
                         fill="", outline=color, width=w)
        line(0, -h * 0.86, 0, -h * 0.50)
    elif name == "search":
        c.create_oval(cx - h * 0.82, cy - h * 0.82, cx + h * 0.22, cy + h * 0.22,
                      fill="", outline=color, width=w)
        line(h * 0.08, cy + h * 0.08, h * 0.78, cy + h * 0.78)


class Btn(tk.Canvas):
    """圆角按钮。variant: primary / ghost / quiet / danger"""

    def __init__(self, parent, text="", command=None, variant="ghost",
                 icon=None, height=36, radius=8, pad_x=14, font_size=10,
                 expand=False, page=CARD, bold=False):
        self._expand = expand
        self._text = text
        self._icon = icon
        self._variant = variant
        self._radius = u(radius)
        self._pad_x = u(pad_x)
        self._font = tkfont.Font(family=FONT, size=font_size,
                                 weight="bold" if bold else "normal")
        self._h = u(height)
        self._icon_s = u(15)
        self._hover = False
        self._press = False
        self._enabled = True

        w = self._pad_x * 2
        if icon:
            w += self._icon_s + (u(6) if text else 0)
        if text:
            w += self._font.measure(text)

        super().__init__(parent, bg=page, highlightthickness=0, bd=0,
                         width=u(40) if expand else w, height=self._h,
                         cursor="hand2")
        self.command = command
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._hovering(True))
        self.bind("<Leave>", lambda e: self._hovering(False))
        self.bind("<Button-1>", self._on_down)
        self.bind("<ButtonRelease-1>", self._on_up)

    def _hovering(self, flag):
        self._hover = flag
        if not flag:
            self._press = False
        self._draw()

    def _on_down(self, _e):
        if self._enabled:
            self._press = True
            self._draw()

    def _on_up(self, _e):
        was = self._press
        self._press = False
        self._draw()
        if was and self._enabled and self.command:
            self.command()

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def _palette(self):
        v = self._variant
        if not self._enabled:
            if v == "primary":
                return "#CFCFD5", "#CFCFD5", "#FFFFFF"
            return CARD, LINE, TEXT3
        if v == "primary":
            bg = DARK_D if self._press else (DARK_H if self._hover else DARK)
            return bg, bg, "#FFFFFF"
        if v == "danger":
            bg = "#FEE2E2" if (self._hover or self._press) else CARD
            return bg, ("#FCA5A5" if self._hover else LINE2), RED
        if v == "quiet":
            bg = HOVER if (self._hover or self._press) else CARD
            return bg, bg, (TEXT if self._hover else TEXT2)
        bg = TRACK if self._press else (HOVER if self._hover else CARD)
        return bg, ("#C9C9CF" if self._hover else LINE2), TEXT

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        fill, outline, fg = self._palette()
        self.create_polygon(rr_points(0, 0, w - 1, h - 1, self._radius),
                            smooth=True, splinesteps=16,
                            fill=fill, outline=outline, width=1)

        content = 0
        if self._icon:
            content += self._icon_s + (u(6) if self._text else 0)
        if self._text:
            content += self._font.measure(self._text)
        x = (w - content) / 2
        cy = h / 2

        if self._icon:
            draw_icon(self, self._icon, x + self._icon_s / 2, cy, self._icon_s, fg)
            x += self._icon_s + u(6)
        if self._text:
            self.create_text(x, cy, text=self._text, anchor="w", fill=fg,
                             font=self._font)


class Switch(tk.Canvas):
    """开关"""

    def __init__(self, parent, variable, command=None, page=CARD, height=20):
        self._sw = u(34)
        self._sh = u(height)
        super().__init__(parent, width=self._sw, height=self._sh, bg=page,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.command = command
        self._pos = 1.0 if variable.get() else 0.0
        self._target = self._pos
        self._animating = False
        self._hover = False
        self._draw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        variable.trace_add("write",
                           lambda *a: self._animate(1.0 if variable.get() else 0.0))

    def _set_hover(self, flag):
        self._hover = flag
        self._draw()

    def _toggle(self, _e):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _animate(self, target):
        self._target = target
        if not self._animating:
            self._animating = True
            self._step()

    def _step(self):
        d = self._target - self._pos
        if abs(d) < 0.02:
            self._pos = self._target
            self._draw()
            self._animating = False
            return
        self._pos += d * 0.34
        self._draw()
        self.after(12, self._step)

    def _draw(self):
        self.delete("all")
        w, h = self._sw, self._sh
        r = h / 2.0
        color = mix("#E3E3E7", DARK, self._pos)
        if self._hover:
            color = mix(color, "#000000", 0.10)
        self.create_oval(0, 0, h, h, fill=color, outline="")
        self.create_oval(w - h, 0, w, h, fill=color, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=color, outline="")
        k = h - u(6)
        kx = u(3) + self._pos * (w - h)
        self.create_oval(kx, u(3), kx + k, u(3) + k, fill="#FFFFFF", outline="")


class Segmented(tk.Canvas):
    """分段选择器"""

    def __init__(self, parent, options, variable, height=34, page=CARD):
        self._opts = options
        self._height = u(height)
        self._font = tkfont.Font(family=FONT, size=9)
        self.var = variable
        widths = [self._font.measure(lb) + u(26) for lb, _ in options]
        self._widths = widths
        super().__init__(parent, width=sum(widths), height=self._height,
                         bg=page, highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", self._on_click)
        variable.trace_add("write", lambda *a: self._draw())

    def _index(self):
        val = self.var.get()
        for i, (_, v) in enumerate(self._opts):
            if v == val:
                return i
        return 0

    def _on_click(self, e):
        acc = 0
        for i, w in enumerate(self._widths):
            if e.x < acc + w:
                self.var.set(self._opts[i][1])
                return
            acc += w

    def _draw(self):
        self.delete("all")
        h = self._height
        w = sum(self._widths)
        self.create_polygon(rr_points(0, 0, w - 1, h - 1, u(7)),
                            smooth=True, splinesteps=16, fill=TRACK, outline="")
        i = self._index()
        x1 = sum(self._widths[:i]) + u(2)
        x2 = x1 + self._widths[i] - u(4)
        self.create_polygon(rr_points(x1, u(2), x2, h - u(2), u(6)),
                            smooth=True, splinesteps=16,
                            fill=CARD, outline=LINE2, width=1)
        cx = 0
        for j, (lb, _) in enumerate(self._opts):
            self.create_text(cx + self._widths[j] / 2, h / 2, text=lb,
                             fill=TEXT if j == i else TEXT2, font=self._font)
            cx += self._widths[j]


class Input(tk.Canvas):
    """圆角输入框"""

    def __init__(self, parent, textvariable, height=36, radius=8, page=CARD):
        self._h = u(height)
        self._radius = u(radius)
        super().__init__(parent, height=self._h, highlightthickness=0, bd=0,
                         bg=page, width=u(60))
        self.entry = tk.Entry(self, textvariable=textvariable, bd=0, relief="flat",
                              bg=CARD, fg=TEXT, insertbackground=TEXT,
                              font=(FONT, 9), highlightthickness=0,
                              selectbackground="#DCDCE1", selectforeground=TEXT)
        self._win = self.create_window(u(11), self._h / 2, window=self.entry,
                                       anchor="w")
        self._focused = False
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.entry.bind("<FocusIn>", self._on_focus)
        self.entry.bind("<FocusOut>", self._on_blur)
        self.entry.bind("<Enter>", lambda e: self._set_hover(True))
        self.entry.bind("<Leave>", lambda e: self._set_hover(False))

    def _set_hover(self, flag):
        self._hover = flag
        self._draw()

    def _on_focus(self, _e):
        self._focused = True
        self._draw()

    def _on_blur(self, _e):
        self._focused = False
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        self.delete("shape")
        if self._focused:
            outline, width = DARK, u(1.5)
        elif self._hover:
            outline, width = "#C9C9CF", 1
        else:
            outline, width = LINE2, 1
        self.create_polygon(rr_points(0, 0, w - 1, h - 1, self._radius),
                            smooth=True, splinesteps=16,
                            fill=CARD, outline=outline, width=width, tags="shape")
        self.tag_lower("shape")
        self.itemconfigure(self._win, width=max(u(20), w - u(22)))


class Progress(tk.Canvas):
    """细圆角进度条"""

    def __init__(self, parent, height=4, page=PAGE):
        self._h = u(height)
        super().__init__(parent, height=self._h, bg=page,
                         highlightthickness=0, bd=0)
        self._v, self._m = 0, 1
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value, maximum=None):
        self._v = value
        if maximum:
            self._m = max(1, maximum)
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2:
            return
        r = h / 2.0
        self.create_oval(0, 0, h, h, fill=TRACK, outline="")
        self.create_oval(w - h, 0, w, h, fill=TRACK, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=TRACK, outline="")
        frac = 0.0 if self._m <= 0 else max(0.0, min(1.0, self._v / self._m))
        fw = w * frac
        if fw <= h:
            return
        self.create_oval(0, 0, h, h, fill=DARK, outline="")
        self.create_oval(fw - h, 0, fw, h, fill=DARK, outline="")
        self.create_rectangle(r, 0, fw - r, h, fill=DARK, outline="")


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------
class ModManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.msg_q = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.files = []
        self._closing = False
        self.seven_zip = find_seven_zip(self.cfg.get("seven_zip", ""))

        try:
            self.root.tk.call("tk", "scaling", detect_dpi() / 72.0)
        except Exception:
            pass

        self._build_style()
        self._build_ui()
        self._load_into_ui()
        self._refresh_list()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._pump)

        self.log("Mod 管理器已就绪。")
        if self.seven_zip:
            self.log(f"解压引擎：7-Zip {seven_zip_version(self.seven_zip)}".rstrip())
        else:
            self.log("未检测到 7-Zip，RAR 将无法解压。请到 7-zip.org 安装。", "warn")
        self.log("设置三个目录后，点「扫描压缩包」开始。", "dim")

    # ---------------- ttk 样式（列表 / 滚动条）----------------
    def _build_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=PAGE, foreground=TEXT, font=(FONT, 9))

        st.configure("Flat.Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, borderwidth=0, relief="flat",
                     rowheight=u(32), font=(FONT, 9))
        st.map("Flat.Treeview",
               background=[("selected", TRACK)],
               foreground=[("selected", TEXT)])
        st.configure("Flat.Treeview.Heading", background=CARD, foreground=TEXT3,
                     relief="flat", borderwidth=0, font=(FONT, 8),
                     padding=(u(6), u(7)))
        st.map("Flat.Treeview.Heading", background=[("active", CARD)])
        st.layout("Flat.Treeview", [("Flat.Treeview.treearea", {"sticky": "nswe"})])

        for orient in ("Vertical", "Horizontal"):
            name = f"Thin.{orient}.TScrollbar"
            st.configure(name, gripcount=0, background="#D4D4D9",
                         darkcolor="#D4D4D9", lightcolor="#D4D4D9",
                         troughcolor=CARD, bordercolor=CARD,
                         arrowcolor=CARD, arrowsize=1, relief="flat")
            st.map(name, background=[("active", "#B4B4BC")])

    # ---------------- 布局 ----------------
    def _build_ui(self):
        self.root.configure(bg=PAGE)
        self.root.title("Mod 管理器")
        self.root.geometry(f"{u(1180)}x{u(940)}")
        self.root.minsize(u(1010), u(640))

        outer = tk.Frame(self.root, bg=PAGE)
        outer.pack(fill="both", expand=True, padx=u(20), pady=u(18))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        self._build_header(outer)

        body = tk.Frame(outer, bg=PAGE)
        body.grid(row=1, column=0, sticky="nsew", pady=(u(16), 0))
        body.columnconfigure(0, minsize=u(384))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ScrollArea(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, u(16)))
        self.left_scroll = left
        right = tk.Frame(body, bg=PAGE)
        right.grid(row=0, column=1, sticky="nsew")

        self._build_left(left.inner)
        self._build_right(right)
        self._build_status(outer)

    def _build_header(self, parent):
        head = tk.Frame(parent, bg=PAGE)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)

        left = tk.Frame(head, bg=PAGE)
        left.grid(row=0, column=0, sticky="w")
        tk.Label(left, text="Mod 管理器", bg=PAGE, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(left, text="恋爱活动 · 压缩包自动解压分类归位", bg=PAGE,
                 fg=TEXT2, font=(FONT, 9)).pack(anchor="w", pady=(u(2), 0))

        self.chip = tk.Canvas(head, height=u(26), bg=PAGE,
                              highlightthickness=0, bd=0)
        self.chip.grid(row=0, column=2, sticky="e", pady=(u(6), 0))
        self._draw_chip()

    def _draw_chip(self):
        c = self.chip
        c.delete("all")
        f = tkfont.Font(family=FONT, size=8)
        if self.seven_zip:
            txt = f"7-Zip {seven_zip_version(self.seven_zip)}".strip()
            color = GREEN
        else:
            txt, color = "未找到 7-Zip", AMBER
        w = f.measure(txt) + u(36)
        h = u(26)
        c.configure(width=w)
        c.create_polygon(rr_points(0, 0, w - 1, h - 1, h / 2),
                         smooth=True, splinesteps=16,
                         fill=CARD, outline=LINE, width=1)
        c.create_oval(u(11), h / 2 - u(3), u(17), h / 2 + u(3),
                      fill=color, outline="")
        c.create_text(u(23), h / 2, text=txt, anchor="w", fill=TEXT2, font=f)

    def _section(self, parent, row, text, sub=None):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, u(7)))
        tk.Label(wrap, text=text, bg=CARD, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(side="left")
        if sub:
            tk.Label(wrap, text=sub, bg=CARD, fg=TEXT3,
                     font=(FONT, 8)).pack(side="left", padx=(u(8), 0))
        return wrap

    # --- 左侧 ---
    def _build_left(self, parent):
        self.card_dir = Card(parent)
        self.card_dir.grid(row=0, column=0, sticky="ew")
        b = self.card_dir.body
        b.columnconfigure(0, weight=1)

        self._section(b, 0, "目录")
        self.var_watch = tk.StringVar()
        self.var_zipmod = tk.StringVar()
        self.var_png = tk.StringVar()
        self.var_misc = tk.StringVar()
        self.var_archive = tk.StringVar()

        row = 1
        for label, var in (("待处理目录", self.var_watch),
                           ("zipmod 目标", self.var_zipmod),
                           ("png 目标", self.var_png),
                           ("其它文件", self.var_misc),
                           ("归档目录", self.var_archive)):
            self._path_row(b, row, label, var)
            row += 1

        tk.Label(b, text="归档目录留空时，自动使用「待处理目录 / _已处理」",
                 bg=CARD, fg=TEXT3, font=(FONT, 8), anchor="w").grid(
            row=row, column=0, sticky="w", pady=(u(2), 0))
        row += 1
        Btn(b, "从游戏目录一键填充", icon="folder", variant="ghost",
            height=30, pad_x=12, font_size=9,
            command=self.fill_from_game_dir).grid(
            row=row, column=0, sticky="w", pady=(u(10), 0))

        # ── 处理选项 ──
        self.card_opt = Card(parent)
        self.card_opt.grid(row=1, column=0, sticky="ew", pady=(u(12), 0))
        b2 = self.card_opt.body
        b2.columnconfigure(0, weight=1)
        self._section(b2, 0, "处理选项")

        self.var_keep = tk.BooleanVar()
        self.var_other = tk.BooleanVar()
        self.var_clean = tk.BooleanVar()
        self.var_rec = tk.BooleanVar()
        self.var_dry = tk.BooleanVar()

        r = 1
        for var, text in ((self.var_keep, "整理后把原压缩包移入归档目录"),
                          (self.var_other, "保留未分类文件"),
                          (self.var_clean, "清空临时解压目录"),
                          (self.var_rec, "扫描子目录里的压缩包"),
                          (self.var_dry, "试运行（只预览，不移动文件）")):
            self._switch_row(b2, r, var, text)
            r += 1

        # ── 重名策略 ──
        self.card_rn = Card(parent)
        self.card_rn.grid(row=2, column=0, sticky="ew", pady=(u(12), 0))
        b3 = self.card_rn.body
        b3.columnconfigure(0, weight=1)
        self._section(b3, 0, "重名策略")
        self.var_conflict = tk.StringVar()
        Segmented(b3, [("自动改名", "rename"),
                       ("跳过", "skip"),
                       ("覆盖", "overwrite")],
                  self.var_conflict).grid(row=1, column=0, sticky="w")

        # ── 操作 ──
        act = tk.Frame(parent, bg=PAGE)
        act.grid(row=3, column=0, sticky="ew", pady=(u(12), 0))
        act.columnconfigure(0, weight=1)
        act.columnconfigure(1, weight=1)

        self.btn_run = Btn(act, "开始整理", variant="primary", icon="play",
                           height=40, radius=9, font_size=10, bold=True,
                           expand=True, page=PAGE, command=self.do_process)
        self.btn_run.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, u(7)))

        self.btn_scan = Btn(act, "扫描压缩包", icon="search", variant="ghost",
                            height=34, page=PAGE, command=self.do_scan)
        self.btn_scan.grid(row=1, column=0, sticky="ew", padx=(0, u(4)), pady=(0, u(7)))
        Btn(act, "添加压缩包", icon="plus", variant="ghost", height=34,
            page=PAGE, command=self.do_add_files).grid(
            row=1, column=1, sticky="ew", padx=(u(4), 0), pady=(0, u(7)))

        self.btn_stop = Btn(act, "停止", icon="stop", variant="ghost", height=34,
                            page=PAGE, command=self.do_stop)
        self.btn_stop.grid(row=2, column=0, sticky="ew", padx=(0, u(4)))
        Btn(act, "撤销上次", icon="undo", variant="ghost", height=34,
            page=PAGE, command=self.do_undo).grid(
            row=2, column=1, sticky="ew", padx=(u(4), 0))
        self.btn_stop.set_enabled(False)

    def _path_row(self, parent, row, label, var):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.grid(row=row, column=0, sticky="ew", pady=(0, u(5)))
        wrap.columnconfigure(1, weight=1)

        tk.Label(wrap, text=label, bg=CARD, fg=TEXT2, font=(FONT, 9),
                 anchor="w", width=11).grid(row=0, column=0, sticky="w")
        Input(wrap, var, height=34).grid(row=0, column=1, sticky="ew", padx=(u(8), u(6)))
        Btn(wrap, "", icon="folder", variant="ghost", height=32, radius=8,
            pad_x=0, page=CARD, command=lambda v=var: self.pick_dir(v)).grid(
            row=0, column=2)
        return wrap

    def _switch_row(self, parent, row, var, text):
        wrap = tk.Frame(parent, bg=CARD)
        wrap.grid(row=row, column=0, sticky="ew", pady=u(2))
        Switch(wrap, var).pack(side="left")
        lb = tk.Label(wrap, text=text, bg=CARD, fg=TEXT, font=(FONT, 9),
                      anchor="w", cursor="hand2")
        lb.pack(side="left", padx=(u(10), 0))
        lb.bind("<Button-1>", lambda e, v=var: v.set(not v.get()))
        return wrap

    # --- 右侧 ---
    def _build_right(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=2)

        self.card_list = Card(parent, fill=True)
        self.card_list.grid(row=0, column=0, sticky="nsew")
        b = self.card_list.body
        b.columnconfigure(0, weight=1)
        b.rowconfigure(1, weight=1)

        head = tk.Frame(b, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", pady=(0, u(10)))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="压缩包", bg=CARD, fg=TEXT,
                 font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w")
        self.count_lbl = tk.Label(head, text="共 0 个", bg=CARD, fg=TEXT3,
                                  font=(FONT, 8))
        self.count_lbl.grid(row=0, column=1, sticky="w", padx=(u(8), 0))
        Btn(head, "清空", icon="trash", variant="quiet", height=28,
            pad_x=10, font_size=8, command=self.clear_list).grid(
            row=0, column=2, sticky="e")

        holder = tk.Frame(b, bg=CARD)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        cols = ("name", "size", "status")
        self.tree = ttk.Treeview(holder, columns=cols, show="headings",
                                 style="Flat.Treeview", selectmode="extended")
        self.tree.heading("name", text="文件", anchor="w")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.heading("status", text="状态", anchor="w")
        self.tree.column("name", width=u(330), anchor="w")
        self.tree.column("size", width=u(78), anchor="e", stretch=False)
        self.tree.column("status", width=u(200), anchor="w")
        vs = ttk.Scrollbar(holder, orient="vertical",
                           style="Thin.Vertical.TScrollbar", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns", padx=(u(6), 0))
        self.tree.tag_configure("ok", foreground=GREEN)
        self.tree.tag_configure("fail", foreground=RED)
        self.tree.tag_configure("skip", foreground=TEXT3)
        self.tree.tag_configure("dry", foreground=AMBER)
        self.tree.bind("<Double-1>", self._open_selected_folder)

        self.empty_lbl = tk.Label(holder, bg=CARD, fg=TEXT3, font=(FONT, 9),
                                  justify="center", cursor="hand2",
                                  text="还没有压缩包\n\n选好「待处理目录」后点下方"
                                       "「扫描压缩包」\n也可以直接「添加压缩包」")
        self.empty_lbl.bind("<Button-1>", lambda e: self.do_scan())

        self.card_log = Card(parent, fill=True)
        self.card_log.grid(row=1, column=0, sticky="nsew", pady=(u(12), 0))
        b2 = self.card_log.body
        b2.columnconfigure(0, weight=1)
        b2.rowconfigure(1, weight=1)

        head2 = tk.Frame(b2, bg=CARD)
        head2.grid(row=0, column=0, sticky="ew", pady=(0, u(10)))
        head2.columnconfigure(1, weight=1)
        tk.Label(head2, text="运行日志", bg=CARD, fg=TEXT,
                 font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w")
        Btn(head2, "清空", variant="quiet", height=28, pad_x=10,
            font_size=8, command=self.clear_log).grid(row=0, column=2, sticky="e")

        holder2 = tk.Frame(b2, bg=CARD)
        holder2.grid(row=1, column=0, sticky="nsew")
        holder2.rowconfigure(0, weight=1)
        holder2.columnconfigure(0, weight=1)

        self.log_text = tk.Text(holder2, bg=CARD, fg=TEXT2, bd=0, relief="flat",
                                wrap="word", padx=0, pady=0, font=(FONT, 9),
                                insertbackground=TEXT, highlightthickness=0,
                                selectbackground="#DCDCE1", selectforeground=TEXT)
        ls = ttk.Scrollbar(holder2, orient="vertical",
                           style="Thin.Vertical.TScrollbar", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ls.grid(row=0, column=1, sticky="ns", padx=(u(6), 0))
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("dim", foreground=TEXT3)

    def _build_status(self, parent):
        bar = tk.Frame(parent, bg=PAGE)
        bar.grid(row=2, column=0, sticky="ew", pady=(u(12), 0))
        bar.columnconfigure(0, weight=1)

        self.progress = Progress(bar)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.status_lbl = tk.Label(bar, text="就绪", bg=PAGE, fg=TEXT3,
                                   font=(FONT, 8), anchor="w")
        self.status_lbl.grid(row=1, column=0, sticky="w", pady=(u(7), 0))
        self.summary_lbl = tk.Label(bar, text="", bg=PAGE, fg=TEXT3,
                                    font=(FONT, 8), anchor="e")
        self.summary_lbl.grid(row=1, column=1, sticky="e", pady=(u(7), 0))

    # ---------------- 界面 <-> 配置 ----------------
    def _load_into_ui(self):
        c = self.cfg
        self.var_watch.set(c.get("watch_dir", ""))
        self.var_zipmod.set(c.get("zipmod_dir", ""))
        self.var_png.set(c.get("png_dir", ""))
        self.var_misc.set(c.get("misc_dir", ""))
        self.var_archive.set(c.get("archive_dir", ""))
        self.var_keep.set(bool(c.get("keep_archive", True)))
        self.var_other.set(bool(c.get("copy_other", False)))
        self.var_clean.set(bool(c.get("clean_temp", True)))
        self.var_rec.set(bool(c.get("recursive", False)))
        self.var_dry.set(bool(c.get("dry_run", False)))
        self.var_conflict.set(c.get("conflict", "rename"))

    def _grab_ui(self) -> dict:
        return {
            "watch_dir": self.var_watch.get().strip(),
            "zipmod_dir": self.var_zipmod.get().strip(),
            "png_dir": self.var_png.get().strip(),
            "misc_dir": self.var_misc.get().strip(),
            "archive_dir": self.var_archive.get().strip(),
            "seven_zip": self.seven_zip,
            "keep_archive": bool(self.var_keep.get()),
            "copy_other": bool(self.var_other.get()),
            "clean_temp": bool(self.var_clean.get()),
            "recursive": bool(self.var_rec.get()),
            "dry_run": bool(self.var_dry.get()),
            "conflict": self.var_conflict.get() or "rename",
        }

    # ---------------- 小工具 ----------------
    def pick_dir(self, var):
        init = var.get() or self.var_watch.get() or str(Path.home())
        d = filedialog.askdirectory(title="选择目录", initialdir=init)
        if d:
            var.set(os.path.normpath(d))

    def fill_from_game_dir(self):
        d = filedialog.askdirectory(
            title="选择游戏根目录（含 Koikatu.exe 的那一层）",
            initialdir=self.var_watch.get() or None)
        if not d:
            return
        g = Path(os.path.normpath(d))
        hits = []
        for name in ("Mods", "mods"):
            if (g / name).is_dir():
                self.var_zipmod.set(str(g / name))
                hits.append("zipmod → Mods")
                break
        else:
            self.var_zipmod.set(str(g / "Mods"))
            hits.append("zipmod → Mods（将新建）")
        if not self.var_png.get():
            for sub in ("UserData/chara/female", "UserData/chara", "UserData/coordinate"):
                p = g / sub.replace("/", os.sep)
                if p.is_dir():
                    self.var_png.set(str(p))
                    hits.append(f"png → {sub}")
                    break
        self.log(("已根据游戏目录填充：" + "，".join(hits)) if hits
                 else "未在所选目录中找到 Mods / UserData 子目录，请手动指定。")

    # ---------------- 日志 / 队列 ----------------
    def log(self, text, tag=None):
        self.msg_q.put(("log", (text, tag)))

    def _pump(self):
        if self._closing or not self.root.winfo_exists():
            return
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self._append_log(*payload)
                elif kind == "progress":
                    self.progress.set(payload[0], payload[1])
                elif kind == "status":
                    self.status_lbl.config(text=payload)
                elif kind == "summary":
                    self.summary_lbl.config(text=payload)
                elif kind == "file":
                    idx, status, detail, tag = payload
                    if 0 <= idx < len(self.files):
                        self.files[idx]["status"] = status
                        self.files[idx]["detail"] = detail
                        self._refresh_row(idx, tag)
                elif kind == "done":
                    self._on_done(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _append_log(self, text, tag=None):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{ts}  ", "dim")
        self.log_text.insert("end", text + "\n", tag or "")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ---------------- 列表 ----------------
    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(self.files):
            self.tree.insert("", "end", iid=str(i), values=self._row_values(item))
        self.count_lbl.config(text=f"共 {len(self.files)} 个")
        if self.files:
            self.empty_lbl.place_forget()
        else:
            self.empty_lbl.place(relx=0.5, rely=0.40, anchor="center")

    def _row_values(self, item):
        try:
            size = human_size(item["path"].stat().st_size)
        except Exception:
            size = "—"
        st = item["status"]
        if item.get("detail"):
            st = f"{st} · {item['detail']}"
        return (item["path"].name, size, st)

    def _refresh_row(self, idx, tag=None):
        iid = str(idx)
        if not self.tree.exists(iid):
            return
        self.tree.item(iid, values=self._row_values(self.files[idx]))
        if tag:
            self.tree.item(iid, tags=(tag,))
        self.tree.see(iid)

    def clear_list(self):
        if self.worker and self.worker.is_alive():
            return
        self.files = []
        self._refresh_list()
        self.status_lbl.config(text="就绪")
        self.summary_lbl.config(text="")
        self.progress.set(0, 1)

    def _open_selected_folder(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.files[int(sel[0])]
        try:
            if item["path"].exists():
                subprocess.Popen(["explorer", "/select,", str(item["path"])])
        except Exception:
            pass

    def _add_paths(self, paths):
        exist = {str(it["path"]).lower() for it in self.files}
        added = 0
        for p in paths:
            p = Path(p)
            if str(p).lower() in exist:
                continue
            self.files.append({"path": p, "status": "待处理", "detail": ""})
            exist.add(str(p).lower())
            added += 1
        self.files.sort(key=lambda x: x["path"].name.lower())
        self._refresh_list()
        self.log(f"已添加 {added} 个压缩包。")

    # ---------------- 扫描 ----------------
    def do_scan(self):
        if self.worker and self.worker.is_alive():
            return
        watch = self.var_watch.get().strip()
        if not watch or not os.path.isdir(watch):
            messagebox.showwarning("提示", "请先选择有效的「待处理目录」。")
            return
        root = Path(watch)
        it = root.rglob("*") if self.var_rec.get() else root.glob("*")
        found = [p for p in it
                 if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS
                 and not p.name.startswith("~$")]
        self.files = [{"path": p, "status": "待处理", "detail": ""} for p in found]
        self.files.sort(key=lambda x: x["path"].name.lower())
        self._refresh_list()
        self.progress.set(0, max(len(found), 1))
        self.status_lbl.config(text=f"扫描完成，找到 {len(found)} 个压缩包")
        self.summary_lbl.config(text="")
        self.log(f"扫描 {watch} → 找到 {len(found)} 个压缩包"
                 + ("（含子目录）" if self.var_rec.get() else ""))

    def do_add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择压缩包",
            initialdir=self.var_watch.get() or None,
            filetypes=[("压缩包", "*.rar *.zip *.7z *.001"), ("所有文件", "*.*")])
        if paths:
            self._add_paths(paths)

    # ---------------- 开始整理 ----------------
    def do_process(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "任务正在运行中。")
            return
        if not self.files:
            messagebox.showwarning("提示", "列表为空，请先「扫描压缩包」或「添加压缩包」。")
            return

        cfg = self._grab_ui()
        if not cfg["zipmod_dir"]:
            messagebox.showwarning("提示", "请设置「zipmod 目标」目录。")
            return
        if not cfg["png_dir"]:
            messagebox.showwarning("提示", "请设置「png 目标」目录。")
            return
        if cfg["copy_other"] and not cfg["misc_dir"]:
            messagebox.showwarning("提示", "开启了「保留未分类文件」，请设置「其它文件」目录。")
            return

        self.seven_zip = find_seven_zip(cfg["seven_zip"])
        cfg["seven_zip"] = self.seven_zip
        if cfg["keep_archive"]:
            cfg["archive_dir"] = (cfg["archive_dir"]
                                  or os.path.join(cfg["watch_dir"] or str(APP_DIR), "_已处理"))
        self.cfg.update(cfg)
        save_config(self.cfg)

        self.cancel_flag.clear()
        self.progress.set(0, max(len(self.files), 1))
        for it in self.files:
            it["status"] = "待处理"
            it["detail"] = ""
        self._refresh_list()
        self.btn_run.set_enabled(False)
        self.btn_scan.set_enabled(False)
        self.btn_stop.set_enabled(True)

        self.log("─" * 40, "dim")
        if cfg["dry_run"]:
            self.log("试运行模式：不会移动任何文件。", "warn")

        self.worker = threading.Thread(target=self._run_job,
                                       args=(list(self.files), cfg), daemon=True)
        self.worker.start()

    def do_stop(self):
        self.cancel_flag.set()
        self.status_lbl.config(text="正在停止…（当前文件处理完后中断）")

    def _run_job(self, files, cfg):
        manifest = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "dry_run": cfg["dry_run"], "placed": [], "overwritten": [],
                    "archived": [], "errors": []}
        stats = {"zipmod": 0, "png": 0, "misc": 0, "skip": 0, "fail": 0, "archive": 0}
        dry = cfg["dry_run"]
        conflict = cfg["conflict"]
        seven = cfg["seven_zip"]
        planned = set()

        try:
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            TEMP_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            for idx, item in enumerate(files):
                if self.cancel_flag.is_set():
                    self.msg_q.put(("file", (idx, "已取消", "", "skip")))
                    continue
                arc: Path = item["path"]
                self.msg_q.put(("status",
                                f"[{idx + 1}/{len(files)}] 正在处理 {arc.name}"))
                self.log(f"[{idx + 1}/{len(files)}] {arc.name}")

                if not arc.exists():
                    self.msg_q.put(("file", (idx, "失败", "文件不存在", "fail")))
                    stats["fail"] += 1
                    manifest["errors"].append({"file": str(arc), "error": "文件不存在"})
                    self.log("  文件不存在", "err")
                    self.msg_q.put(("progress", (idx + 1, len(files))))
                    continue

                tmp_root = Path(tempfile.mkdtemp(prefix=f"{safe_name(arc.stem)}_",
                                                 dir=str(TEMP_DIR)))
                try:
                    ok, err = extract_archive(seven, arc, tmp_root)
                    if not ok:
                        self.msg_q.put(("file", (idx, "解压失败", err[:60], "fail")))
                        stats["fail"] += 1
                        manifest["errors"].append({"file": str(arc), "error": err})
                        self.log(f"  解压失败：{err}", "err")
                        continue

                    moved_z = moved_p = moved_o = skipped = 0
                    for f in iter_files(tmp_root):
                        ext = f.suffix.lower()
                        if ext == ".zipmod":
                            kind, target = "zipmod", cfg["zipmod_dir"]
                        elif ext == ".png":
                            kind, target = "png", cfg["png_dir"]
                        else:
                            if not cfg["copy_other"]:
                                continue
                            kind, target = "misc", cfg["misc_dir"]

                        res, detail = self._place(f, Path(target), kind, conflict,
                                                  dry, manifest, planned)
                        if res == "ok":
                            if kind == "zipmod":
                                moved_z += 1; stats["zipmod"] += 1
                            elif kind == "png":
                                moved_p += 1; stats["png"] += 1
                            else:
                                moved_o += 1; stats["misc"] += 1
                        elif res == "skip":
                            skipped += 1; stats["skip"] += 1
                        else:
                            stats["fail"] += 1
                            manifest["errors"].append({"file": str(f), "error": detail})

                    parts = []
                    if moved_z: parts.append(f"zipmod ×{moved_z}")
                    if moved_p: parts.append(f"png ×{moved_p}")
                    if moved_o: parts.append(f"其它 ×{moved_o}")
                    if skipped: parts.append(f"已存在 ×{skipped}")
                    summary = "，".join(parts) if parts else "无可分类文件"
                    self.msg_q.put(("file", (idx, "试运行" if dry else "已完成",
                                             summary, "dry" if dry else "ok")))
                    self.log(f"  {summary}", "ok")

                    if cfg["keep_archive"] and not dry:
                        adir = Path(cfg["archive_dir"])
                        adir.mkdir(parents=True, exist_ok=True)
                        dst = unique_path(adir / arc.name)
                        shutil.move(str(arc), str(dst))
                        manifest["archived"].append({"src": str(arc), "dst": str(dst)})
                        stats["archive"] += 1
                        self.log(f"  原包已归档 → {dst.parent.name}/{dst.name}", "dim")

                except Exception as e:
                    stats["fail"] += 1
                    manifest["errors"].append({"file": str(arc), "error": str(e)})
                    self.msg_q.put(("file", (idx, "异常", str(e)[:60], "fail")))
                    self.log(f"  异常：{e}", "err")
                    self.log(traceback.format_exc().strip().splitlines()[-1], "dim")
                finally:
                    if cfg["clean_temp"] or dry:
                        shutil.rmtree(tmp_root, ignore_errors=True)
                    self.msg_q.put(("progress", (idx + 1, len(files))))

            if not dry:
                try:
                    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
                        json.dump(manifest, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            self.msg_q.put(("done", {"stats": stats, "dry": dry,
                                     "cancel": self.cancel_flag.is_set()}))
        except Exception as e:
            self.log(f"任务异常终止：{e}", "err")
            self.log(traceback.format_exc(), "dim")
            self.msg_q.put(("done", {"stats": stats, "dry": dry, "cancel": False}))

    def _place(self, src: Path, target: Path, kind: str, conflict: str,
               dry: bool, manifest: dict, planned: set = None):
        if planned is None:
            planned = set()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return "err", f"无法创建目录 {target}：{e}"

        dst = target / src.name
        if dst.exists():
            same = False
            try:
                same = dst.stat().st_size == src.stat().st_size
            except Exception:
                pass
            if same or conflict == "skip":
                return "skip", f"{dst.name} 已存在"
            if conflict == "rename":
                dst = unique_path(dst)
        if dry:
            key = str(dst).lower()
            if key in planned:
                return "skip", f"{dst.name} 本批次重复"
            planned.add(key)
            return "ok", ""

        if dst.exists() and conflict == "overwrite":
            bdir = BACKUP_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
            bdir.mkdir(parents=True, exist_ok=True)
            bdst = unique_path(bdir / dst.name)
            try:
                shutil.move(str(dst), str(bdst))
                manifest["overwritten"].append({"dst": str(dst), "backup": str(bdst)})
            except Exception as e:
                return "err", f"备份旧文件失败：{e}"

        try:
            shutil.move(str(src), str(dst))
        except Exception as e:
            return "err", f"移动失败：{e}"
        manifest["placed"].append({"kind": kind, "dst": str(dst), "name": src.name})
        return "ok", dst.name

    def _on_done(self, payload):
        s = payload["stats"]
        self.btn_run.set_enabled(True)
        self.btn_scan.set_enabled(True)
        self.btn_stop.set_enabled(False)
        head = "试运行结束" if payload["dry"] else "整理完成"
        if payload.get("cancel"):
            head = "已停止"
        self.log("─" * 40, "dim")
        self.log(f"{head}：zipmod {s['zipmod']} · png {s['png']} · 其它 {s['misc']} · "
                 f"跳过 {s['skip']} · 归档 {s['archive']} · 失败 {s['fail']}",
                 "err" if s["fail"] else "ok")
        self.status_lbl.config(text=head)
        self.summary_lbl.config(
            text=f"zipmod {s['zipmod']}　png {s['png']}　失败 {s['fail']}")
        if not payload["dry"] and not payload.get("cancel"):
            self.log("如需回退，点「撤销上次」。", "dim")

    # ---------------- 撤销 ----------------
    def do_undo(self):
        if self.worker and self.worker.is_alive():
            return
        if not MANIFEST_PATH.is_file():
            messagebox.showinfo("提示", "没有找到上次整理的记录。")
            return
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"清单读取失败：{e}")
            return
        if m.get("dry_run"):
            messagebox.showinfo("提示", "上次是试运行，没有产生实际改动。")
            return

        placed, archived, overwritten = (m.get("placed", []), m.get("archived", []),
                                         m.get("overwritten", []))
        n = len(placed) + len(archived) + len(overwritten)
        if n == 0:
            messagebox.showinfo("提示", "上次整理没有可撤销的改动。")
            return
        if not messagebox.askyesno(
                "确认撤销",
                f"将撤销 {m.get('time', '')} 的整理：\n\n"
                f"· 已分发文件 {len(placed)} 个 → 移入备份目录\n"
                f"· 归档压缩包 {len(archived)} 个 → 移回原位\n"
                f"· 被覆盖文件 {len(overwritten)} 个 → 还原旧版本\n\n"
                f"所有内容都会保留在 _work\\backup 中，不会删除任何文件。\n是否继续？"):
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        udir = BACKUP_DIR / f"undo_{stamp}"
        ok = err = 0

        for rec in reversed(placed):
            dst = Path(rec["dst"])
            if not dst.exists():
                continue
            tdir = udir / rec.get("kind", "misc")
            tdir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(dst), str(unique_path(tdir / dst.name)))
                ok += 1
            except Exception as e:
                err += 1
                self.log(f"移回失败 {dst.name}：{e}", "err")

        for rec in reversed(overwritten):
            dst, bak = Path(rec["dst"]), Path(rec["backup"])
            if not bak.exists():
                continue
            try:
                if dst.exists():
                    tdir = udir / "replaced"
                    tdir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(unique_path(tdir / dst.name)))
                shutil.move(str(bak), str(dst))
                ok += 1
            except Exception as e:
                err += 1
                self.log(f"还原失败 {dst.name}：{e}", "err")

        for rec in reversed(archived):
            src, dst = Path(rec["src"]), Path(rec["dst"])
            if not dst.exists():
                continue
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                ok += 1
            except Exception as e:
                err += 1
                self.log(f"压缩包移回失败 {dst.name}：{e}", "err")

        self.log(f"撤销完成：成功 {ok} 项，失败 {err} 项。", "warn" if err else "ok")
        self.log(f"撤回的文件保留在：{udir}", "dim")
        try:
            MANIFEST_PATH.unlink()
        except Exception:
            pass
        messagebox.showinfo("撤销完成",
                            f"成功 {ok} 项，失败 {err} 项。\n\n"
                            f"撤回的文件保留在：\n{udir}\n\n"
                            f"原压缩包已移回待处理目录。")

    # ---------------- 关闭 ----------------
    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("确认退出", "任务仍在运行，确定要退出吗？"):
                return
            self.cancel_flag.set()
        self._closing = True
        self.cfg.update(self._grab_ui())
        save_config(self.cfg)
        self.root.destroy()


def main():
    global SCALE
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    SCALE = max(1.0, min(detect_dpi() / 96.0, 2.0))

    root = tk.Tk()
    app = ModManagerApp(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"程序启动失败：\n\n{err}", "Mod 管理器", 0x10)
        except Exception:
            pass
        print(err)
