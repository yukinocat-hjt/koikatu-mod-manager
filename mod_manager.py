# -*- coding: utf-8 -*-
"""
恋爱活动（Koikatsu）Mod 管理器
------------------------------------------------
流程：扫描压缩包 -> 7-Zip 解压到临时目录 -> 按扩展名分类 -> 移动到指定目录

默认规则：
    *.zipmod  ->  Mods 目录
    *.png     ->  卡片/图片目录
    其它文件  ->  可选，输出到「其它」目录

作者：WorkBuddy
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --------------------------------------------------------------------------
# 常量 / 配色
# --------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
WORK_DIR = APP_DIR / "_work"          # 临时解压 + 备份 + 清单
TEMP_DIR = WORK_DIR / "temp"
BACKUP_DIR = WORK_DIR / "backup"
MANIFEST_PATH = WORK_DIR / "last_run.json"

ARCHIVE_EXTS = {".rar", ".zip", ".7z", ".001"}

BG = "#F5F6F8"
CARD = "#FFFFFF"
LINE = "#E4E6EA"
LINE_SOFT = "#EEF0F3"
TEXT = "#1F2329"
SUB = "#8A9099"
RED = "#E4453A"
RED_DARK = "#C6392F"
RED_SOFT = "#FDEDEB"
GREEN = "#2FA36B"
AMBER = "#D98A00"

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


# --------------------------------------------------------------------------
# 工具函数
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


def extract_archive(seven_zip: str, archive: Path, dest: Path) -> tuple:
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
        self.files = []          # [{"path": Path, "status": str, "detail": str}]
        self._closing = False
        self.seven_zip = find_seven_zip(self.cfg.get("seven_zip", ""))

        self._build_style()
        self._build_ui()
        self._load_into_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._pump)

        self.log("恋爱活动 Mod 管理器已就绪。")
        if self.seven_zip:
            self.log(f"解压引擎：{self.seven_zip}")
        else:
            self.log("⚠ 未检测到 7-Zip，RAR 将无法解压。请到 7-zip.org 安装。")
        self.log("请先设置「待处理目录」「zipmod 目标目录」「png 目标目录」，然后点『扫描压缩包』。")

    # ---------------- 样式 ----------------
    def _build_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass

        st.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("CardSub.TLabel", background=CARD, foreground=SUB, font=(FONT, 9))
        st.configure("Section.TLabel", background=CARD, foreground=TEXT, font=(FONT, 10, "bold"))

        st.configure("TCheckbutton", background=CARD, foreground=TEXT, focuscolor=CARD)
        st.map("TCheckbutton", background=[("active", CARD)])

        st.configure("TEntry", fieldbackground="#FFFFFF", foreground=TEXT,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                     insertcolor=TEXT, padding=5)
        st.map("TEntry", bordercolor=[("focus", RED)])

        st.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF",
                     bordercolor=LINE, arrowcolor=SUB, padding=4)
        st.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")],
               bordercolor=[("focus", RED)])

        st.configure("TButton", background="#FFFFFF", foreground=TEXT,
                     bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                     focuscolor=CARD, padding=(12, 6), relief="flat")
        st.map("TButton",
               background=[("active", "#F0F1F3"), ("pressed", LINE_SOFT)],
               bordercolor=[("active", "#D6D9DE")])

        st.configure("Accent.TButton", background=RED, foreground="#FFFFFF",
                     bordercolor=RED, lightcolor=RED, darkcolor=RED,
                     padding=(14, 7), font=(FONT, 10, "bold"))
        st.map("Accent.TButton",
               background=[("active", RED_DARK), ("pressed", RED_DARK)],
               bordercolor=[("active", RED_DARK)],
               foreground=[("disabled", "#F2C9C5")])

        st.configure("Ghost.TButton", background="#FFFFFF", foreground=SUB,
                     bordercolor=LINE, padding=(10, 4), font=(FONT, 9))
        st.map("Ghost.TButton", background=[("active", RED_SOFT)],
               foreground=[("active", RED)], bordercolor=[("active", "#F2C9C5")])

        st.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                     foreground=TEXT, bordercolor=LINE, borderwidth=0,
                     rowheight=26, font=(FONT, 9))
        st.configure("Treeview.Heading", background="#FAFAFB", foreground=SUB,
                     relief="flat", font=(FONT, 9), padding=(6, 6))
        st.map("Treeview.Heading", background=[("active", "#F0F1F3")])
        st.map("Treeview", background=[("selected", RED_SOFT)],
               foreground=[("selected", TEXT)])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        st.configure("Thin.Horizontal.TProgressbar", background=RED,
                     troughcolor=LINE_SOFT, bordercolor=LINE_SOFT,
                     lightcolor=RED, darkcolor=RED, thickness=4)

    # ---------------- 布局 ----------------
    def _build_ui(self):
        self.root.configure(bg=BG)
        self.root.title("恋爱活动 Mod 管理器")
        self.root.geometry("1140x760")
        self.root.minsize(1000, 680)

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        # ---- 页眉 ----
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        tk.Frame(header, bg=RED, width=3, height=34).pack(side="left", padx=(0, 10))
        t = tk.Frame(header, bg=BG)
        t.pack(side="left")
        tk.Label(t, text="恋爱活动 Mod 管理器", bg=BG, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(t, text="解压 → 分类 → 归位  ·  zipmod / png 自动分发",
                 bg=BG, fg=SUB, font=(FONT, 9)).pack(anchor="w", pady=(2, 0))
        self.engine_lbl = tk.Label(header, text="", bg=BG, fg=SUB, font=(FONT, 9))
        self.engine_lbl.pack(side="right", pady=(14, 0))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, pady=(14, 0))
        body.columnconfigure(0, minsize=452)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")

        self._build_left(left)
        self._build_right(right)
        self._build_statusbar(outer)

    # --- 左侧：配置 + 选项 + 操作 ---
    def _build_left(self, parent):
        # 目录配置
        card, body = self._card(parent, "目录配置")
        card.pack(fill="x")

        self.var_watch = tk.StringVar()
        self.var_zipmod = tk.StringVar()
        self.var_png = tk.StringVar()
        self.var_misc = tk.StringVar()
        self.var_archive = tk.StringVar()

        self._path_row(body, "待处理目录", self.var_watch,
                       "存放下载好的 rar / zip / 7z")
        self._path_row(body, "zipmod 目标", self.var_zipmod,
                       "一般是 Koikatu\\Mods")
        self._path_row(body, "png 目标", self.var_png,
                       "角色卡 / 场景卡目录")
        self._path_row(body, "其它文件", self.var_misc,
                       "可选，需勾选下方『保留』")
        self._path_row(body, "归档目录", self.var_archive,
                       "留空则为『待处理目录/_已处理』")

        ttk.Button(body, text="从游戏目录一键填充", style="Ghost.TButton",
                   command=self.fill_from_game_dir).grid(
            row=99, column=0, columnspan=3, sticky="w", pady=(8, 2))

        # 选项
        card2, body2 = self._card(parent, "处理选项")
        card2.pack(fill="x", pady=(12, 0))

        self.var_keep = tk.BooleanVar()
        self.var_other = tk.BooleanVar()
        self.var_clean = tk.BooleanVar()
        self.var_rec = tk.BooleanVar()
        self.var_dry = tk.BooleanVar()

        r = 0
        for var, txt in (
            (self.var_keep, "整理后把原压缩包移入归档目录（不删除，可重来）"),
            (self.var_other, "保留未分类文件，输出到「其它文件」目录"),
            (self.var_clean, "整理完成后清空临时解压目录"),
            (self.var_rec, "同时扫描子目录里的压缩包"),
            (self.var_dry, "试运行：只预览结果，不移动任何文件"),
        ):
            ttk.Checkbutton(body2, text=txt, variable=var,
                            style="TCheckbutton").grid(
                row=r, column=0, columnspan=3, sticky="w", pady=2)
            r += 1

        tk.Frame(body2, bg=LINE_SOFT, height=1).grid(
            row=r, column=0, columnspan=3, sticky="ew", pady=8)
        r += 1
        ttk.Label(body2, text="重名处理", style="Card.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 4))
        self.var_conflict = tk.StringVar()
        cb = ttk.Combobox(body2, textvariable=self.var_conflict, state="readonly",
                          width=30, values=[
                              "自动改名（xxx_1.zipmod）",
                              "跳过已存在的文件",
                              "直接覆盖（旧文件会备份）",
                          ])
        cb.grid(row=r, column=1, columnspan=2, sticky="ew", pady=(0, 4))
        r += 1

        # 操作
        card3, body3 = self._card(parent, "操作")
        card3.pack(fill="x", pady=(12, 0))
        body3.columnconfigure(0, weight=1)
        body3.columnconfigure(1, weight=1)

        self.btn_scan = ttk.Button(body3, text="扫描压缩包", command=self.do_scan)
        self.btn_scan.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=3)
        ttk.Button(body3, text="添加压缩包…", command=self.do_add_files).grid(
            row=0, column=1, sticky="ew", padx=(5, 0), pady=3)

        self.btn_run = ttk.Button(body3, text="开始整理", style="Accent.TButton",
                                  command=self.do_process)
        self.btn_run.grid(row=1, column=0, sticky="ew", padx=(0, 5), pady=3)
        self.btn_stop = ttk.Button(body3, text="停止", command=self.do_stop,
                                   state="disabled")
        self.btn_stop.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=3)

        ttk.Button(body3, text="撤销上次整理", command=self.do_undo).grid(
            row=2, column=0, sticky="ew", padx=(0, 5), pady=3)
        ttk.Button(body3, text="清空列表", command=self.clear_list).grid(
            row=2, column=1, sticky="ew", padx=(5, 0), pady=3)

    # --- 右侧：列表 + 日志 ---
    def _build_right(self, parent):
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=2)
        parent.columnconfigure(0, weight=1)

        card, body = self._card(parent, "压缩包列表")
        card.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        self.count_lbl = ttk.Label(body, text="共 0 个", style="CardSub.TLabel")
        self.count_lbl.grid(row=0, column=0, columnspan=3, sticky="e", pady=(0, 6))

        wrap = tk.Frame(body, bg=LINE, bd=0)
        wrap.grid(row=1, column=0, columnspan=3, sticky="nsew")
        cols = ("name", "size", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("name", text="压缩包", anchor="w")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.heading("status", text="状态", anchor="w")
        self.tree.column("name", width=330, anchor="w")
        self.tree.column("size", width=80, anchor="e", stretch=False)
        self.tree.column("status", width=200, anchor="w")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        vs.grid(row=0, column=1, sticky="ns", pady=1, padx=(0, 1))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree.tag_configure("ok", foreground=GREEN)
        self.tree.tag_configure("fail", foreground=RED)
        self.tree.tag_configure("skip", foreground=SUB)
        self.tree.tag_configure("dry", foreground=AMBER)
        self.tree.bind("<Double-1>", self._open_selected_folder)

        # 日志
        card2, body2 = self._card(parent, "运行日志")
        card2.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        body2.rowconfigure(1, weight=1)
        body2.columnconfigure(0, weight=1)

        ttk.Button(body2, text="清空日志", style="Ghost.TButton",
                   command=self.clear_log).grid(row=0, column=0, columnspan=3,
                                                sticky="e", pady=(0, 6))

        lw = tk.Frame(body2, bg=LINE)
        lw.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.log_text = tk.Text(lw, bg="#FFFFFF", fg="#333840", bd=0,
                                relief="flat", wrap="word", padx=10, pady=8,
                                font=(FONT, 9), insertbackground=TEXT,
                                selectbackground=RED_SOFT, selectforeground=TEXT)
        ls = ttk.Scrollbar(lw, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        ls.grid(row=0, column=1, sticky="ns", pady=1, padx=(0, 1))
        lw.rowconfigure(0, weight=1)
        lw.columnconfigure(0, weight=1)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("dim", foreground=SUB)

    def _build_statusbar(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(bar, style="Thin.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        self.status_lbl = tk.Label(bar, text="就绪", bg=BG, fg=SUB,
                                   font=(FONT, 9), anchor="w")
        self.status_lbl.pack(fill="x", pady=(4, 0))

    def _card(self, parent, title):
        """带标题的白色卡片，返回 (外框, 内容框)"""
        outer = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                         highlightthickness=1, bd=0)
        pad = tk.Frame(outer, bg=CARD)
        pad.pack(fill="both", expand=True, padx=14, pady=12)
        head = tk.Frame(pad, bg=CARD)
        head.pack(fill="x", pady=(0, 10))
        tk.Frame(head, bg=RED, width=3, height=13).pack(side="left", padx=(0, 7))
        ttk.Label(head, text=title, style="Section.TLabel").pack(side="left")
        body = tk.Frame(pad, bg=CARD)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        return outer, body

    def _path_row(self, body, label, var, hint):
        r = body.grid_size()[1]
        ttk.Label(body, text=label, style="Card.TLabel",
                  width=10).grid(row=r, column=0, sticky="w", pady=3)
        e = ttk.Entry(body, textvariable=var)
        e.grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(body, text="…", width=3, style="Ghost.TButton",
                   command=lambda v=var: self.pick_dir(v)).grid(
            row=r, column=2, sticky="e", padx=(5, 0), pady=3)
        body.columnconfigure(1, weight=1)
        return e

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
        self.var_conflict.set({
            "rename": "自动改名（xxx_1.zipmod）",
            "skip": "跳过已存在的文件",
            "overwrite": "直接覆盖（旧文件会备份）",
        }.get(c.get("conflict", "rename"), "自动改名（xxx_1.zipmod）"))
        if self.seven_zip:
            self.engine_lbl.config(text=f"解压引擎  7-Zip")
        else:
            self.engine_lbl.config(text="未检测到 7-Zip", fg=RED)

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
            "conflict": {"自动改名（xxx_1.zipmod）": "rename",
                         "跳过已存在的文件": "skip",
                         "直接覆盖（旧文件会备份）": "overwrite",
                         }.get(self.var_conflict.get(), "rename"),
        }

    # ---------------- 通用小工具 ----------------
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
        # Mods 目录
        for name in ("Mods", "mods"):
            if (g / name).is_dir():
                self.var_zipmod.set(str(g / name))
                hits.append("zipmod → Mods")
                break
        else:
            self.var_zipmod.set(str(g / "Mods"))
            hits.append("zipmod → Mods（将新建）")
        # 卡片目录
        for sub in ("UserData/chara/female", "UserData/chara", "UserData/coordinate"):
            if self.var_png.get():
                break
            if (g / sub.replace("/", os.sep)).is_dir():
                self.var_png.set(str(g / sub.replace("/", os.sep)))
                hits.append(f"png → {sub}")
                break
        self.log(("已根据游戏目录填充：" + "，".join(hits)) if hits
                 else "未在所选目录中找到 Mods / UserData 子目录，请手动指定。")
        if not self.var_archive.get() and self.var_watch.get():
            self.log("提示：归档目录留空时会自动使用『待处理目录/_已处理』。")

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
                    text, tag = payload
                    self._append_log(text, tag)
                elif kind == "progress":
                    val, mx = payload
                    self.progress.configure(maximum=max(mx, 1), value=val)
                elif kind == "status":
                    self.status_lbl.config(text=payload)
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
        self.log_text.insert("end", f"[{ts}] ", "dim")
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

    def _row_values(self, item):
        try:
            size = human_size(item["path"].stat().st_size)
        except Exception:
            size = "-"
        st = item["status"]
        if item.get("detail"):
            st = f"{st} · {item['detail']}"
        return (item["path"].name, size, st)

    def _refresh_row(self, idx, tag=None):
        iid = str(idx)
        if not self.tree.exists(iid):
            return
        item = self.files[idx]
        self.tree.item(iid, values=self._row_values(item))
        if tag:
            self.tree.item(iid, tags=(tag,))
        self.tree.see(iid)

    def clear_list(self):
        if self.worker and self.worker.is_alive():
            return
        self.files = []
        self._refresh_list()
        self.status_lbl.config(text="就绪")
        self.progress.configure(value=0)

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
        rec = self.var_rec.get()
        found = []
        root = Path(watch)
        it = root.rglob("*") if rec else root.glob("*")
        for p in it:
            if p.is_file() and p.suffix.lower() in ARCHIVE_EXTS:
                found.append(p)
        found = [p for p in found if not p.name.startswith("~$")]
        self.files = [{"path": p, "status": "待处理", "detail": ""} for p in found]
        self.files.sort(key=lambda x: x["path"].name.lower())
        self._refresh_list()
        self.progress.configure(maximum=max(len(found), 1), value=0)
        self.status_lbl.config(text=f"扫描完成：找到 {len(found)} 个压缩包")
        self.log(f"扫描 {watch} → 找到 {len(found)} 个压缩包"
                 + ("（含子目录）" if rec else ""))

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
            messagebox.showwarning("提示", "列表为空，请先『扫描压缩包』或『添加压缩包』。")
            return

        cfg = self._grab_ui()
        if not cfg["zipmod_dir"]:
            messagebox.showwarning("提示", "请设置「zipmod 目标」目录。")
            return
        if not cfg["png_dir"]:
            messagebox.showwarning("提示", "请设置「png 目标」目录。")
            return
        if cfg["copy_other"] and not cfg["misc_dir"]:
            messagebox.showwarning("提示", "勾选了「保留未分类文件」，请设置「其它文件」目录。")
            return

        self.seven_zip = find_seven_zip(cfg["seven_zip"])
        cfg["seven_zip"] = self.seven_zip

        # 归档目录
        if cfg["keep_archive"]:
            ad = cfg["archive_dir"] or os.path.join(cfg["watch_dir"] or str(APP_DIR), "_已处理")
            cfg["archive_dir"] = ad

        self.cfg.update(cfg)
        save_config(self.cfg)

        self.cancel_flag.clear()
        self.progress.configure(maximum=max(len(self.files), 1), value=0)
        for it in self.files:
            it["status"] = "待处理"
            it["detail"] = ""
        self._refresh_list()
        self.btn_run.config(state="disabled")
        self.btn_scan.config(state="disabled")
        self.btn_stop.config(state="normal")

        self.log("─" * 46)
        if cfg["dry_run"]:
            self.log("【试运行模式】不会移动任何文件。", "warn")
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
        planned = set()          # 试运行用：记录本批次已预订的目标路径

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
                    self.log(f"  ✗ 文件不存在", "err")
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
                        self.log(f"  ✗ 解压失败：{err}", "err")
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
                    if dry:
                        self.msg_q.put(("file", (idx, "试运行", summary, "dry")))
                    else:
                        self.msg_q.put(("file", (idx, "已完成", summary, "ok")))
                    self.log(f"  ✓ {summary}", "ok")

                    # 归档原压缩包
                    if cfg["keep_archive"] and not dry:
                        adir = Path(cfg["archive_dir"])
                        adir.mkdir(parents=True, exist_ok=True)
                        dst = unique_path(adir / arc.name)
                        shutil.move(str(arc), str(dst))
                        manifest["archived"].append({"src": str(arc), "dst": str(dst)})
                        stats["archive"] += 1
                        self.log(f"  ↳ 原包已归档 → {dst.parent.name}/{dst.name}", "dim")

                except Exception as e:
                    stats["fail"] += 1
                    manifest["errors"].append({"file": str(arc), "error": str(e)})
                    self.msg_q.put(("file", (idx, "异常", str(e)[:60], "fail")))
                    self.log(f"  ✗ 异常：{e}", "err")
                    self.log(traceback.format_exc().strip().splitlines()[-1], "dim")
                finally:
                    if cfg["clean_temp"] or dry:
                        shutil.rmtree(tmp_root, ignore_errors=True)
                    self.msg_q.put(("progress", (idx + 1, len(files))))

            # 保存清单
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
        """把文件放入目标目录。返回 (结果, 说明)"""
        if planned is None:
            planned = set()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return "err", f"无法创建目录 {target}：{e}"

        dst = target / src.name
        existed_same = False
        if dst.exists():
            try:
                if dst.stat().st_size == src.stat().st_size:
                    existed_same = True
            except Exception:
                pass
            if existed_same or conflict == "skip":
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
        self.btn_run.config(state="normal")
        self.btn_scan.config(state="normal")
        self.btn_stop.config(state="disabled")
        head = "【试运行结束】" if payload["dry"] else "整理完成"
        if payload.get("cancel"):
            head = "已停止"
        self.log("─" * 46)
        self.log(f"{head}：zipmod {s['zipmod']} · png {s['png']} · "
                 f"其它 {s['misc']} · 跳过 {s['skip']} · 归档 {s['archive']} · "
                 f"失败 {s['fail']}", "err" if s["fail"] else "ok")
        self.status_lbl.config(text=f"{head}　zipmod {s['zipmod']} / png {s['png']}"
                                    f" / 失败 {s['fail']}")
        if not payload["dry"] and not payload.get("cancel"):
            self.log("如需回退，可点『撤销上次整理』。", "dim")

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

        placed = m.get("placed", [])
        archived = m.get("archived", [])
        overwritten = m.get("overwritten", [])
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
            tgt_dir = udir / rec.get("kind", "misc")
            tgt_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(dst), str(unique_path(tgt_dir / dst.name)))
                ok += 1
            except Exception as e:
                err += 1
                self.log(f"  ✗ 移回失败 {dst.name}：{e}", "err")

        for rec in reversed(overwritten):
            dst, bak = Path(rec["dst"]), Path(rec["backup"])
            if not bak.exists():
                continue
            try:
                if dst.exists():
                    tgt_dir = udir / "replaced"
                    tgt_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(unique_path(tgt_dir / dst.name)))
                shutil.move(str(bak), str(dst))
                ok += 1
            except Exception as e:
                err += 1
                self.log(f"  ✗ 还原失败 {dst.name}：{e}", "err")

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
                self.log(f"  ✗ 压缩包移回失败 {dst.name}：{e}", "err")

        self.log(f"撤销完成：成功 {ok} 项，失败 {err} 项。", "warn" if err else "ok")
        self.log(f"被撤回的文件保留在：{udir}", "dim")
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
    # 高 DPI
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        root = tk.Tk()
    except Exception as e:
        print(f"无法创建窗口：{e}")
        raise
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
                None, f"程序启动失败：\n\n{err}", "恋爱活动 Mod 管理器", 0x10)
        except Exception:
            pass
        print(err)
