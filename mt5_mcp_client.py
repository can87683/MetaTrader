#!/usr/bin/env python3
# mt5_mcp_client.py
# For MT5 native MCP server only
# Copyright Su Nie | BSD-3C License | https://github.com/can87683

"""configparser customtkinter requests"""

import os
import json
import time
import hashlib
import base64
import configparser
import threading
import socket
import requests
from datetime import datetime, timezone, timedelta
import tempfile

import customtkinter as ctk
import tkinter as tk
import tkinter.ttk as ttk


class Mt5McpClient(ctk.CTkFrame):

    # ---- Window ----
    LOG_FRAME_HEIGHT = 300
    LOG_AUTOSCROLL = True
    LOG_FLUSH_MS = 100

    # ---- MCP defaults ----
    AUTO_CONNECT_DEFAULT = True
    AUTO_CONNECT_DELAY_MS = 500
    AUTO_CONNECT_INTERVAL_MS = 5000
    DEFAULT_SERVER_IP = "127.0.0.1"
    DEFAULT_PORT = "22346"
    DEFAULT_ENDPOINT = "/mcp"
    DEFAULT_TOKEN = ""
    SCHEME = "http"
    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_NAME = ""
    CLIENT_VERSION = ""
    HTTP_TIMEOUT = 30.0
    KEEP_ALIVE_INTERVAL_MS = 15000

    KEEP_ALIVE_TIMEOUT_SEC      = 5.0
    KEEP_ALIVE_MAX_FAILURES     = 3
    RECONNECT_BACKOFF_MS        = (2000, 5000, 10000, 30000)
    RECONNECT_MAX_ATTEMPTS      = 0

    _TOKEN_SALT = b"mcp_client_v1_token_obfuscation"

    TITLE_FONT = ("Arial", 14, "bold")
    UI_FONT = ("Arial", 12)
    MONO_FONT = ("Consolas", 12)
    BUTTON_FONT = ("Arial", 12, "bold")
    LOG_FONT = ("Consolas", 10)

    BG_COLOR = "#1c1c1c"
    TITLE_COLOR = "#ffff00"
    TEXT_COLOR = "#ffffff"

    BUTTON_FG = "white"
    BUTTON_BG = "dark blue"
    BUTTON_BORDER_WIDTH = 2
    BUTTON_ROUNDNESS = 5

    COLOR_STATUS_OK = "#4CAF50"
    COLOR_STATUS_BAD = "#F44336"

    DEFAULT_BLACKLIST = ""

    BAR_SECONDS = {
        "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300,
        "M6": 360, "M10": 600, "M12": 720, "M15": 900, "M20": 1200,
        "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800,
        "H4": 14400, "H6": 21600, "H8": 28800, "H12": 43200,
        "D1": 86400, "W1": 604800, "MN1": 2592000,
    }
    RANGE_BUFFER = 2.0

    MCP_TOOL_LIMIT          = 100000   # per-call output cap sent to MCP tools
    MCP_PAGE_SIZE           = 10000    # bars per page when paging a wide window

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, master, config_path=None, **kwargs):
        super().__init__(master, fg_color=self.BG_COLOR, **kwargs)

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
        self.on_disconnect = None

        self._auto_connect_job = None
        self._keep_alive_job = None
        self._reconnect_job = None
        self._reconnect_attempt = 0
        self._keep_alive_failures = 0
        self._last_ok_request = 0.0
        self._connect_in_flight = False
        self._closing = False

        self._log_pending = []
        self._log_flush_job = None

        self._load_state()
        self._build_ui()

        self._auto_connect_job = self.after(
            self.AUTO_CONNECT_DELAY_MS, self._auto_connect_check)

    # ------------------------------------------------------------------
    # Token obfuscation
    # ------------------------------------------------------------------

    @classmethod
    def _derive_key(cls) -> bytes:
        material = cls._TOKEN_SALT + socket.gethostname().encode("utf-8", "ignore")
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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self):
        if not self.ini.has_section("mcp"):
            self.ini.add_section("mcp")

        self.server_ip_var = ctk.StringVar(
            value=self.ini.get("mcp", "server_ip", fallback=self.DEFAULT_SERVER_IP))
        self.port_var = ctk.StringVar(
            value=self.ini.get("mcp", "port", fallback=self.DEFAULT_PORT))
        self.endpoint_var = ctk.StringVar(
            value=self.ini.get("mcp", "endpoint", fallback=self.DEFAULT_ENDPOINT))
        self.token_var = ctk.StringVar(
            value=self.decrypt_token(
                self.ini.get("mcp", "token", fallback=self.DEFAULT_TOKEN)))
        self.auto_connect_var = ctk.BooleanVar(
            value=self.ini.getboolean("mcp", "auto_connect",
                                      fallback=self.AUTO_CONNECT_DEFAULT))
        self.market_watch_only_var = ctk.BooleanVar(
            value=self.ini.getboolean("mcp", "market_watch_only", fallback=True))
        self.blacklist_enabled_var = ctk.BooleanVar(
            value=self.ini.getboolean("mcp", "blacklist_enabled", fallback=True))
        self.blacklist_var = ctk.StringVar(
            value=self.ini.get("mcp", "blacklist", fallback=self.DEFAULT_BLACKLIST))

    def _atomic_write_ini(self):
        disk = configparser.ConfigParser()
        if os.path.exists(self.ini_path):
            disk.read(self.ini_path, encoding="utf-8")

        if not disk.has_section("mcp"):
            disk.add_section("mcp")
        for k, v in self.ini.items("mcp") if self.ini.has_section("mcp") else []:
            disk.set("mcp", k, v)

        preferred = ["mcp", "Log", "Window"]
        ordered = configparser.ConfigParser()
        ordered.optionxform = str

        for sect in preferred:
            if disk.has_section(sect):
                ordered.add_section(sect)
                for k, v in disk.items(sect):
                    ordered.set(sect, k, v)

        for sect in disk.sections():
            if sect in preferred:
                continue
            ordered.add_section(sect)
            for k, v in disk.items(sect):
                ordered.set(sect, k, v)

        dirn = os.path.dirname(os.path.abspath(self.ini_path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".mcp_", suffix=".ini", dir=dirn)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            ordered.write(f)
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
        self.ini.set("mcp", "market_watch_only",
                     str(self.market_watch_only_var.get()))
        self.ini.set("mcp", "blacklist_enabled",
                     str(self.blacklist_enabled_var.get()))
        self.ini.set("mcp", "blacklist", self.blacklist_var.get())
        self._atomic_write_ini()

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------

    def _build_ui(self):
        conn_frame = ctk.CTkFrame(self, border_width=2, fg_color=self.BG_COLOR)
        conn_frame.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(conn_frame, text="MCP Connection",
                     font=self.TITLE_FONT,
                     text_color=self.TITLE_COLOR,
                     anchor="w").pack(fill="x", padx=2, pady=2)

        ip_row = ctk.CTkFrame(conn_frame, fg_color=self.BG_COLOR, border_width=2)
        ip_row.pack(fill="x", padx=0, pady=0)
        ip_row.pack_propagate(False)
        ip_row.configure(height=34)

        ctk.CTkLabel(ip_row, text="Server IP:",
                     font=self.UI_FONT,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)
        ctk.CTkEntry(ip_row, textvariable=self.server_ip_var,
                     width=90, font=self.UI_FONT,
                     border_width=2,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)

        ctk.CTkLabel(ip_row, text="Port:",
                     font=self.UI_FONT,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)
        ctk.CTkEntry(ip_row, textvariable=self.port_var,
                     width=70, font=self.UI_FONT,
                     border_width=2,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)

        ctk.CTkLabel(ip_row, text="Endpoint:",
                     font=self.UI_FONT,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)
        ctk.CTkEntry(ip_row, textvariable=self.endpoint_var,
                     width=70, font=self.UI_FONT,
                     border_width=2,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)

        token_row = ctk.CTkFrame(conn_frame, fg_color=self.BG_COLOR,
                                 border_width=2)
        token_row.pack(fill="x", padx=0, pady=0)
        token_row.pack_propagate(False)
        token_row.configure(height=34)

        ctk.CTkLabel(token_row, text="Token:",
                     font=self.UI_FONT,
                     text_color=self.TEXT_COLOR).pack(side="left", padx=2)
        self.token_entry = ctk.CTkEntry(token_row, textvariable=self.token_var,
                                        show="*", font=self.UI_FONT,
                                        border_width=2,
                                        text_color=self.TEXT_COLOR)
        self.token_entry.pack(side="left", fill="x", expand=True, padx=2)

        self.token_visible = False
        self.token_toggle = ctk.CTkButton(
            token_row, text="👁", width=30, font=self.UI_FONT,
            fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS,
            command=self.toggle_token)
        self.token_toggle.pack(side="left", padx=2)

        self.token_clean_btn = ctk.CTkButton(
            token_row, text="🧹 Clean", width=70, font=self.UI_FONT,
            fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS,
            command=self.clean_token_entry)
        self.token_clean_btn.pack(side="left", padx=2)

        self.token_save_btn = ctk.CTkButton(
            token_row, text="💾 Save", width=70, font=self.UI_FONT,
            fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS,
            command=self.save_token)
        self.token_save_btn.pack(side="left", padx=2)

        self.token_clear_btn = ctk.CTkButton(
            token_row, text="🗑", width=30, font=self.UI_FONT,
            fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS,
            command=self.clear_saved_token)
        self.token_clear_btn.pack(side="left", padx=2)

        options_row = ctk.CTkFrame(conn_frame, fg_color=self.BG_COLOR,
                                   border_width=2)
        options_row.pack(fill="x", padx=0, pady=0)
        options_row.pack_propagate(False)
        options_row.configure(height=34)

        self.market_watch_only_cb = ctk.CTkCheckBox(
            options_row, text="Market Watch Only",
            variable=self.market_watch_only_var,
            font=self.UI_FONT, text_color=self.TEXT_COLOR,
            command=self._on_options_change)
        self.market_watch_only_cb.pack(side="left", padx=(2, 12))

        self.blacklist_cb = ctk.CTkCheckBox(
            options_row, text="Blacklist:",
            variable=self.blacklist_enabled_var,
            font=self.UI_FONT, text_color=self.TEXT_COLOR,
            command=self._on_options_change)
        self.blacklist_cb.pack(side="left", padx=2)

        self.blacklist_entry = ctk.CTkEntry(
            options_row, textvariable=self.blacklist_var,
            font=self.UI_FONT, border_width=2,
            text_color=self.TEXT_COLOR,
            placeholder_text="SYMBOL1,SYMBOL2,SYMBOL3")
        self.blacklist_entry.pack(side="left", fill="x", expand=True, padx=2)
        self.blacklist_entry.bind("<Return>", self._on_blacklist_edit)
        self.blacklist_entry.bind("<FocusOut>", self._on_blacklist_edit)

        btn_row = ctk.CTkFrame(conn_frame, fg_color=self.BG_COLOR,
                               border_width=2)
        btn_row.pack(fill="x", padx=0, pady=0)
        btn_row.pack_propagate(False)
        btn_row.configure(height=34)

        self.auto_connect_cb = ctk.CTkCheckBox(
            btn_row, text="Auto Connect",
            variable=self.auto_connect_var, font=self.UI_FONT,
            text_color=self.TEXT_COLOR,
            command=self._on_auto_connect_change)
        self.auto_connect_cb.pack(side="left", padx=2)

        self.connect_btn = ctk.CTkButton(
            btn_row, text="Connect", command=self._manual_connect,
            width=100, fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            font=self.BUTTON_FONT,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS)
        self.connect_btn.pack(side="left", padx=2)

        self.list_tools_btn = ctk.CTkButton(
            btn_row, text="List Capabilities", command=self._list_mcp_tools,
            width=130, fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            font=self.BUTTON_FONT,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS)
        self.list_tools_btn.pack(side="left", padx=2)

        self.symbols_btn = ctk.CTkButton(
            btn_row, text="Symbols", command=self._show_marketwatch_symbols,
            width=80, fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            font=self.BUTTON_FONT,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS)
        self.symbols_btn.pack(side="left", padx=2)

        self.schema_btn = ctk.CTkButton(
            btn_row, text="Schema", command=self._show_tool_schemas,
            width=80, fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            font=self.BUTTON_FONT,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS)
        self.schema_btn.pack(side="left", padx=2)

        self.clear_log_btn = ctk.CTkButton(
            btn_row, text="Clear Log", command=self._clear_log,
            width=80, fg_color=self.BUTTON_BG, text_color=self.BUTTON_FG,
            font=self.BUTTON_FONT,
            border_width=self.BUTTON_BORDER_WIDTH,
            corner_radius=self.BUTTON_ROUNDNESS)
        self.clear_log_btn.pack(side="left", padx=2)

        self.conn_status_dot = ctk.CTkLabel(
            btn_row, text="●", text_color=self.COLOR_STATUS_BAD,
            font=self.UI_FONT)
        self.conn_status_dot.pack(side="left", padx=6)

        self.log_frame = ctk.CTkFrame(self, border_width=2,
                                      fg_color=self.BG_COLOR,
                                      height=self.LOG_FRAME_HEIGHT)
        self.log_frame.pack(fill="both", expand=True, padx=0, pady=0)
        self.log_frame.pack_propagate(False)

        self.conn_info = tk.Text(
            self.log_frame, font=("Consolas", 10), wrap="word",
            bg="#141414", fg=self.TEXT_COLOR, insertbackground=self.TEXT_COLOR,
            relief="flat", highlightthickness=0, borderwidth=2)
        self.conn_info.pack(side="left", fill="both", expand=True,
                            padx=(2, 0), pady=2)
        self.conn_info_sb = ttk.Scrollbar(
            self.log_frame, orient="vertical",
            command=self.conn_info.yview)
        self.conn_info_sb.pack(side="right", fill="y", padx=(0, 2), pady=2)
        self.conn_info.configure(yscrollcommand=self.conn_info_sb.set)

        self.conn_info.insert("1.0", "MCP Connection Log\n" + "=" * 50 + "\n")
        self.conn_info.bind("<Key>", lambda e: "break")
        self.conn_info.bind("<<Paste>>", lambda e: "break")
        self.conn_info.bind("<<Cut>>", lambda e: "break")

        menu = tk.Menu(self.conn_info, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_log)
        menu.add_command(label="Clear", command=self._clear_log)
        self.conn_info.bind("<Button-3>",
                            lambda e: menu.tk_popup(e.x_root, e.y_root))

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

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
        self.ini.set("mcp", "token",
                     self.encrypt_token(self.token_var.get().strip()))
        self._atomic_write_ini()
        self.log("🔑 Token saved (obfuscated)")

    def clear_saved_token(self):
        self.token_var.set("")
        self.ini.set("mcp", "token", "")
        self._atomic_write_ini()
        self.log("🗑️ Saved token cleared")

    def _copy_log(self):
        text = (self.conn_info.selection_get()
                if self.conn_info.tag_ranges("sel")
                else self.conn_info.get("1.0", "end-1c"))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.log("✅ Log copied")

    def _clear_log(self):
        if self._log_flush_job:
            self.after_cancel(self._log_flush_job)
            self._log_flush_job = None
        self._log_pending.clear()
        self.conn_info.delete("1.0", "end")
        self.conn_info.insert("1.0", "MCP Connection Log\n" + "=" * 50 + "\n")

    def _on_auto_connect_change(self):
        state = self.auto_connect_var.get()
        if state:
            self.log("Auto Connect enabled")
            if self._auto_connect_job:
                self.after_cancel(self._auto_connect_job)
                self._auto_connect_job = None
            self._auto_connect_job = self.after(
                self.AUTO_CONNECT_INTERVAL_MS, self._auto_connect_check)
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
        ts = datetime.now().strftime("%H:%M:%S")
        self.conn_info.insert("end", f"[{ts}] {message}\n")
        if self.LOG_AUTOSCROLL:
            self.conn_info.see("end")

    # ------------------------------------------------------------------
    # Connection control
    # ------------------------------------------------------------------

    def _manual_connect(self):
        if self._connect_in_flight:
            return
        self._connect_in_flight = True
        self.connect_btn.configure(state="disabled")

        def worker():
            self.mcp_configure(
                self.server_ip_var.get().strip(),
                self.port_var.get().strip(),
                self.endpoint_var.get().strip(),
                self.token_var.get().strip())
            success = self.mcp_connect()

            def finish():
                self._connect_in_flight = False
                if self._closing:
                    return
                self.connect_btn.configure(state="normal")
                self._update_connection_status(success)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_connect_check(self):
        self._auto_connect_job = None
        if self._closing:
            return
        if (self.auto_connect_var.get()
                and not self.connected
                and not self._connect_in_flight
                and self._reconnect_job is None):
            self._manual_connect()
        self._auto_connect_job = self.after(
            self.AUTO_CONNECT_INTERVAL_MS, self._auto_connect_check)

    def _update_connection_status(self, connected: bool):
        if connected:
            self.conn_status_dot.configure(text_color=self.COLOR_STATUS_OK)
            self.connect_btn.configure(text="Disconnect",
                                       command=self._disconnect)
        else:
            self.conn_status_dot.configure(text_color=self.COLOR_STATUS_BAD)
            self.connect_btn.configure(text="Connect",
                                       command=self._manual_connect)

    def _disconnect(self):
        if self._connect_in_flight:
            return
        self.mcp_disconnect()
        self._update_connection_status(False)
        self._reconnect_attempt = 0
        self.log("MCP disconnected — "
                 + ("auto-reconnect armed"
                    if self.auto_connect_var.get()
                    else "stays disconnected (Auto Connect off)"))
        if self.auto_connect_var.get():
            self._schedule_reconnect()

    # ------------------------------------------------------------------
    # Tool views
    # ------------------------------------------------------------------

    def _list_mcp_tools(self):
        if self._closing:
            return
        self.log("Fetching server capabilities...")
        tools = self.mcp_list_tools()
        resources = self.mcp_list_resources()
        prompts = self.mcp_list_prompts()

        self.conn_info.insert("end", "\n--- Server Capabilities ---\n")

        self.conn_info.insert("end", f"\nTools ({len(tools)}):\n")
        for t in tools:
            self.conn_info.insert(
                "end",
                f"  • {t.get('name', 'unknown')}: "
                f"{t.get('description', '')}\n")

        self.conn_info.insert("end", f"\nResources ({len(resources)}):\n")
        for r in resources:
            self.conn_info.insert(
                "end",
                f"  • {r.get('name', r.get('uri', 'unknown'))} "
                f"({r.get('uri', '')})\n")

        self.conn_info.insert("end", f"\nPrompts ({len(prompts)}):\n")
        for p in prompts:
            self.conn_info.insert(
                "end",
                f"  • {p.get('name', 'unknown')}: "
                f"{p.get('description', '')}\n")

        self.conn_info.insert("end", "\n" + "=" * 50 + "\n")
        self.conn_info.see("end")

    def _show_tool_schemas(self):
        if self._closing:
            return
        tools = self.mcp_list_tools()
        if not tools:
            self.log("❌ No tools available — connect first.")
            return
        self.log(f"Fetching schemas for {len(tools)} tools...")
        self.conn_info.insert("end", "\n--- Tool Schemas ---\n")
        self.conn_info.insert("end", "=" * 50 + "\n")
        for t in tools:
            name = t.get("name", "unknown")
            desc = t.get("description", "")
            schema = t.get("inputSchema", {}) or {}
            self.conn_info.insert("end", f"\n● {name}\n")
            if desc:
                self.conn_info.insert("end", f"  {desc}\n")
            pretty = json.dumps(schema, indent=2, ensure_ascii=False)
            for line in pretty.splitlines():
                self.conn_info.insert("end", f"    {line}\n")
        self.conn_info.insert("end", "\n" + "=" * 50 + "\n")
        self.conn_info.see("end")

    def _unwrap_tool_text(self, envelope, label):
        if envelope is None:
            raise RuntimeError(f"{label}: empty response")
        if envelope.get("isError"):
            text = envelope["content"][0]["text"]
            raise RuntimeError(f"{label}: MCP tool error: {text}")
        text = envelope["content"][0]["text"]
        if not text:
            raise RuntimeError(f"{label}: empty tool payload")
        return json.loads(text)

    def fetch_symbols(self):
        mw_only = self.market_watch_only_var.get()
        args = {}
        if not mw_only:
            args["include_hidden"] = True
        envelope = self.mcp_call_tool("get_marketwatch_symbols", args)
        payload = self._unwrap_tool_text(envelope, "get_marketwatch_symbols")
        records = payload.get("symbols", []) or []
        symbols = {r.get("symbol") for r in records
                   if isinstance(r, dict) and r.get("symbol")}
        blacklist = self._parse_blacklist()
        if blacklist:
            symbols -= blacklist
        return sorted(symbols)

    def _show_marketwatch_symbols(self):
        if self._closing:
            return
        if not self.connected:
            self.log("❌ Not connected.")
            return
        mw_only = self.market_watch_only_var.get()
        bl = self.blacklist_enabled_var.get()
        label = "Market Watch" if mw_only else "All Symbols"
        if bl:
            label += " (blacklist)"
        self.log(f"Fetching {label}...")
        symbols = self.fetch_symbols()
        self.conn_info.insert("end", f"\n--- {label} ({len(symbols)}) ---\n")
        if symbols:
            self.conn_info.insert("end", ", ".join(symbols) + "\n")
        self.conn_info.insert("end", "=" * 50 + "\n")
        self.conn_info.see("end")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self):
        self._closing = True
        self._stop_keep_alive()
        if self._log_flush_job:
            self.after_cancel(self._log_flush_job)
            self._log_flush_job = None
        self._log_pending.clear()
        for attr in ("_auto_connect_job", "_reconnect_job"):
            job = getattr(self, attr, None)
            if job:
                self.after_cancel(job)
                setattr(self, attr, None)
        self.connected = False
        self.session_id = None

    def destroy(self):
        self.stop()
        self.save_state()
        super().destroy()

    # ------------------------------------------------------------------
    # MCP transport
    # ------------------------------------------------------------------

    def mcp_configure(self, server_ip, port, endpoint, token):
        self.server_ip = server_ip
        self.port = port
        self.endpoint = (endpoint if endpoint.startswith("/")
                         else f"/{endpoint}")
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

    def _mcp_raw_post(self, payload: dict, timeout: float = None) -> requests.Response:
        url = f"{self.scheme}://{self.server_ip}:{self.port}{self.endpoint}"
        return requests.post(
            url, json=payload, headers=self._mcp_headers(),
            timeout=timeout if timeout is not None else self.HTTP_TIMEOUT)

    def _mcp_parse_response(self, response, request_id=None) -> dict:
        ct = response.headers.get("Content-Type", "")
        if "text/event-stream" not in ct:
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
        self._stop_keep_alive()
        self.connected = False
        self.session_id = None
        self._keep_alive_failures = 0

    def mcp_rpc(self, method: str, params: dict = None) -> dict:
        req_id = int(datetime.now().timestamp() * 1000)
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method,
                   "params": params or {}}
        try:
            r = self._mcp_raw_post(payload)
        except Exception as e:
            self.log(f"[MCP] {method} -> {e}")
            return None
        if r.status_code != 200:
            self.log(f"[MCP] {method} -> HTTP {r.status_code}")
            return None
        body = self._mcp_parse_response(r, request_id=req_id)
        if not body:
            return None
        if "error" in body:
            self.log(f"[MCP] {method} error: {body['error']}")
            return None
        return body.get("result")

    def mcp_list_tools(self) -> list:
        result = self.mcp_rpc("tools/list", {})
        return result["tools"] if result and "tools" in result else []

    def mcp_call_tool(self, name: str, arguments: dict = None) -> dict:
        return self.mcp_rpc("tools/call",
                            {"name": name, "arguments": arguments or {}})

    def mcp_list_resources(self) -> list:
        result = self.mcp_rpc("resources/list", {})
        return result["resources"] if result and "resources" in result else []

    def mcp_list_prompts(self) -> list:
        result = self.mcp_rpc("prompts/list", {})
        return result["prompts"] if result and "prompts" in result else []

    # ------------------------------------------------------------------
    # Keep-alive / reconnect
    # ------------------------------------------------------------------

    def _start_keep_alive(self):
        self._stop_keep_alive()
        self._keep_alive_failures = 0
        self._keep_alive_job = self.after(
            self.KEEP_ALIVE_INTERVAL_MS, self._keep_alive_check)

    def _stop_keep_alive(self):
        if self._keep_alive_job:
            self.after_cancel(self._keep_alive_job)
            self._keep_alive_job = None

    def _keep_alive_check(self):
        self._keep_alive_job = None
        if self._closing or not self.connected:
            return

        def worker():
            resp = self._mcp_raw_post(
                {"jsonrpc": "2.0", "id": int(time.time() * 1000),
                 "method": "ping"},
                timeout=self.KEEP_ALIVE_TIMEOUT_SEC)
            ok = resp.status_code == 200
            self.after(0, self._on_keep_alive_result, ok)

        threading.Thread(target=worker, daemon=True).start()

    def _on_keep_alive_error(self, e: Exception):
        self.log(f"Keep-alive probe failed: {e}")
        self._on_keep_alive_result(False)

    def _on_keep_alive_result(self, ok: bool):
        if not self.connected or self._closing:
            return
        if ok:
            self._keep_alive_failures = 0
            self._reconnect_attempt = 0
            self._keep_alive_job = self.after(
                self.KEEP_ALIVE_INTERVAL_MS, self._keep_alive_check)
            return
        self._keep_alive_failures += 1
        self.log(f"Keep-alive failure {self._keep_alive_failures}/"
                 f"{self.KEEP_ALIVE_MAX_FAILURES}")
        if self._keep_alive_failures >= self.KEEP_ALIVE_MAX_FAILURES:
            self.log("MCP connection declared dead — starting reconnect")
            self._stop_keep_alive()
            self.mcp_disconnect()
            self._update_connection_status(False)
            self._schedule_reconnect()
            return
        self._keep_alive_job = self.after(
            max(1000, self.KEEP_ALIVE_INTERVAL_MS // 2),
            self._keep_alive_check)

    def _schedule_reconnect(self):
        if self._reconnect_job:
            return
        if not self.auto_connect_var.get():
            self.log("Auto-connect off — not reconnecting")
            return
        if (self.RECONNECT_MAX_ATTEMPTS
                and self._reconnect_attempt >= self.RECONNECT_MAX_ATTEMPTS):
            self.log("Reconnect attempts exhausted")
            return
        delay = self.RECONNECT_BACKOFF_MS[
            min(self._reconnect_attempt, len(self.RECONNECT_BACKOFF_MS) - 1)]
        self._reconnect_attempt += 1
        self.log(f"Reconnect attempt {self._reconnect_attempt} in {delay} ms")
        self._reconnect_job = self.after(delay, self._do_reconnect)

    def _do_reconnect(self):
        self._reconnect_job = None
        if self.connected or self._closing:
            return
        if self._connect_in_flight:
            self._schedule_reconnect()
            return
        self._manual_connect()

    # ==================================================================
    # Public MCP tool API
    # ==================================================================

    def _fetch_tool(self, name, arguments):
        return self._unwrap_tool_text(
            self.mcp_call_tool(name, arguments), name)

    def chart_open(self, symbol, period):
        return self._fetch_tool("chart_open",
                                {"symbol": symbol, "period": period})

    def chart_close(self, chart_id):
        return self._fetch_tool("chart_close", {"chart_id": chart_id})

    def list_open_charts(self):
        return self._fetch_tool("list_open_charts", {})

    def get_chart_history(self, symbol, timeframe, datetime_from, datetime_to,
                          limit=None):
        if limit is None:
            limit = self.MCP_TOOL_LIMIT
        payload = self._fetch_tool("get_chart_history", {
            "symbol": symbol, "period": timeframe,
            "datetime_from": datetime_from, "datetime_to": datetime_to,
            "limit": limit,
        })
        if not payload.get("ok", False):
            return []
        return payload.get("history", []) or []

    def get_rates(self, symbol, timeframe, count):
        seconds = self.BAR_SECONDS.get(timeframe)
        if seconds is None:
            raise ValueError(f"Unknown timeframe: {timeframe}")
        want = int(count)
        span = int(seconds * want * self.RANGE_BUFFER)
        dt_to = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
        dt_from = dt_to - timedelta(seconds=span)
        history = self.get_chart_history(
            symbol, timeframe, dt_from.isoformat(), dt_to.isoformat())
        return history[-want:] if len(history) > want else history

    def get_chart_ticks_history(self, symbol, datetime_from, datetime_to,
                                limit=100000):
        return self._fetch_tool("get_chart_ticks_history", {
            "symbol": symbol,
            "datetime_from": datetime_from, "datetime_to": datetime_to,
            "limit": limit,
        })

    def get_market_watch_symbols(self):
        payload = self._fetch_tool("get_marketwatch_symbols", {})
        records = payload.get("symbols", []) or []
        blacklist = self._parse_blacklist()
        out = {str(r.get("symbol")) for r in records
               if isinstance(r, dict) and r.get("symbol")}
        if blacklist:
            out -= blacklist
        return sorted(out)

    def get_all_symbols(self):
        payload = self._fetch_tool("get_marketwatch_symbols",
                                   {"include_hidden": True, "limit": 100000})
        records = payload.get("symbols", []) or []
        out = {str(r.get("symbol")) for r in records
               if isinstance(r, dict) and r.get("symbol")}
        return sorted(out)

    def get_symbol_info(self, symbol):
        payload = self._fetch_tool("get_marketwatch_symbols",
                                   {"symbol": symbol})
        records = payload.get("symbols", []) or []
        return records[0] if records else {}

    def get_current_price(self, symbol):
        rec = self.get_symbol_info(symbol)
        if not rec:
            return {}
        return {
            "symbol": rec.get("symbol", symbol),
            "bid": rec.get("bid"),
            "ask": rec.get("ask"),
            "last": rec.get("price_close"),
            "time": rec.get("update_time"),
        }

    def add_marketwatch_symbol(self, symbol, show=True):
        return self._fetch_tool("add_marketwatch_symbol",
                                {"symbol": symbol, "show": show})

    def remove_marketwatch_symbol(self, symbol):
        return self._fetch_tool("remove_marketwatch_symbol",
                                {"symbol": symbol})

    def get_account_info(self):
        return self._fetch_tool("get_trading_account_info", {})

    def get_account_balance(self):
        return float(self.get_account_info()["balance"])

    def get_account_equity(self):
        return float(self.get_account_info()["equity"])

    def get_account_margin(self):
        return float(self.get_account_info()["margin"])

    def get_account_free_margin(self):
        return float(self.get_account_info()["free_margin"])

    def get_open_positions_and_orders(self, symbol=""):
        args = {"include_orders": True}
        if symbol:
            args["symbol"] = symbol
        return self._fetch_tool("get_trading_open_positions", args)

    def get_positions(self, symbol=""):
        return self.get_open_positions_and_orders(symbol).get(
            "positions", []) or []

    def get_orders(self, symbol=""):
        return self.get_open_positions_and_orders(symbol).get(
            "orders", []) or []

    def get_trading_history_positions(self, datetime_from, datetime_to,
                                      symbol=None, limit=100000):
        args = {"datetime_from": datetime_from, "datetime_to": datetime_to,
                "limit": limit}
        if symbol:
            args["symbol"] = symbol
        return self._fetch_tool("get_trading_history_positions", args)

    def get_trading_history_orders(self, datetime_from, datetime_to,
                                   symbol=None, include_orders=True,
                                   include_orders_canceled=False,
                                   include_deals=True, limit=100000):
        args = {"datetime_from": datetime_from, "datetime_to": datetime_to,
                "include_orders": include_orders,
                "include_orders_canceled": include_orders_canceled,
                "include_deals": include_deals, "limit": limit}
        if symbol:
            args["symbol"] = symbol
        return self._fetch_tool("get_trading_history_orders", args)

    def trade_send_market_order(self, symbol, side, volume,
                                sl=None, tp=None, comment=""):
        if side.upper() not in ("BUY", "SELL"):
            raise ValueError(f"Invalid market side: {side}")
        args = {"symbol": symbol, "type": side.lower(),
                "volume": float(volume)}
        if sl is not None:
            args["sl"] = float(sl)
        if tp is not None:
            args["tp"] = float(tp)
        if comment:
            args["comment"] = str(comment)[:31]
        return self._fetch_tool("trade_send_market_order", args)

    def trade_send_pending_order(self, symbol, order_type, volume, price,
                                 sl=None, tp=None, stoplimit=None,
                                 filling_type=None, expiration_type="gtc",
                                 expiration_time=None, comment=""):
        valid = {"buy_limit", "sell_limit", "buy_stop", "sell_stop",
                 "buy_stop_limit", "sell_stop_limit"}
        if order_type not in valid:
            raise ValueError(f"Invalid pending type: {order_type}")
        args = {"symbol": symbol, "type": order_type,
                "volume": float(volume), "price": float(price)}
        if stoplimit is not None:
            args["stoplimit"] = float(stoplimit)
        if sl is not None:
            args["sl"] = float(sl)
        if tp is not None:
            args["tp"] = float(tp)
        if filling_type:
            args["filling_type"] = filling_type
        if expiration_type:
            args["expiration_type"] = expiration_type
        if expiration_time:
            args["expiration_time"] = expiration_time
        if comment:
            args["comment"] = str(comment)[:31]
        return self._fetch_tool("trade_send_pending_order", args)

    def trade_modify_sl_tp(self, symbol, sl=None, tp=None,
                           position_ticket=None, order_ticket=None):
        if (position_ticket is None) == (order_ticket is None):
            raise ValueError(
                "trade_modify_sl_tp: provide exactly one of "
                "position_ticket or order_ticket")
        args = {"symbol": symbol}
        if position_ticket is not None:
            args["position_ticket"] = float(position_ticket)
        if order_ticket is not None:
            args["order_ticket"] = float(order_ticket)
        if sl is not None:
            args["sl"] = float(sl)
        if tp is not None:
            args["tp"] = float(tp)
        return self._fetch_tool("trade_modify_sl_tp", args)

    def trade_delete_order(self, symbol, order_ticket):
        return self._fetch_tool("trade_delete_order",
                                {"symbol": symbol,
                                 "order_ticket": float(order_ticket)})

    def trade_close_single_position(self, symbol, position_ticket):
        return self._fetch_tool("trade_close_single_position",
                                {"symbol": symbol,
                                 "position_ticket": float(position_ticket)})

    def trade_close_by_position(self, symbol, position_ticket,
                                position_ticket_by):
        return self._fetch_tool("trade_close_by_position", {
            "symbol": symbol,
            "position_ticket": float(position_ticket),
            "position_ticket_by": float(position_ticket_by),
        })

    def get_time_information(self):
        return self._fetch_tool("get_time_information", {})

    def economic_calendar_list_values(self, datetime_from, datetime_to,
                                      event_id=None, limit=1000):
        args = {"datetime_from": datetime_from,
                "datetime_to": datetime_to, "limit": limit}
        if event_id:
            args["event_id"] = str(event_id)
        return self._fetch_tool("economic_calendar_list_values", args)

    def economic_calendar_list_values_last(self, change_id, event_id=None):
        args = {"change_id": str(change_id)}
        if event_id:
            args["event_id"] = str(event_id)
        return self._fetch_tool("economic_calendar_list_values_last", args)

    def get_terminal_journal(self, datetime_from, datetime_to,
                             filter_text=None, limit=None):
        args = {"datetime_from": datetime_from, "datetime_to": datetime_to}
        if filter_text:
            args["filter"] = filter_text
        if limit is not None:
            args["limit"] = int(limit)
        return self._fetch_tool("get_terminal_journal", args)

    def get_expert_journal(self, datetime_from, datetime_to,
                           mql5_program_name=None, filter_text=None,
                           limit=None):
        args = {"datetime_from": datetime_from, "datetime_to": datetime_to}
        if mql5_program_name:
            args["mql5_program_name"] = mql5_program_name
        if filter_text:
            args["filter"] = filter_text
        if limit is not None:
            args["limit"] = int(limit)
        return self._fetch_tool("get_expert_journal", args)

    def get_tester_journal(self, datetime_from, datetime_to,
                           filter_text=None, limit=None):
        args = {"datetime_from": datetime_from, "datetime_to": datetime_to}
        if filter_text:
            args["filter"] = filter_text
        if limit is not None:
            args["limit"] = int(limit)
        return self._fetch_tool("get_tester_journal", args)


if __name__ == "__main__":
    root = ctk.CTk()
    root.title("MT5 MCP Client")
    root.geometry("640x1020")
    root.resizable(False, False)
    app = Mt5McpClient(root)
    app.pack(fill="both", expand=True)
    root.mainloop()