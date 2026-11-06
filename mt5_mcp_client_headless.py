#!/usr/bin/env python3
# mt5_mcp_client_headless.py
# For MT5 native MCP server only
# Copyright Su Nie | BSD-3C License | https://github.com/can87683

"""
Pip requirements: requests
"""

import base64
import configparser
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

import requests


class Mt5McpClientHeadless:
    """Self-managing headless MCP client. No GUI, no external lifecycle code."""

    # ------------------------------------------------------------------
    # Hardcoded defaults (override via <scriptname>.ini or constructor arg)
    # ------------------------------------------------------------------
    DEFAULT_SERVER_IP           = "127.0.0.1"
    DEFAULT_PORT                = "22346"
    DEFAULT_ENDPOINT            = "/mcp"
    DEFAULT_TOKEN               = ""
    DEFAULT_BLACKLIST           = ""
    DEFAULT_AUTO_CONNECT        = True
    DEFAULT_BLACKLIST_ENABLED   = True

    SCHEME                      = "http"
    PROTOCOL_VERSION            = "2024-11-05"
    CLIENT_NAME                 = "mt5-mcp-client-headless"
    CLIENT_VERSION              = "1.0"

    HTTP_TIMEOUT                = 30.0

    # Auto-connect loop
    AUTO_CONNECT_INITIAL_DELAY_SEC  = 0.5
    AUTO_CONNECT_RETRY_INTERVAL_SEC = 5.0
    AUTO_CONNECT_WAIT_SEC           = 15.0   # how long a tool call waits for it

    # Keep-alive loop
    KEEP_ALIVE_INTERVAL_SEC     = 15.0
    KEEP_ALIVE_TIMEOUT_SEC      = 5.0
    KEEP_ALIVE_MAX_FAILURES     = 3
    KEEP_ALIVE_RETRY_SEC        = 2.0

    # Reconnect backoff
    RECONNECT_BACKOFF_SEC       = (2.0, 5.0, 10.0, 30.0)

    # Bar/timeframe helpers
    BAR_SECONDS = {
        "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300,
        "M6": 360, "M10": 600, "M12": 720, "M15": 900, "M20": 1200,
        "M30": 1800, "H1": 3600, "H2": 7200, "H3": 10800,
        "H4": 14400, "H6": 21600, "H8": 28800, "H12": 43200,
        "D1": 86400, "W1": 604800, "MN1": 2592000,
    }
    RANGE_BUFFER                = 2.0

    AB_TIMEFRAMES               = ["M5", "M15", "H1", "H4", "D1", "W1"]
    AB_WANT                     = 10000
    AB_MAX_LOOKBACK_DAYS        = 365 * 50

    _TOKEN_SALT                 = b"mcp_client_v1_token_obfuscation"

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config_path=None, log_callback=None):
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

        self.log_callback = log_callback

        # Connection state (private — consumers must not touch these).
        self.scheme = self.SCHEME
        self.server_ip = self.DEFAULT_SERVER_IP
        self.port = self.DEFAULT_PORT
        self.endpoint = self.DEFAULT_ENDPOINT
        self.token = self.DEFAULT_TOKEN
        self.auto_connect = self.DEFAULT_AUTO_CONNECT
        self.blacklist_enabled = self.DEFAULT_BLACKLIST_ENABLED
        self.blacklist = self.DEFAULT_BLACKLIST

        self.connected = False
        self.protocol_version = None
        self.server_info = None
        self.session_id = None

        self._auto_connect_job = None
        self._keep_alive_job = None
        self._reconnect_job = None
        self._reconnect_attempt = 0
        self._keep_alive_failures = 0
        self._connect_in_flight = False
        self._connect_lock = threading.Lock()
        self._closing = False

        self._load_state()

        # Auto-connect is always on unless explicitly disabled in the .ini.
        if self.auto_connect:
            self._schedule_auto_connect(self.AUTO_CONNECT_INITIAL_DELAY_SEC)

    # ------------------------------------------------------------------
    # Token obfuscation
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self):
        if not self.ini.has_section("mcp"):
            self.ini.add_section("mcp")

        self.server_ip = self.ini.get(
            "mcp", "server_ip", fallback=self.DEFAULT_SERVER_IP)
        self.port = self.ini.get(
            "mcp", "port", fallback=self.DEFAULT_PORT)
        self.endpoint = self.ini.get(
            "mcp", "endpoint", fallback=self.DEFAULT_ENDPOINT)
        self.token = self.decrypt_token(
            self.ini.get("mcp", "token", fallback=self.DEFAULT_TOKEN))
        self.auto_connect = self.ini.getboolean(
            "mcp", "auto_connect", fallback=self.DEFAULT_AUTO_CONNECT)
        self.blacklist_enabled = self.ini.getboolean(
            "mcp", "blacklist_enabled", fallback=self.DEFAULT_BLACKLIST_ENABLED)
        self.blacklist = self.ini.get(
            "mcp", "blacklist", fallback=self.DEFAULT_BLACKLIST)

    def _atomic_write_ini(self):
        disk = configparser.ConfigParser()
        if os.path.exists(self.ini_path):
            disk.read(self.ini_path, encoding="utf-8")
        if not disk.has_section("mcp"):
            disk.add_section("mcp")

        allowed = {
            "server_ip", "port", "endpoint", "token",
            "auto_connect", "blacklist_enabled", "blacklist",
        }
        for key in list(disk.options("mcp")):
            if key not in allowed:
                disk.remove_option("mcp", key)
        for key in allowed:
            if self.ini.has_option("mcp", key):
                disk.set("mcp", key, self.ini.get("mcp", key))

        ordered = configparser.ConfigParser()
        ordered.optionxform = str
        ordered.add_section("mcp")
        for k in ("server_ip", "port", "endpoint", "token",
                  "auto_connect", "blacklist_enabled", "blacklist"):
            if disk.has_option("mcp", k):
                ordered.set("mcp", k, disk.get("mcp", k))
        for sect in disk.sections():
            if sect == "mcp":
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
        self.ini.set("mcp", "server_ip", self.server_ip)
        self.ini.set("mcp", "port", self.port)
        self.ini.set("mcp", "endpoint", self.endpoint)
        self.ini.set("mcp", "token", self.encrypt_token(self.token))
        self.ini.set("mcp", "auto_connect", str(bool(self.auto_connect)))
        self.ini.set("mcp", "blacklist_enabled", str(bool(self.blacklist_enabled)))
        self.ini.set("mcp", "blacklist", self.blacklist or "")
        self._atomic_write_ini()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, message):
        if self._closing or self.log_callback is None:
            return
        try:
            self.log_callback(message)
        except Exception:
            pass

    def _parse_blacklist(self):
        if not self.blacklist_enabled:
            return set()
        raw = self.blacklist or ""
        return {s.strip() for s in raw.split(",") if s.strip()}

    # ------------------------------------------------------------------
    # Lifecycle — entirely internal. Consumers call shutdown() only at exit.
    # ------------------------------------------------------------------

    def shutdown(self):
        """Stop background loops and persist state. Call once at process exit."""
        self._closing = True
        self._stop_keep_alive()
        for attr in ("_auto_connect_job", "_reconnect_job"):
            job = getattr(self, attr, None)
            if job:
                job.cancel()
                setattr(self, attr, None)
        self.connected = False
        self.session_id = None
        try:
            self.save_state()
        except Exception as e:
            self.log(f"[MCP] save_state failed during shutdown: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.shutdown()
        return False

    def _schedule_auto_connect(self, delay):
        if self._auto_connect_job:
            self._auto_connect_job.cancel()
        self._auto_connect_job = threading.Timer(delay, self._auto_connect_tick)
        self._auto_connect_job.daemon = True
        self._auto_connect_job.start()

    def _auto_connect_tick(self):
        self._auto_connect_job = None
        if self._closing:
            return
        if self.connected:
            return
        if self._reconnect_job is not None:
            # Reconnect loop owns the retry cadence; don't compete with it.
            return
        try:
            self._connect()
        except Exception as e:
            self.log(f"[MCP] auto-connect failed: {e}")
        if not self.connected and not self._closing and self.auto_connect:
            self._schedule_auto_connect(self.AUTO_CONNECT_RETRY_INTERVAL_SEC)

    def _wait_for_connect(self, timeout):
        """Block up to `timeout` seconds for the background auto-connect."""
        if self.connected:
            return True
        deadline = time.monotonic() + timeout
        while not self.connected and time.monotonic() < deadline:
            if self._closing:
                return False
            time.sleep(0.05)
        return self.connected

    # ------------------------------------------------------------------
    # MCP transport (private)
    # ------------------------------------------------------------------

    def _mcp_headers(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _mcp_raw_post(self, payload, timeout=None):
        url = f"{self.scheme}://{self.server_ip}:{self.port}{self.endpoint}"
        return requests.post(
            url, json=payload, headers=self._mcp_headers(),
            timeout=timeout if timeout is not None else self.HTTP_TIMEOUT)

    def _mcp_parse_response(self, response, request_id=None):
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

    def _connect(self):
        with self._connect_lock:
            if self.connected:
                return True
            init_payload = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": self.CLIENT_NAME,
                                   "version": self.CLIENT_VERSION},
                },
            }
            r = self._mcp_raw_post(
                init_payload, timeout=self.KEEP_ALIVE_TIMEOUT_SEC)
            if r.status_code != 200:
                self.log(f"[MCP] initialize failed: HTTP {r.status_code}")
                return False
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            body = self._mcp_parse_response(r, request_id=1)
            if not body or "result" not in body:
                self.log("[MCP] initialize returned no result")
                return False
            result = body["result"]
            self.protocol_version = result.get("protocolVersion")
            self.server_info = result.get("serverInfo", {})
            self._mcp_raw_post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                timeout=self.KEEP_ALIVE_TIMEOUT_SEC)
            self.connected = True
            self._keep_alive_failures = 0
            self._reconnect_attempt = 0
            self._start_keep_alive()
            self.log(f"[MCP] connected to {self.scheme}://"
                     f"{self.server_ip}:{self.port}{self.endpoint}")
            return True

    def _rpc(self, method, params=None):
        # Transparent connect: wait for the background auto-connect if needed.
        if not self.connected:
            if not self._wait_for_connect(self.AUTO_CONNECT_WAIT_SEC):
                raise RuntimeError(
                    f"MCP not connected — auto-connect did not succeed "
                    f"within {self.AUTO_CONNECT_WAIT_SEC:.0f}s "
                    f"({self.scheme}://{self.server_ip}:{self.port}{self.endpoint})")
        req_id = int(datetime.now().timestamp() * 1000)
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method,
                   "params": params or {}}
        r = self._mcp_raw_post(payload)
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

    # ------------------------------------------------------------------
    # Keep-alive / reconnect (private)
    # ------------------------------------------------------------------

    def _start_keep_alive(self):
        self._stop_keep_alive()
        self._keep_alive_failures = 0
        self._keep_alive_job = threading.Timer(
            self.KEEP_ALIVE_INTERVAL_SEC, self._keep_alive_check)
        self._keep_alive_job.daemon = True
        self._keep_alive_job.start()

    def _stop_keep_alive(self):
        if self._keep_alive_job:
            self._keep_alive_job.cancel()
            self._keep_alive_job = None

    def _keep_alive_check(self):
        self._keep_alive_job = None
        if self._closing or not self.connected:
            return
        ok = False
        try:
            resp = self._mcp_raw_post(
                {"jsonrpc": "2.0", "id": int(time.time() * 1000),
                 "method": "ping"},
                timeout=self.KEEP_ALIVE_TIMEOUT_SEC)
            if resp.status_code == 200:
                ok = True
        except Exception as e:
            self.log(f"[MCP] keep-alive probe failed: {e}")
        self._on_keep_alive_result(ok)

    def _on_keep_alive_result(self, ok):
        if not self.connected or self._closing:
            return
        if ok:
            self._keep_alive_failures = 0
            self._reconnect_attempt = 0
            self._keep_alive_job = threading.Timer(
                self.KEEP_ALIVE_INTERVAL_SEC, self._keep_alive_check)
            self._keep_alive_job.daemon = True
            self._keep_alive_job.start()
            return
        self._keep_alive_failures += 1
        self.log(f"[MCP] keep-alive failure "
                 f"{self._keep_alive_failures}/{self.KEEP_ALIVE_MAX_FAILURES}")
        if self._keep_alive_failures >= self.KEEP_ALIVE_MAX_FAILURES:
            self.log("[MCP] connection declared dead — reconnecting")
            self._stop_keep_alive()
            self.connected = False
            self.session_id = None
            self._schedule_reconnect()
            return
        self._keep_alive_job = threading.Timer(
            self.KEEP_ALIVE_RETRY_SEC, self._keep_alive_check)
        self._keep_alive_job.daemon = True
        self._keep_alive_job.start()

    def _schedule_reconnect(self):
        if self._closing or self._reconnect_job:
            return
        if not self.auto_connect:
            return
        idx = min(self._reconnect_attempt, len(self.RECONNECT_BACKOFF_SEC) - 1)
        delay = self.RECONNECT_BACKOFF_SEC[idx]
        self._reconnect_attempt += 1
        self.log(f"[MCP] reconnect attempt {self._reconnect_attempt} "
                 f"in {delay:.0f}s")
        self._reconnect_job = threading.Timer(delay, self._do_reconnect)
        self._reconnect_job.daemon = True
        self._reconnect_job.start()

    def _do_reconnect(self):
        self._reconnect_job = None
        if self._closing or self.connected:
            return
        try:
            self._connect()
        except Exception as e:
            self.log(f"[MCP] reconnect failed: {e}")
        if not self.connected and not self._closing:
            self._schedule_reconnect()

    # ==================================================================
    # Public MCP tool API — the ONLY surface consumers should use.
    # ==================================================================

    def _fetch_tool(self, name, arguments):
        return self._unwrap_tool_text(self._rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}), name)

    @staticmethod
    def _unwrap_tool_text(envelope, label):
        if envelope is None:
            raise RuntimeError(f"{label}: empty response")
        if envelope.get("isError"):
            try:
                text = envelope["content"][0]["text"]
            except Exception:
                text = repr(envelope)
            raise RuntimeError(f"{label}: MCP tool error: {text}")
        try:
            text = envelope["content"][0]["text"]
        except Exception:
            raise RuntimeError(f"{label}: malformed tool payload: {envelope!r}")
        if not text:
            raise RuntimeError(f"{label}: empty tool payload")
        return json.loads(text)

    # ---- charts / history ------------------------------------------------

    def chart_open(self, symbol, period):
        return self._fetch_tool("chart_open",
                                {"symbol": symbol, "period": period})

    def chart_close(self, chart_id):
        return self._fetch_tool("chart_close", {"chart_id": chart_id})

    def list_open_charts(self):
        return self._fetch_tool("list_open_charts", {})

    def get_chart_history(self, symbol, timeframe, datetime_from, datetime_to,
                          limit=100000):
        payload = self._fetch_tool("get_chart_history", {
            "symbol": symbol, "period": timeframe,
            "datetime_from": datetime_from, "datetime_to": datetime_to,
            "limit": limit,
        })
        if not payload.get("ok", False):
            raise RuntimeError(
                f"get_chart_history({symbol},{timeframe}): ok=false")
        return payload.get("history", []) or []

    def get_rates(self, symbol, timeframe, count):
        seconds = self.BAR_SECONDS.get(timeframe)
        if seconds is None:
            raise ValueError(f"Unknown timeframe: {timeframe}")
        want = int(count)
        span = int(seconds * want * self.RANGE_BUFFER)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        dt_from = now - timedelta(seconds=span)
        dt_to = now + timedelta(days=1)

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

    # ---- symbols ---------------------------------------------------------

    def get_market_watch_symbols(self):
        payload = self._fetch_tool("get_marketwatch_symbols", {})
        records = payload.get("symbols", []) or []
        out = {str(r.get("symbol")) for r in records
               if isinstance(r, dict) and r.get("symbol")}
        out -= self._parse_blacklist()
        return sorted(out)

    def get_all_symbols(self):
        payload = self._fetch_tool("get_marketwatch_symbols",
                                   {"include_hidden": True, "limit": 100000})
        records = payload.get("symbols", []) or []
        return sorted({str(r.get("symbol")) for r in records
                       if isinstance(r, dict) and r.get("symbol")})

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

    # ---- account ---------------------------------------------------------

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

    # ---- open positions / orders ----------------------------------------

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

    # ---- history ---------------------------------------------------------

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

    # ---- trading ---------------------------------------------------------

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

    # ---- time / calendar / journals -------------------------------------

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
    client = Mt5McpClientHeadless(
        log_callback=lambda m: print(
            f"[{datetime.now():%H:%M:%S}] {m}", flush=True))
    print(f"[MCP] config: {client.ini_path}")
    print("[MCP] auto-connect active; press Ctrl-C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.shutdown()