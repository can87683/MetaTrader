#!/usr/bin/env python3
# mt5_fetch_mcp.py
# Copyright Su Nie | BSD-3C License | https://github.com/can87683


"""customtkinter==5.2.2 requests==2.32.3"""

import base64
import configparser
import csv
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
import tkinter as tk


TOOL_CANDIDATES = {
    "account":           ["get_trading_account_info", "get_account_info"],
    "add_symbol":        ["add_marketwatch_symbol"],
    "charts":            ["list_open_charts"],
    "close_position":    ["trade_close_single_position", "trade_close_by_position"],
    "delete_order":      ["trade_delete_order"],
    "history_orders":    ["get_trading_history_orders"],
    "history_positions": ["get_trading_history_positions"],
    "modify_sl_tp":      ["trade_modify_sl_tp"],
    "order_market":      ["trade_send_market_order"],
    "order_pending":     ["trade_send_pending_order"],
    "positions":         ["get_trading_open_positions", "get_open_positions"],
    "rates":             ["get_chart_history"],
    "remove_symbol":     ["remove_marketwatch_symbol"],
    "symbols":           ["get_marketwatch_symbols"],
    "ticks":             ["get_chart_ticks_history"],
    "time":              ["get_time_information"],
    "workspace":         ["get_workspace_info"],
}

TOOLS_FILE = Path(sys.argv[0]).resolve().parent / "mt5_mcp_tools.json"


class Config:
    """Static configuration and INI merge helper."""

    TF_MAX_BARS = {
        "M5": 200_000,
        "M15": 100_000,
        "H1": 50_000,
        "H4": 30_000,
        "D1": 10_000,
        "W1": 2_000,
    }

    WINDOW_WIDTH = 640
    WINDOW_HEIGHT = 800
    INI_FILE = str(Path(sys.argv[0]).resolve().with_suffix(".ini"))
    LOG_MAX_LINES = 1000
    LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
    DISCONNECT_GRACE = 2

    INI_SECTION_ORDER = ("Window", "Mcp", "Fetch")
    INI_SECTION_WINDOW = "Window"

    TIMEFRAME_ORDER = ["M5", "M15", "H1", "H4", "D1", "W1"]
    TF_MINUTES = {
        "M5": 5, "M15": 15, "H1": 60, "H4": 240,
        "D1": 1440, "W1": 10080,
    }
    DEFAULT_MAX_BARS = 10000
    DATA_DIR = os.path.join(str(Path.home()), "mt5_data")

    WINDOW_X = None
    WINDOW_Y = None

    COLOR_BORDER = "white"
    BORDER_WIDTH = 2

    @staticmethod
    def atomic_merge_ini(path, section, updates):
        disk = configparser.ConfigParser(interpolation=None)
        if os.path.exists(path):
            disk.read(path, encoding="utf-8")
        if not disk.has_section(section):
            disk.add_section(section)
        for key, value in updates.items():
            disk.set(section, key, str(value))

        preferred = [s.lower() for s in Config.INI_SECTION_ORDER]
        ordered = configparser.ConfigParser(interpolation=None)
        ordered.optionxform = str
        for sect in preferred:
            if disk.has_section(sect):
                ordered.add_section(sect)
                for key, value in disk.items(sect):
                    ordered.set(sect, key, value)
        for sect in disk.sections():
            if sect in preferred:
                continue
            ordered.add_section(sect)
            for key, value in disk.items(sect):
                ordered.set(sect, key, value)

        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(
            prefix=".mcp_", suffix=".ini", dir=directory,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            ordered.write(handle)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    @staticmethod
    def load_window_geometry(path):
        parser = configparser.ConfigParser(interpolation=None)
        if os.path.exists(path):
            parser.read(path, encoding="utf-8")
        if not parser.has_section(Config.INI_SECTION_WINDOW):
            return (None, None)

        geometry = parser.get(
            Config.INI_SECTION_WINDOW, "geometry", fallback="",
        )
        if geometry:
            _size, rest = geometry.split("+", 1)
            x_str, y_str = rest.split("+", 1)
            return (int(x_str), int(y_str))

        x = parser.getint(Config.INI_SECTION_WINDOW, "x", fallback=None)
        y = parser.getint(Config.INI_SECTION_WINDOW, "y", fallback=None)
        return (x, y)

    @staticmethod
    def save_window_geometry(path, geometry):
        parser = configparser.ConfigParser(interpolation=None)
        if os.path.exists(path):
            parser.read(path, encoding="utf-8")
        if parser.has_section(Config.INI_SECTION_WINDOW):
            for key in ("x", "y"):
                if parser.has_option(Config.INI_SECTION_WINDOW, key):
                    parser.remove_option(Config.INI_SECTION_WINDOW, key)

        directory = os.path.dirname(os.path.abspath(path)) or "."
        if os.path.exists(path):
            fd, tmp = tempfile.mkstemp(
                prefix=".mcp_", suffix=".ini", dir=directory,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                parser.write(handle)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)

        Config.atomic_merge_ini(
            path, Config.INI_SECTION_WINDOW, {"geometry": geometry},
        )


class LogFrame(ctk.CTkFrame):
    """Shared bottom log; also acts as the log_callback target."""

    TITLE_FONT = ("Arial", 14, "bold")
    TITLE_COLOR = "yellow"
    MONO_FONT = ("Consolas", 12)
    COLOR_BORDER = Config.COLOR_BORDER
    BORDER_WIDTH = Config.BORDER_WIDTH
    LOG_BG_COLOR = "black"
    CLEAR_SENTINEL = "__clear__"

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            border_width=self.BORDER_WIDTH,
            border_color=self.COLOR_BORDER,
            **kwargs,
        )

        ctk.CTkLabel(
            self, text="Log",
            font=self.TITLE_FONT, text_color=self.TITLE_COLOR, anchor="w",
        ).pack(fill="x", padx=6, pady=(6, 2))

        self.log_text = ctk.CTkTextbox(
            self, font=self.MONO_FONT, wrap="word", border_width=0,
            fg_color=self.LOG_BG_COLOR,
        )
        self.log_text.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.log_text.configure(state="disabled")

        menu = tk.Menu(self.log_text, tearoff=0)
        menu.add_command(label="Copy", command=self.copy)
        menu.add_command(label="Clear", command=self.clear)
        self.log_text.bind(
            "<Button-3>",
            lambda event: menu.tk_popup(event.x_root, event.y_root),
        )

    def append(self, message):
        if message == self.CLEAR_SENTINEL:
            self.clear()
            return
        box = self.log_text
        box.configure(state="normal")

        at_bottom = box.yview()[1] >= 0.999
        box.insert("end", f"{message}\n")
        lines = int(box.index("end-1c").split(".")[0])
        if lines > Config.LOG_MAX_LINES:
            box.delete("1.0", f"{lines - Config.LOG_MAX_LINES}.0")
        if at_bottom:
            box.see("end")
        box.configure(state="disabled")

    def copy(self):
        text = (
            self.log_text.selection_get()
            if self.log_text.tag_ranges("sel")
            else self.log_text.get("1.0", "end-1c")
        )
        self.clipboard_clear()
        self.clipboard_append(text)

    def clear(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")


class Mt5McpClient(ctk.CTkFrame):
    """MCP connection frame with token obfuscation and tool discovery."""

    # --- editable constants ---
    DEFAULT_SERVER_IP = "127.0.0.1"
    DEFAULT_PORT = "22346"
    DEFAULT_ENDPOINT = "/mcp"
    DEFAULT_TOKEN = ""

    _TOKEN_SALT = b"mcp_client_v1_token_obfuscation"

    SCHEME = "http"
    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_NAME = "mcp_tab"
    CLIENT_VERSION = "1.0.0"
    HTTP_TIMEOUT = 30.0

    AUTO_CONNECT_DEFAULT = True
    AUTO_CONNECT_INTERVAL_MS = 10000
    KEEP_ALIVE_INTERVAL_MS = 15000
    RPC_LOG_LIMIT = 20

    DEFAULT_BLACKLIST = ""

    TITLE_FONT = ("Arial", 14, "bold")
    TITLE_COLOR = "yellow"
    UI_FONT = ("Arial", 12)
    MONO_FONT = ("Consolas", 12)
    BUTTON_FONT = ("Arial", 12, "bold")

    COLOR_CONNECT = "#2196F3"
    COLOR_DISCONNECT = "#F44336"
    COLOR_CAPABILITIES = "#9C27B0"
    COLOR_SYMBOLS = "#FF9800"
    COLOR_SCHEMA = "#00897B"
    COLOR_TOKEN_CLEAN = "#455A64"
    COLOR_TOKEN_SAVE = "#2E7D32"
    COLOR_TOKEN_CLEAR = "#B71C1C"
    COLOR_CLEAR_LOG = "#546E7A"
    COLOR_STATUS_OK = "#4CAF50"
    COLOR_STATUS_BAD = "#F44336"

    COLOR_BORDER = Config.COLOR_BORDER
    BORDER_WIDTH = Config.BORDER_WIDTH

    LOG_FRAME_HEIGHT = 0
    LOG_AUTOSCROLL = True

    FRAME_W = 640
    FRAME_H = 160

    DEFAULT_CONFIG_PATH = "mt5_dual_client.ini"

    def __init__(self, master, config_path=None, log_callback=None, **kwargs):
        super().__init__(
            master,
            border_width=self.BORDER_WIDTH,
            border_color=self.COLOR_BORDER,
            **kwargs,
        )
        if config_path is None:
            config_path = self.DEFAULT_CONFIG_PATH
        self.ini_path = config_path
        self.log_callback = log_callback

        self.scheme = self.SCHEME
        self.server_ip = self.DEFAULT_SERVER_IP
        self.port = self.DEFAULT_PORT
        self.endpoint = self.DEFAULT_ENDPOINT
        self.token = self.DEFAULT_TOKEN
        self.connected = False
        self.protocol_version = None
        self.server_info = None
        self.session_id = None
        self.on_connect_success = None

        if config_path is None:
            script_name = os.path.splitext(os.path.basename(__file__))[0]
            config_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"{script_name}.ini",
            )
        self.ini_path = config_path
        self.ini = configparser.ConfigParser()
        if os.path.exists(self.ini_path):
            self.ini.read(self.ini_path, encoding="utf-8")

        self._auto_connect_job = None
        self._keep_alive_job = None
        self._closing = False
        self._connecting = False
        self._rpc_inflight = 0
        self._tool_map = {}
        self._tool_registry = {}

        self._load_state()
        self._build_ui()

        self.configure(width=self.FRAME_W, height=self.FRAME_H)
        self.pack_propagate(False)
        self.grid_propagate(False)

        if self.auto_connect_var.get():
            self._auto_connect_job = self.after(
                self.AUTO_CONNECT_INTERVAL_MS, self._auto_connect_check,
            )

        self._keep_alive_job = self.after(
            self.KEEP_ALIVE_INTERVAL_MS, self._keep_alive_check,
        )

    @classmethod
    def _derive_key(cls) -> bytes:
        node = os.uname().nodename if hasattr(os, "uname") else socket.gethostname()
        material = cls._TOKEN_SALT + node.encode("utf-8", "ignore")
        return hashlib.sha256(material).digest()

    @classmethod
    def encrypt_token(cls, plaintext: str) -> str:
        if not plaintext:
            return ""
        key = cls._derive_key()
        data = plaintext.encode("utf-8")
        xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return "enc:" + base64.urlsafe_b64encode(xored).decode("ascii")

    @classmethod
    def decrypt_token(cls, stored: str) -> str:
        if not stored:
            return ""
        if not stored.startswith("enc:"):
            return stored
        raw = base64.urlsafe_b64decode(stored[4:].encode("ascii"))
        key = cls._derive_key()
        data = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        return data.decode("utf-8")

    def _load_state(self):
        if not self.ini.has_section("mcp"):
            self.ini.add_section("mcp")

        self.server_ip_var = ctk.StringVar(
            value=self.ini.get("mcp", "server_ip", fallback=self.DEFAULT_SERVER_IP),
        )
        self.port_var = ctk.StringVar(
            value=self.ini.get("mcp", "port", fallback=self.DEFAULT_PORT),
        )
        self.endpoint_var = ctk.StringVar(
            value=self.ini.get("mcp", "endpoint", fallback=self.DEFAULT_ENDPOINT),
        )
        self.token_var = ctk.StringVar(
            value=self.decrypt_token(
                self.ini.get("mcp", "token", fallback=self.DEFAULT_TOKEN),
            ),
        )
        self.auto_connect_var = ctk.BooleanVar(
            value=self.ini.getboolean(
                "mcp", "auto_connect", fallback=self.AUTO_CONNECT_DEFAULT,
            ),
        )
        self.market_watch_only_var = ctk.BooleanVar(
            value=self.ini.getboolean("mcp", "market_watch_only", fallback=True),
        )
        self.blacklist_enabled_var = ctk.BooleanVar(
            value=self.ini.getboolean("mcp", "blacklist_enabled", fallback=True),
        )
        self.blacklist_var = ctk.StringVar(
            value=self.ini.get("mcp", "blacklist", fallback=self.DEFAULT_BLACKLIST),
        )

    def _atomic_write_ini(self):
        disk = configparser.ConfigParser()
        if os.path.exists(self.ini_path):
            disk.read(self.ini_path, encoding="utf-8")

        if not disk.has_section("mcp"):
            disk.add_section("mcp")
        if self.ini.has_section("mcp"):
            for key, value in self.ini.items("mcp"):
                disk.set("mcp", key, value)

        preferred = ["mcp", "Log", "Window"]
        ordered = configparser.ConfigParser()
        ordered.optionxform = str

        for sect in preferred:
            if disk.has_section(sect):
                ordered.add_section(sect)
                for key, value in disk.items(sect):
                    ordered.set(sect, key, value)

        for sect in disk.sections():
            if sect in preferred:
                continue
            ordered.add_section(sect)
            for key, value in disk.items(sect):
                ordered.set(sect, key, value)

        directory = os.path.dirname(os.path.abspath(self.ini_path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".mcp_", suffix=".ini", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            ordered.write(handle)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.ini_path)

    def save_state(self):
        allowed = {
            "server_ip", "port", "endpoint", "token",
            "auto_connect", "market_watch_only",
            "blacklist", "blacklist_enabled",
        }
        for key in list(self.ini["mcp"].keys()):
            if key not in allowed:
                self.ini.remove_option("mcp", key)

        self.ini.set("mcp", "server_ip", self.server_ip_var.get())
        self.ini.set("mcp", "port", self.port_var.get())
        self.ini.set("mcp", "endpoint", self.endpoint_var.get())
        self.ini.set("mcp", "token", self.encrypt_token(self.token_var.get()))
        self.ini.set("mcp", "auto_connect", str(self.auto_connect_var.get()))
        self.ini.set(
            "mcp", "market_watch_only", str(self.market_watch_only_var.get()),
        )
        self.ini.set(
            "mcp", "blacklist_enabled", str(self.blacklist_enabled_var.get()),
        )
        self.ini.set("mcp", "blacklist", self.blacklist_var.get())
        self._atomic_write_ini()

    def _build_ui(self):
        self._build_connection_section()
        self._build_log_section()

    def _build_connection_section(self):
        conn_frame = ctk.CTkFrame(self)
        conn_frame.pack(fill="x", padx=2, pady=2)

        ctk.CTkLabel(
            conn_frame, text="MT5 Fetch MCP ",
            font=self.TITLE_FONT, text_color=self.TITLE_COLOR, anchor="w",
        ).pack(fill="x", padx=2, pady=2)

        self._build_server_row(conn_frame)
        self._build_token_row(conn_frame)
        self._build_options_row(conn_frame)
        self._build_buttons_row(conn_frame)

    def _build_server_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=2, pady=2)

        ctk.CTkLabel(row, text="Server IP:", font=self.UI_FONT).pack(side="left", padx=2)
        ctk.CTkEntry(
            row, textvariable=self.server_ip_var,
            width=100, font=self.UI_FONT, border_width=2,
        ).pack(side="left", padx=2)

        ctk.CTkLabel(row, text="Port:", font=self.UI_FONT).pack(side="left", padx=2)
        ctk.CTkEntry(
            row, textvariable=self.port_var,
            width=80, font=self.UI_FONT, border_width=2,
        ).pack(side="left", padx=2)

        ctk.CTkLabel(row, text="Endpoint:", font=self.UI_FONT).pack(side="left", padx=2)
        ctk.CTkEntry(
            row, textvariable=self.endpoint_var,
            width=80, font=self.UI_FONT, border_width=2,
        ).pack(side="left", padx=2)

    def _build_token_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=2, pady=2)

        ctk.CTkLabel(row, text="Token:", font=self.UI_FONT).pack(side="left", padx=2)

        self.token_entry = ctk.CTkEntry(
            row, textvariable=self.token_var,
            show="*", font=self.UI_FONT, border_width=2,
        )
        self.token_entry.pack(side="left", fill="x", expand=True, padx=2)

        self.token_visible = False
        self.token_toggle = ctk.CTkButton(
            row, text="👁", width=30, font=self.UI_FONT, border_width=2,
            command=self.toggle_token,
        )
        self.token_toggle.pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="🧹 Clean", width=70, font=self.UI_FONT, border_width=2,
            fg_color=self.COLOR_TOKEN_CLEAN, text_color="white",
            command=self.clean_token_entry,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="💾 Save", width=70, font=self.UI_FONT, border_width=2,
            fg_color=self.COLOR_TOKEN_SAVE, text_color="white",
            command=self.save_token,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="🗑", width=30, font=self.UI_FONT, border_width=2,
            fg_color=self.COLOR_TOKEN_CLEAR, text_color="white",
            command=self.clear_saved_token,
        ).pack(side="left", padx=2)

    def _build_options_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=2, pady=2)

        ctk.CTkCheckBox(
            row, text="Market Watch Only",
            variable=self.market_watch_only_var,
            font=self.UI_FONT,
            command=self._on_options_change,
        ).pack(side="left", padx=(2, 12))

        ctk.CTkCheckBox(
            row, text="Blacklist:",
            variable=self.blacklist_enabled_var,
            font=self.UI_FONT,
            command=self._on_options_change,
        ).pack(side="left", padx=2)

        self.blacklist_entry = ctk.CTkEntry(
            row, textvariable=self.blacklist_var,
            font=self.UI_FONT, border_width=2,
            placeholder_text="SYMBOL1,SYMBOL2,SYMBOL3",
        )
        self.blacklist_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.blacklist_entry.bind("<Return>", self._on_blacklist_edit)
        self.blacklist_entry.bind("<FocusOut>", self._on_blacklist_edit)

    def _build_buttons_row(self, parent):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=2, pady=2)

        self.auto_connect_cb = ctk.CTkCheckBox(
            row, text="Auto Connect",
            variable=self.auto_connect_var, font=self.UI_FONT,
            command=self._on_auto_connect_change,
        )
        self.auto_connect_cb.pack(side="left", padx=2)

        self.connect_btn = ctk.CTkButton(
            row, text="Connect", command=self._manual_connect,
            width=100, fg_color=self.COLOR_CONNECT, text_color="white",
            font=self.BUTTON_FONT, border_width=2,
        )
        self.connect_btn.pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="List Capabilities",
            command=self._list_mcp_tools,
            width=130, fg_color=self.COLOR_CAPABILITIES, text_color="white",
            font=self.BUTTON_FONT, border_width=2,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="Symbols",
            command=self._show_marketwatch_symbols,
            width=80, fg_color=self.COLOR_SYMBOLS, text_color="white",
            font=self.BUTTON_FONT, border_width=2,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="Schema",
            command=self._show_tool_schemas,
            width=80, fg_color=self.COLOR_SCHEMA, text_color="white",
            font=self.BUTTON_FONT, border_width=2,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            row, text="Clear Log", command=self._clear_log,
            width=80, fg_color=self.COLOR_CLEAR_LOG, text_color="white",
            font=self.BUTTON_FONT, border_width=2,
        ).pack(side="left", padx=2)

        self.conn_status_dot = ctk.CTkLabel(
            row, text="●", text_color=self.COLOR_STATUS_BAD,
            font=self.UI_FONT,
        )
        self.conn_status_dot.pack(side="left", padx=6)

    def _build_log_section(self):
        self.log_frame = ctk.CTkFrame(
            self, height=self.LOG_FRAME_HEIGHT,
        )
        self.log_frame.pack(fill="x", expand=False, padx=2, pady=2)
        self.log_frame.pack_propagate(False)

        self.conn_info = ctk.CTkTextbox(
            self.log_frame, font=self.MONO_FONT, wrap="word", border_width=2,
        )
        self.conn_info.pack(fill="both", expand=True, padx=2, pady=2)
        self.conn_info.insert("1.0", "MCP Connection Log\n" + "=" * 50 + "\n")
        self.conn_info.configure(state="disabled")

        menu = tk.Menu(self.conn_info, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_log)
        menu.add_command(label="Clear", command=self._clear_log)
        self.conn_info.bind(
            "<Button-3>",
            lambda event: menu.tk_popup(event.x_root, event.y_root),
        )

    def _render_conn_info(self, text):
        self.conn_info.configure(state="normal")
        self.conn_info.insert("end", text)
        self.conn_info.see("end")
        self.conn_info.configure(state="disabled")

    def toggle_token(self):
        self.token_visible = not self.token_visible
        if self.token_visible:
            self.token_entry.configure(show="")
            self.token_toggle.configure(text="🙈")
        else:
            self.token_entry.configure(show="*")
            self.token_toggle.configure(text="👁")

    def clean_token_entry(self):
        self.token_var.set("")
        self.token_entry.focus_set()
        self.log("🧹 Token entry cleared (saved token untouched)")

    def save_token(self):
        self.ini.set(
            "mcp", "token",
            self.encrypt_token(self.token_var.get().strip()),
        )
        self._atomic_write_ini()
        self.log("🔑 Token saved (obfuscated)")

    def clear_saved_token(self):
        self.token_var.set("")
        self.ini.set("mcp", "token", "")
        self._atomic_write_ini()
        self.log("🗑️ Saved token cleared")

    def _copy_log(self):
        text = (
            self.conn_info.selection_get()
            if self.conn_info.tag_ranges("sel")
            else self.conn_info.get("1.0", "end-1c")
        )
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log("✅ Log copied")

    def _clear_log(self):
        self.conn_info.configure(state="normal")
        self.conn_info.delete("1.0", "end")
        self.conn_info.insert("1.0", "MCP Connection Log\n" + "=" * 50 + "\n")
        self.conn_info.configure(state="disabled")
        if self.log_callback:
            self.log_callback(LogFrame.CLEAR_SENTINEL)

    def _on_auto_connect_change(self):
        state = self.auto_connect_var.get()
        if state:
            self.log("Auto Connect enabled")
            if self._auto_connect_job:
                self.after_cancel(self._auto_connect_job)
                self._auto_connect_job = None
            self._auto_connect_job = self.after(
                self.AUTO_CONNECT_INTERVAL_MS, self._auto_connect_check,
            )
        else:
            self.log("Auto Connect disabled")
            if self._auto_connect_job:
                self.after_cancel(self._auto_connect_job)
                self._auto_connect_job = None
        self.save_state()

    def _on_options_change(self):
        mw = "ON" if self.market_watch_only_var.get() else "OFF"
        bl = "ON" if self.blacklist_enabled_var.get() else "OFF"
        self.log(f"Market Watch Only: {mw}  |  Blacklist: {bl}")
        self.save_state()

    def _on_blacklist_edit(self, event=None):
        self.save_state()

    def _parse_blacklist(self):
        if not self.blacklist_enabled_var.get():
            return set()
        raw = self.blacklist_var.get() or ""
        return {s.strip() for s in raw.split(",") if s.strip()}

    def log(self, message):
        if self._closing:
            return

        self._render_conn_info(
            f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n",
        )

        if self.log_callback:
            self.log_callback(message)

    def _manual_connect(self):
        if self._connecting:
            return
        self._connecting = True
        try:
            self.mcp_configure(
                self.server_ip_var.get().strip(),
                self.port_var.get().strip(),
                self.endpoint_var.get().strip(),
                self.token_var.get().strip(),
            )
            success = self.mcp_connect()
            self._update_connection_status(success)
        finally:
            self._connecting = False

    def _auto_connect_check(self):
        if self._closing:
            return
        if self.auto_connect_var.get() and not self.connected and not self._connecting:
            try:
                self._manual_connect()
            except Exception as exc:
                self.log(f"❌ auto-connect failed: {exc}")
        self._auto_connect_job = self.after(
            self.AUTO_CONNECT_INTERVAL_MS, self._auto_connect_check,
        )

    def _keep_alive_check(self):
        if self._closing:
            return
        if self.connected and self._rpc_inflight == 0:
            try:
                self.mcp_rpc("ping", {})
            except Exception as exc:
                self.log(f"❌ keep-alive: {exc}")
                self.connected = False
                self.session_id = None
                self._update_connection_status(False)
        self._keep_alive_job = self.after(
            self.KEEP_ALIVE_INTERVAL_MS, self._keep_alive_check,
        )

    def _update_connection_status(self, connected: bool):
        if connected:
            self.conn_status_dot.configure(text_color=self.COLOR_STATUS_OK)
            self.connect_btn.configure(
                text="Disconnect",
                fg_color=self.COLOR_DISCONNECT,
                command=self._disconnect,
            )
        else:
            self.conn_status_dot.configure(text_color=self.COLOR_STATUS_BAD)
            self.connect_btn.configure(
                text="Connect",
                fg_color=self.COLOR_CONNECT,
                command=self._manual_connect,
            )

    def _disconnect(self):
        self.mcp_disconnect()
        self._update_connection_status(False)

    def _list_mcp_tools(self):
        if not self.connected:
            raise RuntimeError("List Capabilities: not connected")

        self.log("Fetching server capabilities...")
        tools = self.mcp_list_tools()
        resources = self.mcp_list_resources()
        prompts = self.mcp_list_prompts()

        lines = ["\n--- Server Capabilities ---"]
        lines.append(f"\nTools ({len(tools)}):")
        for tool in tools:
            lines.append(
                f"  • {tool.get('name', 'unknown')}: "
                f"{tool.get('description', '')}"
            )
        lines.append(f"\nResources ({len(resources)}):")
        for resource in resources:
            lines.append(
                f"  • {resource.get('name', resource.get('uri', 'unknown'))} "
                f"({resource.get('uri', '')})"
            )
        lines.append(f"\nPrompts ({len(prompts)}):")
        for prompt in prompts:
            lines.append(
                f"  • {prompt.get('name', 'unknown')}: "
                f"{prompt.get('description', '')}"
            )
        lines.append("=" * 50)

        self._render_conn_info("\n".join(lines) + "\n")
        for line in lines:
            self.log(line)

    def _show_tool_schemas(self):
        if not self.connected:
            raise RuntimeError("Schema: not connected")

        tools = self.mcp_list_tools()
        if not tools:
            raise RuntimeError("Schema: server returned no tools")

        self.log(f"Fetching schemas for {len(tools)} tools...")

        lines = ["\n--- Tool Schemas ---", "=" * 50]
        for tool in tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "")
            schema = tool.get("inputSchema", {}) or {}

            lines.append(f"\n● {name}")
            if desc:
                lines.append(f"  {desc}")
            pretty = json.dumps(schema, indent=2, ensure_ascii=False)
            for line in pretty.splitlines():
                lines.append(f"    {line}")
        lines.append("=" * 50)

        self._render_conn_info("\n".join(lines) + "\n")
        for line in lines:
            self.log(line)

    def _unwrap_tool_text(self, envelope, label):
        if envelope is None:
            raise RuntimeError(f"{label}: empty response")
        if envelope.get("isError"):
            content = envelope.get("content") or []
            text = ""
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "")
            raise RuntimeError(f"{label}: MCP tool error: {text}")
        content = envelope.get("content") or []
        if not content or not isinstance(content[0], dict):
            raise RuntimeError(f"{label}: unexpected MCP envelope")
        text = content[0].get("text", "")
        if not text:
            raise RuntimeError(f"{label}: empty tool payload")
        return json.loads(text)

    def fetch_symbols(self):
        mw_only = self.market_watch_only_var.get()
        name = self.resolve_tool("symbols")
        args = {} if mw_only else {"include_hidden": True}
        envelope = self.mcp_call_tool(name, args)
        payload = self._unwrap_tool_text(envelope, name)
        records = payload.get("symbols", []) or []
        symbols = {
            record.get("symbol") for record in records
            if isinstance(record, dict) and record.get("symbol")
        }
        blacklist = self._parse_blacklist()
        if blacklist:
            symbols -= blacklist
        return sorted(symbols)

    def _show_marketwatch_symbols(self):
        if not self.connected:
            raise RuntimeError("Symbols: not connected")

        mw_only = self.market_watch_only_var.get()
        bl = self.blacklist_enabled_var.get()
        label = "Market Watch" if mw_only else "All Symbols"
        if bl:
            label += " (blacklist)"

        self.log(f"Fetching {label}...")
        symbols = self.fetch_symbols()

        lines = [f"\n--- {label} ({len(symbols)}) ---"]
        if symbols:
            lines.append(", ".join(symbols))
        lines.append("=" * 50)

        self._render_conn_info("\n".join(lines) + "\n")
        for line in lines:
            self.log(line)

    def stop(self):
        self._closing = True
        for attr in ("_auto_connect_job", "_keep_alive_job"):
            job = getattr(self, attr, None)
            if job:
                self.after_cancel(job)
                setattr(self, attr, None)
        self.mcp_disconnect()

    def destroy(self):
        self.stop()
        self.save_state()
        super().destroy()

    def mcp_configure(self, server_ip, port, endpoint, token):
        self.server_ip = server_ip
        self.port = port
        self.endpoint = (
            endpoint if endpoint.startswith("/") else f"/{endpoint}"
        )
        self.token = token

    def _mcp_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _mcp_raw_post(self, payload: dict) -> requests.Response:
        url = f"{self.scheme}://{self.server_ip}:{self.port}{self.endpoint}"
        return requests.post(
            url, json=payload,
            headers=self._mcp_headers(),
            timeout=self.HTTP_TIMEOUT,
        )

    def _mcp_parse_response(self, response, request_id=None) -> dict:
        content_type = response.headers.get("Content-Type", "")

        if "text/event-stream" not in content_type:
            return response.json()

        result = None
        for line in response.text.splitlines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                continue
            if request_id is not None and parsed.get("id") == request_id:
                return parsed
            result = parsed
        return result

    def mcp_connect(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            refused = probe.connect_ex((self.server_ip, int(self.port)))
        if refused:
            self.after(0, self.log,
                       f"[MCP] {self.server_ip}:{self.port} unreachable "
                       f"(errno {refused}), retrying...")
            return False

        init_payload = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.CLIENT_NAME,
                               "version": self.CLIENT_VERSION},
            },
        }
        r = self._mcp_raw_post(init_payload, timeout=self.HTTP_TIMEOUT)

        if r.status_code != 200:
            self.after(0, self.log,
                       f"[MCP] initialize failed: HTTP {r.status_code}")
            return False
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        body = self._mcp_parse_response(r, request_id=1)
        if not body or "result" not in body:
            self.after(0, self.log, "[MCP] initialize returned no result")
            return False
        result = body["result"]
        self.protocol_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo", {})
        self._mcp_raw_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=self.HTTP_TIMEOUT)

        self.connected = True
        self.after(0, self.log,
                   f"[MCP] Connected to {self.scheme}://"
                   f"{self.server_ip}:{self.port}{self.endpoint}")
        if callable(self.on_connect_success):
            self.after(0, self.on_connect_success)
        return True

    def mcp_disconnect(self):
        self.connected = False
        self.session_id = None

    def mcp_rpc(self, method: str, params: dict = None) -> dict:
        self._rpc_inflight += 1
        try:
            req_id = int(datetime.now().timestamp() * 1000)
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            response = self._mcp_raw_post(payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"[MCP] {method} -> HTTP {response.status_code}"
                )

            body = self._mcp_parse_response(response, request_id=req_id)
            if not body:
                raise RuntimeError(f"[MCP] {method} -> empty body")
            if "error" in body:
                raise RuntimeError(f"[MCP] {method} error: {body['error']}")
            return body.get("result")
        finally:
            self._rpc_inflight -= 1

    def mcp_list_tools(self) -> list:
        result = self.mcp_rpc("tools/list", {})
        return result["tools"] if result and "tools" in result else []

    def mcp_call_tool(self, name: str, arguments: dict = None) -> dict:
        return self.mcp_rpc(
            "tools/call", {"name": name, "arguments": arguments or {}},
        )

    def mcp_list_resources(self) -> list:
        result = self.mcp_rpc("resources/list", {})
        return result["resources"] if result and "resources" in result else []

    def mcp_list_prompts(self) -> list:
        result = self.mcp_rpc("prompts/list", {})
        return result["prompts"] if result and "prompts" in result else []

    def filter_symbols(self, symbols):
        if not symbols:
            return []
        blacklist = self._parse_blacklist()
        return sorted({s for s in symbols if s and s not in blacklist})

    def get_marketwatch_symbols(self):
        mw_only = self.market_watch_only_var.get()
        args = {} if mw_only else {"include_hidden": True}

        name = self.resolve_tool("symbols")
        envelope = self.mcp_call_tool(name, args)
        payload = self._unwrap_tool_text(envelope, name)
        records = payload.get("symbols", []) or []
        return [
            record.get("symbol") for record in records
            if isinstance(record, dict) and record.get("symbol")
        ]

    def call_semantic(self, key, arguments=None):
        name = self.resolve_tool(key)
        result = self.mcp_call_tool(name, arguments or {})
        if result and result.get("isError"):
            self.log(f"[semantic] {name} returned isError")
        return result

    def count_available_bars(self, symbol, timeframe, probe=500):
        bars = self.get_rates(symbol, timeframe, probe)
        return len(bars) if bars else 0

    def get_symbol_volume_info(self, symbol):
        for name in ("get_symbol_info", "get_symbol_properties", "symbol_info"):
            registry = getattr(self, "_tool_registry", None)
            if not registry or name not in registry:
                continue
            envelope = self.mcp_call_tool(name, {"symbol": symbol})
            if not envelope or envelope.get("isError"):
                continue
            payload = self._unwrap_tool_text(envelope, name)
            if not isinstance(payload, dict):
                continue
            info = payload
            for key in ("symbol", "data", "result", "info"):
                value = payload.get(key)
                if isinstance(value, dict):
                    info = value
                    break
            return (
                float(info.get(
                    "volume_min",
                    info.get("min_volume", info.get("volumeMin", 0.01)),
                )),
                float(info.get(
                    "volume_max",
                    info.get("max_volume", info.get("volumeMax", 100.0)),
                )),
                float(info.get(
                    "volume_step",
                    info.get("step_volume", info.get("volumeStep", 0.01)),
                )),
            )
        return (0.01, 100.0, 0.01)

    def refresh_tool_registry(self) -> dict:
        if self._tool_map and self._tool_registry:
            return self._tool_registry

        tools = self.mcp_list_tools() or []
        registry = {}
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                registry[tool["name"]] = tool

        resolved = {}
        missing = {}
        for key, candidates in TOOL_CANDIDATES.items():
            hit = next((c for c in candidates if c in registry), None)
            if hit:
                resolved[key] = hit
            else:
                missing[key] = candidates

        self._tool_registry = registry
        self._tool_map = resolved

        if missing:
            self.log(
                f"[tools] unresolved semantic keys: "
                f"{', '.join(sorted(missing))}",
            )
        return registry

    def resolve_tool(self, key: str):
        if not self._tool_map:
            self.refresh_tool_registry()
        name = self._tool_map.get(key)
        if not name:
            raise RuntimeError(f"[tools] no server tool resolved for '{key}'")
        return name


class Mt5FetchData(ctk.CTkFrame):
    """Historical bar fetcher for one or all Market Watch symbols."""

    TITLE_FONT = ("Arial", 14, "bold")
    TITLE_COLOR = "yellow"
    UI_FONT = ("Arial", 12)
    MONO_FONT = ("Courier", 12)
    MONO_BOLD = ("Courier", 12, "bold")
    BUTTON_FONT = ("Arial", 12, "bold")

    COLOR_BORDER = "white"
    BORDER_WIDTH = 2
    BG_COLOR = "transparent"

    COLOR_ALL_BTN = "#4285F4"
    COLOR_ALL_BTN_TEXT = "yellow"
    COLOR_SINGLE_BTN = "#E67E22"
    COLOR_SINGLE_BTN_TEXT = "white"
    COLOR_AVAIL_TEXT = "#00FFFF"
    COLOR_FETCHED_TEXT = "#00FF00"

    DEFAULT_DATA_FOLDER = os.path.join(str(Path.home()), "mt5_data")
    DEFAULT_FROM_OFFSET_DAYS = 30

    TIMEFRAME_ORDER = ["M5", "M15", "H1", "H4", "D1", "W1"]
    TF_MINUTES = {
        "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
        "M6": 6, "M10": 10, "M12": 12, "M15": 15,
        "M20": 20, "M30": 30,
        "H1": 60, "H2": 120, "H3": 180, "H4": 240,
        "H6": 360, "H8": 480, "H12": 720,
        "D1": 1440, "W1": 10080, "MN1": 43200,
    }

    TF_MAX_BARS = {
        "M1": 500_000,
        "M5": 200_000,
        "M15": 100_000,
        "H1": 50_000,
        "H4": 30_000,
        "D1": 5_000,
        "W1": 1_000,
        "MN1": 240,
    }

    RANGE_RETRY_STEPS = 4
    AVAILABILITY_PROBE_BARS = 500

    TABLE_COL_WIDTH = 8

    TOOL_CANDIDATES = {
        "symbols": [
            "get_marketwatch_symbols",
            "market_watch_get_symbols",
            "get_symbols",
        ],
        "rates": [
            "get_chart_history",
            "market_data_get_candles",
            "get_candles",
            "get_bars",
            "get_rates",
        ],
    }

    CSV_COLUMNS = ("time", "open", "high", "low", "close", "tick_volume")

    def __init__(self, master, mcp_client, config_path=None,
                 log_callback=None, **kwargs):
        super().__init__(
            master,
            border_width=self.BORDER_WIDTH,
            border_color=self.COLOR_BORDER,
            fg_color=self.BG_COLOR,
            **kwargs,
        )

        self.mcp_client = mcp_client
        self.log_callback = log_callback

        if config_path is None:
            script_name = os.path.splitext(os.path.basename(__file__))[0]
            config_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"{script_name}.ini",
            )
        self.ini_path = config_path
        self.ini = configparser.ConfigParser(interpolation=None)
        if os.path.exists(self.ini_path):
            self.ini.read(self.ini_path, encoding="utf-8")

        self._tool_map = {}
        self._tool_registry = {}

        self.data_folder_path = self.DEFAULT_DATA_FOLDER

        self._build_ui()
        self._load_fetch_state()

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)

    def is_connected(self):
        return bool(
            self.mcp_client
            and getattr(self.mcp_client, "connected", False)
        )

    def set_client(self, mcp_client):
        self.mcp_client = mcp_client
        self._tool_map.clear()
        self._tool_registry.clear()

    def get_selected_timeframes(self):
        return [
            tf for tf in self.TIMEFRAME_ORDER
            if self.timeframe_vars[tf].get()
        ]

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Fetch MT5 Data",
            font=self.TITLE_FONT, text_color=self.TITLE_COLOR, anchor="w",
        ).pack(fill="x", padx=6, pady=(6, 2))

        self._build_folder_row()
        self._build_date_range_row()
        self._build_action_row()
        self._build_timeframe_row()

    def _build_folder_row(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)

        ctk.CTkButton(
            row, text="MT5 Data Folder", font=self.UI_FONT,
            command=self._select_data_folder,
        ).pack(side="left", padx=2)

        self.data_folder_label = ctk.CTkLabel(
            row, text="", font=self.UI_FONT, anchor="w",
        )
        self.data_folder_label.pack(side="left", padx=2, fill="x", expand=True)

    def _build_date_range_row(self):
        today = datetime.now(timezone.utc).date()
        from_date = today - timedelta(days=self.DEFAULT_FROM_OFFSET_DAYS)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)

        ctk.CTkLabel(row, text="From", font=self.UI_FONT, anchor="w").pack(
            side="left", padx=(2, 4),
        )

        self.from_year = ctk.CTkEntry(
            row, width=54, font=self.UI_FONT, border_width=2,
        )
        self.from_year.pack(side="left", padx=1)
        self.from_year.insert(0, f"{from_date.year:04d}")

        ctk.CTkLabel(row, text="-", font=self.UI_FONT, anchor="center").pack(
            side="left", padx=1,
        )

        self.from_month = ctk.CTkEntry(
            row, width=40, font=self.UI_FONT, border_width=2,
        )
        self.from_month.pack(side="left", padx=1)
        self.from_month.insert(0, f"{from_date.month:02d}")

        ctk.CTkLabel(row, text="-", font=self.UI_FONT, anchor="center").pack(
            side="left", padx=1,
        )

        self.from_day = ctk.CTkEntry(
            row, width=40, font=self.UI_FONT, border_width=2,
        )
        self.from_day.pack(side="left", padx=1)
        self.from_day.insert(0, f"{from_date.day:02d}")

        ctk.CTkLabel(row, text="   ", font=self.UI_FONT).pack(side="left")

        ctk.CTkLabel(row, text="To", font=self.UI_FONT, anchor="w").pack(
            side="left", padx=(4, 4),
        )

        self.to_year = ctk.CTkEntry(
            row, width=54, font=self.UI_FONT, border_width=2,
        )
        self.to_year.pack(side="left", padx=1)
        self.to_year.insert(0, f"{today.year:04d}")

        ctk.CTkLabel(row, text="-", font=self.UI_FONT, anchor="center").pack(
            side="left", padx=1,
        )

        self.to_month = ctk.CTkEntry(
            row, width=40, font=self.UI_FONT, border_width=2,
        )
        self.to_month.pack(side="left", padx=1)
        self.to_month.insert(0, f"{today.month:02d}")

        ctk.CTkLabel(row, text="-", font=self.UI_FONT, anchor="center").pack(
            side="left", padx=1,
        )

        self.to_day = ctk.CTkEntry(
            row, width=40, font=self.UI_FONT, border_width=2,
        )
        self.to_day.pack(side="left", padx=1)
        self.to_day.insert(0, f"{today.day:02d}")

        self.available_bars_btn = ctk.CTkButton(
            row, text="Available Bars", width=110,
            command=self._show_available_bars,
            fg_color=self.COLOR_SINGLE_BTN,
            text_color=self.COLOR_SINGLE_BTN_TEXT,
            font=self.BUTTON_FONT, state="disabled",
        )
        self.available_bars_btn.pack(side="left", padx=(8, 2))

    def _build_action_row(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)

        self.fetch_all_btn = ctk.CTkButton(
            row, text="All", width=50,
            command=self._fetch_all_symbols,
            fg_color=self.COLOR_ALL_BTN,
            text_color=self.COLOR_ALL_BTN_TEXT,
            font=self.BUTTON_FONT, state="disabled",
        )
        self.fetch_all_btn.pack(side="left", padx=2)

        self.fetch_single_btn = ctk.CTkButton(
            row, text="Single", width=70,
            command=self._fetch_single_symbol,
            fg_color=self.COLOR_SINGLE_BTN,
            text_color=self.COLOR_SINGLE_BTN_TEXT,
            font=self.BUTTON_FONT, state="disabled",
        )
        self.fetch_single_btn.pack(side="left", padx=2)

        ctk.CTkLabel(row, text="Symbol:", font=self.UI_FONT).pack(side="left", padx=2)

        self.symbol_combo = ctk.CTkComboBox(
            row, width=100, state="readonly", border_width=2,
            font=self.UI_FONT, dropdown_font=self.UI_FONT,
            command=self._on_symbol_selected,
        )
        self.symbol_combo.pack(side="left", fill="x", expand=True, padx=2)
        self.symbol_combo.set("Select Symbol")

        self.refresh_symbols_btn = ctk.CTkButton(
            row, text="↻", width=32,
            command=self._refresh_symbols,
            font=self.UI_FONT, state="disabled",
        )
        self.refresh_symbols_btn.pack(side="left", padx=2)

    def _build_timeframe_row(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=6, pady=2)

        ctk.CTkLabel(row, text="TF:", width=30, font=self.UI_FONT).pack(
            side="left", padx=2,
        )

        self.timeframe_vars = {}
        for tf in self.TIMEFRAME_ORDER:
            var = ctk.BooleanVar(value=True)
            self.timeframe_vars[tf] = var
            ctk.CTkCheckBox(
                row, text=tf, width=60, variable=var,
                font=self.UI_FONT, border_width=2,
            ).pack(side="left", padx=2, fill="x", expand=True)

    def _build_table(self):
        self.table_frame = ctk.CTkFrame(self, height=60, border_width=2)
        self.table_frame.pack(fill="x", padx=6, pady=(2, 6))

        self.table_header_label = ctk.CTkLabel(
            self.table_frame, text="", font=self.MONO_BOLD, anchor="w",
        )
        self.table_header_label.pack(fill="x", padx=4, pady=2)

        self.table_available_label = ctk.CTkLabel(
            self.table_frame, text="", font=self.MONO_FONT,
            text_color=self.COLOR_AVAIL_TEXT, anchor="w",
        )
        self.table_available_label.pack(fill="x", padx=4, pady=2)

        self.table_fetched_label = ctk.CTkLabel(
            self.table_frame, text="", font=self.MONO_BOLD,
            text_color=self.COLOR_FETCHED_TEXT, anchor="w",
        )
        self.table_fetched_label.pack(fill="x", padx=4, pady=2)

    def _load_fetch_state(self):
        today = datetime.now(timezone.utc).date()
        from_date = today - timedelta(days=self.DEFAULT_FROM_OFFSET_DAYS)

        saved_from = f"{from_date.year:04d}-{from_date.month:02d}-{from_date.day:02d}"
        saved_to = f"{today.year:04d}-{today.month:02d}-{today.day:02d}"

        if self.ini.has_section("Fetch"):
            self.data_folder_path = self.ini.get(
                "Fetch", "data_folder", fallback=self.DEFAULT_DATA_FOLDER,
            )
            saved_from = self.ini.get(
                "Fetch", "date_from", fallback=saved_from,
            )
            saved_to = self.ini.get(
                "Fetch", "date_to", fallback=saved_to,
            )

            saved_tfs = [
                tf.strip() for tf in self.ini.get(
                    "Fetch", "fetch_timeframes",
                    fallback=",".join(self.TIMEFRAME_ORDER),
                ).split(",")
                if tf.strip()
            ]
            for tf in self.TIMEFRAME_ORDER:
                self.timeframe_vars[tf].set(tf in saved_tfs)

        self.data_folder_label.configure(text=self.data_folder_path)

        fy, fm, fd = saved_from.split("-")
        ty, tm, td = saved_to.split("-")

        self.from_year.delete(0, "end")
        self.from_year.insert(0, fy)
        self.from_month.delete(0, "end")
        self.from_month.insert(0, fm)
        self.from_day.delete(0, "end")
        self.from_day.insert(0, fd)

        self.to_year.delete(0, "end")
        self.to_year.insert(0, ty)
        self.to_month.delete(0, "end")
        self.to_month.insert(0, tm)
        self.to_day.delete(0, "end")
        self.to_day.insert(0, td)

    def save_config(self):
        date_from = (
            f"{self.from_year.get().strip()}-"
            f"{self.from_month.get().strip()}-"
            f"{self.from_day.get().strip()}"
        )
        date_to = (
            f"{self.to_year.get().strip()}-"
            f"{self.to_month.get().strip()}-"
            f"{self.to_day.get().strip()}"
        )
        Config.atomic_merge_ini(self.ini_path, "Fetch", {
            "date_from":        date_from,
            "date_to":          date_to,
            "fetch_timeframes": ",".join(self.get_selected_timeframes()),
            "data_folder":      self.data_folder_path,
        })

    def _resolve_tools(self):
        if self._tool_map:
            return
        if not self.is_connected():
            return

        tools = self.mcp_client.mcp_list_tools() or []
        registry = {}
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                registry[tool["name"]] = tool
        self._tool_registry = registry

        resolved = {}
        missing = []
        for key, candidates in self.TOOL_CANDIDATES.items():
            hit = next((c for c in candidates if c in registry), None)
            if hit:
                resolved[key] = hit
            else:
                missing.append(key)
        self._tool_map = resolved

        if missing:
            self.log(f"[tools] unresolved in Fetch: {', '.join(missing)}")

    def _call_tool(self, semantic_key, args):
        self._resolve_tools()
        name = self._tool_map.get(semantic_key)
        if not name:
            raise RuntimeError(
                f"[fetch] no server tool resolved for '{semantic_key}'"
            )
        return self.mcp_client.mcp_call_tool(name, args or {})

    @staticmethod
    def _unwrap_tool_text(envelope, label):
        if envelope is None:
            raise RuntimeError(f"{label}: empty response")
        if envelope.get("isError"):
            content = envelope.get("content") or []
            text = ""
            if content and isinstance(content[0], dict):
                text = content[0].get("text", "")
            raise RuntimeError(f"{label}: MCP tool error: {text}")

        structured = envelope.get("structuredContent")
        if isinstance(structured, (dict, list)):
            return structured

        content = envelope.get("content") or []
        if not content or not isinstance(content[0], dict):
            raise RuntimeError(f"{label}: unexpected MCP envelope")
        text = content[0].get("text", "")
        if not text:
            raise RuntimeError(f"{label}: empty tool payload")
        return json.loads(text)

    @staticmethod
    def _extract_bars(payload):
        if isinstance(payload, list):
            return [b for b in payload if isinstance(b, dict)]
        if not isinstance(payload, dict):
            return []
        for key in (
            "data", "bars", "items", "candles", "history",
            "rates", "ohlc", "result",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [b for b in value if isinstance(b, dict)]
        return []

    def get_rates(self, symbol, timeframe, count):
        count = min(int(count), Config.TF_MAX_BARS.get(timeframe, int(count)))
        tf_min = Config.TF_MINUTES.get(timeframe, 5)

        dt_to = datetime.now(timezone.utc)

        request = {
            "symbol": symbol,
            "period": timeframe,
            "datetime_to": dt_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "limit": count,
        }

        max_span_minutes = 5 * 365 * 24 * 60
        span_minutes = tf_min * (count + 5)
        if span_minutes <= max_span_minutes:
            dt_from = dt_to - timedelta(minutes=span_minutes)
            request["datetime_from"] = dt_from.strftime("%Y-%m-%dT%H:%M:%S")

        name = self.resolve_tool("rates")
        envelope = self.mcp_call_tool(name, request)
        payload = self._unwrap_tool_text(envelope, name)
        bars = self._extract_bars(payload)
        return bars[:count]

    def fetch_symbols(self):
        mw_var = getattr(self.mcp_client, "market_watch_only_var", None)
        mw_only = mw_var.get() if mw_var is not None else True

        args = {} if mw_only else {"include_hidden": True}
        envelope = self._call_tool("symbols", args)
        payload = self._unwrap_tool_text(
            envelope, self._tool_map.get("symbols", "symbols"),
        )

        if isinstance(payload, dict):
            records = payload.get("symbols", []) or []
        elif isinstance(payload, list):
            records = payload
        else:
            records = []

        symbols = {
            record.get("symbol") for record in records
            if isinstance(record, dict) and record.get("symbol")
        }

        parse_blacklist = getattr(self.mcp_client, "_parse_blacklist", None)
        if callable(parse_blacklist):
            symbols -= parse_blacklist()
        return sorted(symbols)

    def _filter_symbols(self, symbols):
        if not symbols:
            return []
        filter_fn = getattr(self.mcp_client, "filter_symbols", None)
        if callable(filter_fn):
            return filter_fn(symbols)
        return sorted(set(symbols))

    def _resolve_datetime_range(self):
        dt_from = datetime(
            int(self.from_year.get().strip()),
            int(self.from_month.get().strip()),
            int(self.from_day.get().strip()),
            tzinfo=timezone.utc,
        )
        dt_to = datetime(
            int(self.to_year.get().strip()),
            int(self.to_month.get().strip()),
            int(self.to_day.get().strip()),
            tzinfo=timezone.utc,
        ) + timedelta(days=1) - timedelta(seconds=1)

        if dt_from > dt_to:
            raise RuntimeError(
                "Fetch: 'From' date must be before or equal to 'To' date"
            )
        return dt_from, dt_to

    def _clamp_count(self, timeframe, count):
        cap = self.TF_MAX_BARS.get(timeframe)
        return min(int(count), cap) if cap else int(count)

    def _estimate_bars(self, timeframe, dt_from, dt_to):
        tf_min = self.TF_MINUTES.get(timeframe, 5)
        total_minutes = max(1, int((dt_to - dt_from).total_seconds() // 60))
        return self._clamp_count(timeframe, total_minutes // tf_min + 5)

    def _build_rates_request(self, symbol, timeframe, dt_from, dt_to):
        return {
            "symbol": symbol,
            "period": timeframe,
            "datetime_from": dt_from.strftime("%Y-%m-%dT%H:%M:%S"),
            "datetime_to":   dt_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "limit":         self._estimate_bars(timeframe, dt_from, dt_to),
        }

    def _is_range_error(self, envelope):
        content = envelope.get("content") or []
        text = content[0].get("text", "") if content else ""
        return "invalid datetime range" in text.lower()

    def fetch_rates(self, symbol, timeframe, dt_from=None, dt_to=None):
        if dt_from is None or dt_to is None:
            dt_from, dt_to = self._resolve_datetime_range()

        dt_to_attempt = dt_to

        for step in range(self.RANGE_RETRY_STEPS + 1):
            request = self._build_rates_request(
                symbol, timeframe, dt_from, dt_to_attempt,
            )
            envelope = self._call_tool("rates", request)

            if not envelope.get("isError"):
                payload = self._unwrap_tool_text(
                    envelope, self._tool_map.get("rates", "rates"),
                )
                return self._extract_bars(payload)

            if not self._is_range_error(envelope):
                content = envelope.get("content") or []
                err = content[0].get("text", "") if content else ""
                raise RuntimeError(
                    f"[rates] {symbol} {timeframe}: {err}"
                )

            if step + 1 > self.RANGE_RETRY_STEPS:
                raise RuntimeError(
                    f"[rates] {symbol} {timeframe}: range errors after retries"
                )
            dt_to_attempt = dt_from + (dt_to_attempt - dt_from) * 0.5

        raise RuntimeError(
            f"[rates] {symbol} {timeframe}: range errors after retries"
        )

    def count_available_bars(self, symbol, timeframe):
        dt_from, dt_to = self._resolve_datetime_range()
        return len(self.fetch_rates(symbol, timeframe, dt_from, dt_to))

    def _select_data_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self.data_folder_path,
            parent=self.winfo_toplevel(),
        )
        if folder:
            self.data_folder_path = folder
            self.data_folder_label.configure(text=self.data_folder_path)
            self.save_config()

    def _wipe_data_folder(self):
        if os.path.isdir(self.data_folder_path):
            shutil.rmtree(self.data_folder_path)
            self.log(f"🧹 Removed {self.data_folder_path}")
        else:
            self.log(f"🧹 No existing data folder at {self.data_folder_path}")

    def _on_symbol_selected(self, event=None):
        return

    def _show_available_bars(self):
        if not self.is_connected():
            raise RuntimeError("Available Bars: not connected")

        symbols = self._filter_symbols(self.fetch_symbols())
        if not symbols:
            raise RuntimeError("Available Bars: no Market Watch symbols available")

        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs:
            raise RuntimeError("Available Bars: no timeframe selected")

        dt_from, dt_to = self._resolve_datetime_range()
        self.save_config()
        self.log(
            f"🔍 Scanning available bars for {len(symbols)} symbols "
            f"{dt_from:%Y-%m-%d} → {dt_to:%Y-%m-%d}",
        )
        self.available_bars_btn.configure(state="disabled")

        def scan_thread():
            header = "TF:" + "".join(
                "".join([" " * ((self.TABLE_COL_WIDTH - len(tf)) // 2), tf])
                for tf in self.TIMEFRAME_ORDER
            )
            self.log(header)
            for idx, symbol in enumerate(symbols, 1):
                counts = {}
                for tf in selected_tfs:
                    data = self.fetch_rates(symbol, tf, dt_from, dt_to)
                    counts[tf] = len(data)
                    time.sleep(0.05)
                row = symbol.ljust(12)
                for tf in self.TIMEFRAME_ORDER:
                    n = counts.get(tf, 0)
                    text = self._fmt_bars(n) if n > 0 else "N/A"
                    row += " " * (self.TABLE_COL_WIDTH - len(text)) + text
                self.log(f"{idx}/{len(symbols)} {row}")
                time.sleep(0.1)
            self.log(f"✅ Available bars scan complete: {len(symbols)} symbols")
            self.after(0, lambda: self.available_bars_btn.configure(state="normal"))

        threading.Thread(target=scan_thread, daemon=True).start()

    def _refresh_symbols(self):
        if not self.is_connected():
            raise RuntimeError("Fetch: refresh symbols not connected")

        def get_thread():
            self.log("📋 Fetching symbols...")
            symbols = self.fetch_symbols()
            symbols = self._filter_symbols(symbols)
            self.after(0, self._update_symbols_dropdown, symbols)
            self.log(f"✅ Got {len(symbols)} symbols")

        threading.Thread(target=get_thread, daemon=True).start()

    def _update_symbols_dropdown(self, symbols):
        filtered = sorted(set(self._filter_symbols(symbols)))
        self.symbol_combo.configure(values=filtered)
        if filtered:
            current = self.symbol_combo.get()
            self.symbol_combo.set(
                current if current in filtered else filtered[0],
            )

    def update_symbols(self, symbols):
        filtered = sorted(set(self._filter_symbols(symbols)))
        self.symbol_combo.configure(values=filtered)
        if filtered:
            self.symbol_combo.set(filtered[0])
        self.refresh_symbols_btn.configure(state="normal")
        self.fetch_all_btn.configure(state="normal")
        self.fetch_single_btn.configure(state="normal")
        self.available_bars_btn.configure(state="normal")

    @staticmethod
    def _fmt_bars(n):
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    def _row_for(self, values, label):
        cw = self.TABLE_COL_WIDTH
        row = label
        for tf in self.TIMEFRAME_ORDER:
            n = values.get(tf, 0)
            if n > 0:
                text = self._fmt_bars(n)
                row += " " * (cw - len(text)) + text
            else:
                na = "N/A"
                left = (cw - len(na)) // 2
                right = (cw - len(na)) - left
                row += " " * left + na + " " * right
        return row

    def _update_available_display(self, available):
        cw = self.TABLE_COL_WIDTH
        header = "TF:" + "".join(
            " " * ((cw - len(tf)) // 2) + tf
            + " " * ((cw - len(tf)) - (cw - len(tf)) // 2)
            for tf in self.TIMEFRAME_ORDER
        )
        self.table_header_label.configure(text=header)
        self.table_available_label.configure(
            text=self._row_for(available, "Avail:"),
        )
        self.table_fetched_label.configure(
            text="Fetched:" + " " * (cw * len(self.TIMEFRAME_ORDER)),
        )

    def _update_fetched_display(self, fetched):
        self.table_fetched_label.configure(
            text=self._row_for(fetched, "Fetched:"),
        )

    def _fetch_single_symbol(self):
        if not self.is_connected():
            raise RuntimeError("Fetch: single not connected")
        symbol = self.symbol_combo.get()
        if not symbol or symbol == "Select Symbol":
            raise RuntimeError("Fetch: no symbol selected")
        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs:
            raise RuntimeError("Fetch: no timeframe selected")

        dt_from, dt_to = self._resolve_datetime_range()
        target_dir = self.data_folder_path
        self.save_config()
        self.log(
            f"🚀 Fetching {symbol} "
            f"{dt_from:%Y-%m-%d} → {dt_to:%Y-%m-%d}"
        )
        self.fetch_all_btn.configure(state="disabled")
        self.fetch_single_btn.configure(state="disabled")
        self.available_bars_btn.configure(state="disabled")

        def fetch_thread():
            self._wipe_data_folder()

            for tf in selected_tfs:
                data = self.fetch_rates(symbol, tf, dt_from, dt_to)
                if data:
                    self._save_to_csv(symbol, tf, data, target_dir)
                time.sleep(0.1)
            self.after(0, lambda: self.fetch_all_btn.configure(state="normal"))
            self.after(0, lambda: self.fetch_single_btn.configure(state="normal"))
            self.after(0, lambda: self.available_bars_btn.configure(state="normal"))

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _fetch_all_symbols(self):
        if not self.is_connected():
            raise RuntimeError("Fetch: all not connected")

        symbols = self._filter_symbols(self.fetch_symbols())
        if not symbols:
            raise RuntimeError("Fetch: no Market Watch symbols available")

        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs:
            raise RuntimeError("Fetch: no timeframe selected")

        dt_from, dt_to = self._resolve_datetime_range()
        target_dir = self.data_folder_path
        self.save_config()
        self.log(
            f"🚀 Fetching {len(symbols)} symbols "
            f"{dt_from:%Y-%m-%d} → {dt_to:%Y-%m-%d}"
        )
        self.fetch_all_btn.configure(state="disabled")
        self.fetch_single_btn.configure(state="disabled")
        self.available_bars_btn.configure(state="disabled")

        def fetch_thread():
            self._wipe_data_folder()

            for idx, symbol in enumerate(symbols, 1):
                self.log(f"📈 {idx}/{len(symbols)}: {symbol}")
                for tf in selected_tfs:
                    data = self.fetch_rates(symbol, tf, dt_from, dt_to)
                    if data:
                        self._save_to_csv(symbol, tf, data, target_dir)
                    time.sleep(0.1)
                time.sleep(0.2)
            self.after(0, lambda: self.log(
                f"✅ Fetch complete: {len(symbols)} symbols",
            ))
            self.after(0, lambda: self.fetch_all_btn.configure(state="normal"))
            self.after(0, lambda: self.fetch_single_btn.configure(state="normal"))
            self.after(0, lambda: self.available_bars_btn.configure(state="normal"))

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _save_to_csv(self, symbol, timeframe, data, target_dir):
        safe_symbol = re.sub(r"[^A-Za-z0-9_+#!$-]", "_", symbol)
        symbol_dir = os.path.join(target_dir, safe_symbol)
        os.makedirs(symbol_dir, exist_ok=True)

        rows = []
        for bar in data:
            row = {}
            for col in self.CSV_COLUMNS:
                if col in bar:
                    row[col] = bar[col]
                elif col == "tick_volume" and "volume" in bar:
                    row["tick_volume"] = bar["volume"]
            if row:
                rows.append(row)
        if not rows:
            return

        filepath = os.path.join(symbol_dir, f"{safe_symbol}_{timeframe}.csv")
        with open(filepath, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(self.CSV_COLUMNS),
                lineterminator=os.linesep,
            )
            writer.writeheader()
            writer.writerows(rows)
        self.log(f"💾 Saved to {filepath}")


class Mt5Fetch(ctk.CTk):
    """Top-level application window; owns frames, wiring, and lifecycle."""

    TITLE = "MT5 Fetch MCP"
    MIN_WIDTH = Config.WINDOW_WIDTH
    MIN_HEIGHT = 500
    APPEARANCE = "dark"
    THEME = "blue"
    BG_COLOR = "#1a1a1a"

    def __init__(self):
        super().__init__()

        self.ini_path = Config.INI_FILE

        ctk.set_appearance_mode(self.APPEARANCE)
        ctk.set_default_color_theme(self.THEME)

        self.title(self.TITLE)
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resizable(True, True)
        self.configure(fg_color=self.BG_COLOR)

        saved_x, saved_y = Config.load_window_geometry(self.ini_path)
        self._apply_geometry(saved_x, saved_y)

        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.log_frame = LogFrame(self)
        self.log_frame.grid(
            row=2, column=0, sticky="nsew", padx=6, pady=(3, 6),
        )

        self.mcp_frame = Mt5McpClient(
            self,
            config_path=self.ini_path,
            log_callback=self.log_frame.append,
        )
        self.mcp_frame.grid(
            row=0, column=0, sticky="ew", padx=6, pady=(6, 3),
        )

        self.fetch_frame = Mt5FetchData(
            self,
            mcp_client=self.mcp_frame,
            config_path=self.ini_path,
            log_callback=self.log_frame.append,
        )
        self.fetch_frame.grid(
            row=1, column=0, sticky="ew", padx=6, pady=3,
        )

        original_update = self.mcp_frame._update_connection_status

        def wrapped_update(connected):
            original_update(connected)
            if connected:
                self.fetch_frame.refresh_symbols_btn.configure(state="normal")
                self.fetch_frame.fetch_all_btn.configure(state="normal")
                self.fetch_frame.fetch_single_btn.configure(state="normal")
                self.after(300, self._after_connected)
            else:
                self.fetch_frame.refresh_symbols_btn.configure(state="disabled")
                self.fetch_frame.fetch_all_btn.configure(state="disabled")
                self.fetch_frame.fetch_single_btn.configure(state="disabled")

        self.mcp_frame._update_connection_status = wrapped_update

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_geometry(self, saved_x, saved_y):
        width = Config.WINDOW_WIDTH
        height = Config.WINDOW_HEIGHT
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        if saved_x is None or saved_y is None:
            x = (screen_w - width) // 2
            y = (screen_h - height) // 2
        else:
            x, y = saved_x, saved_y

        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))

        self.geometry(f"{width}x{height}+{x}+{y}")

    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        self.log_frame.append(f"❌ {exc_type.__name__}: {exc_value}")

    def _after_connected(self):
        def worker():
            symbols = self.mcp_frame.get_marketwatch_symbols()
            symbols = self.mcp_frame.filter_symbols(symbols)
            if symbols:
                self.log_frame.append(f"✅ Loaded {len(symbols)} symbols")
                self.after(
                    0, lambda: self.fetch_frame.update_symbols(symbols),
                )
            else:
                self.log_frame.append(
                    "⚠️ No symbols available after blacklist filter",
                )

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        confirmed = messagebox.askyesno(
            "Exit MT5 MCP Trading Client",
            "Are you sure you want to quit?\n\n"
            "Any running operations will be stopped.",
            icon="question",
            parent=self,
        )
        if not confirmed:
            return

        self.mcp_frame.stop()
        self.fetch_frame.save_config()
        self.mcp_frame.save_state()
        Config.save_window_geometry(self.ini_path, self.wm_geometry())
        self.destroy()


if __name__ == "__main__":
    Mt5Fetch().mainloop()