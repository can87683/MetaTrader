#!/usr/bin/env python3
# mt5_connector.py
# Copyright Su Nie | BSD-C-3 License | https://github.com/can87683

import setproctitle
setproctitle.setproctitle("mt5_connector.py")

import os
import sys
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import MetaTrader5 as mt5
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import threading
import configparser
from pathlib import Path

# ==================== CENTRALIZED CONFIGURATION ====================
TIMEFRAMES = ["M5", "M15", "H1", "H4", "D1", "W1"]
TIMEFRAME_ORDER = ["M5", "M15", "H1", "H4", "D1", "W1"]
DEFAULT_MAX_BARS = 10000
DEFAULT_DATA_DIR = os.path.join(str(Path.home()), "mt5_data")


class ConfigCtk:
    """Centralized CTk theme and style configuration."""
    WIN_W = 640
    WIN_H = 800
    HEADER_HEIGHT = 52

    FONT_H = ("Roboto", 12, "bold")
    FONT_S = ("Roboto", 12)
    FONT_MONO = ("Consolas", 12)

    APPEARANCE_MODE = "dark"
    COLOR_THEME = "dark-blue"

    BG0 = "#0f1117"
    BG1 = "#181c27"
    BG2 = "#21263a"
    BG3 = "#2c3350"
    ACCENT = "#f5a623"
    ACCENT2 = "#e07b10"
    FG = "#d4d8e8"
    FG_DIM = "#6b7499"
    GREEN = "#3ecf8e"
    RED = "#ff4d6d"
    YELLOW = "#ffd166"
    DARK_BLUE = "#1e3a8a"


class MT5API:
    """Direct MetaTrader5 API implementation."""

    TIMEFRAMES = {
        'M5': mt5.TIMEFRAME_M5, 'M15': mt5.TIMEFRAME_M15, 'H1': mt5.TIMEFRAME_H1,
        'H4': mt5.TIMEFRAME_H4, 'D1': mt5.TIMEFRAME_D1, 'W1': mt5.TIMEFRAME_W1,
    }

    ORDER_TYPES = {
        'BUY': mt5.ORDER_TYPE_BUY, 'SELL': mt5.ORDER_TYPE_SELL,
        'BUY_LIMIT': mt5.ORDER_TYPE_BUY_LIMIT, 'SELL_LIMIT': mt5.ORDER_TYPE_SELL_LIMIT,
        'BUY_STOP': mt5.ORDER_TYPE_BUY_STOP, 'SELL_STOP': mt5.ORDER_TYPE_SELL_STOP,
        'BUY_STOP_LIMIT': mt5.ORDER_TYPE_BUY_STOP_LIMIT, 'SELL_STOP_LIMIT': mt5.ORDER_TYPE_SELL_STOP_LIMIT,
    }

    ORDER_FILLING = {'IOC': mt5.ORDER_FILLING_IOC, 'FOK': mt5.ORDER_FILLING_FOK, 'RETURN': mt5.ORDER_FILLING_RETURN}
    ORDER_TIME = {'GTC': mt5.ORDER_TIME_GTC, 'DAY': mt5.ORDER_TIME_DAY, 'SPECIFIED': mt5.ORDER_TIME_SPECIFIED, 'SPECIFIED_DAY': mt5.ORDER_TIME_SPECIFIED_DAY}

    def __init__(self, path: str = None, timeout: int = 60000, portable: bool = False):
        self.is_connected = False
        self.path = path
        self.timeout = timeout
        self.portable = portable
        self.account_info = None
        self.last_error_msg = None
        self.last_error_code = None

    def initialize(self, path: str = None, timeout: int = None, portable: bool = None) -> bool:
        path = path or self.path
        timeout = timeout or self.timeout
        portable = portable or self.portable

        mt5.shutdown()
        time.sleep(0.5)

        initialized = mt5.initialize(path=path, timeout=timeout, portable=portable) if path else mt5.initialize()
        if not initialized:
            self.last_error_code, self.last_error_msg = mt5.last_error()
            print(f"❌ MT5 initialization failed: {self.last_error_msg}")
            return False

        self.account_info = mt5.account_info()
        self.is_connected = True
        if self.account_info:
            print(f"✅ MT5 connected to {self.account_info.server} (Account: {self.account_info.login})")
        return True

    def shutdown(self) -> None:
        mt5.shutdown()
        self.is_connected = False
        self.account_info = None
        print("✅ MT5 shutdown")

    def version(self) -> Tuple[str, str, str]:
        if not self.is_connected: return ("", "", "")
        v = mt5.version()
        return (v[0], v[1], v[2]) if v else ("", "", "")

    def get_account_info(self) -> Optional[Dict]:
        info = mt5.account_info() if self.is_connected else None
        return info._asdict() if info else None

    def get_account_balance(self) -> float:
        info = self.get_account_info()
        return info.get('balance', 0.0) if info else 0.0

    def get_account_equity(self) -> float:
        info = self.get_account_info()
        return info.get('equity', 0.0) if info else 0.0

    def get_account_margin(self) -> float:
        info = self.get_account_info()
        return info.get('margin', 0.0) if info else 0.0

    def get_free_margin(self) -> float:
        info = self.get_account_info()
        return info.get('margin_free', 0.0) if info else 0.0

    def get_margin_level(self) -> float:
        info = self.get_account_info()
        return info.get('margin_level', 0.0) if info else 0.0

    def get_leverage(self) -> int:
        info = self.get_account_info()
        return info.get('leverage', 0) if info else 0

    def terminal_info(self) -> Optional[Dict]:
        info = mt5.terminal_info() if self.is_connected else None
        return info._asdict() if info else None

    def get_server_time(self) -> Optional[datetime]:
        if not self.is_connected: return None
        for symbol in ['EURUSD', 'XAUUSD', 'BTCUSD']:
            tick = mt5.symbol_info_tick(symbol)
            if tick and tick.time > 0:
                return datetime.fromtimestamp(tick.time)
        return None

    def _clean_symbol_list(self, symbols):
        if not symbols: return []
        return sorted({str(s).strip() for s in symbols if s and not str(s).strip().startswith('#')})

    def get_market_watch_symbols(self) -> List[str]:
        if not self.is_connected: return []
        symbols = mt5.symbols_get()
        return self._clean_symbol_list([s.name for s in symbols if s.visible]) if symbols else []

    def get_all_symbols(self) -> List[str]:
        if not self.is_connected: return []
        symbols = mt5.symbols_get()
        return self._clean_symbol_list([s.name for s in symbols]) if symbols else []

    def get_available_symbols(self) -> List[str]:
        return [s for s in self.get_all_symbols() if self.get_symbol_info(s) and self.get_symbol_info(s).get('trade_mode', 0) > 0]

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        info = mt5.symbol_info(symbol) if self.is_connected else None
        return info._asdict() if info else None

    def get_symbol_info_tick(self, symbol: str) -> Optional[Dict]:
        if not self.is_connected: return None
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        return tick._asdict() if tick else None

    def get_current_price(self, symbol: str) -> Dict[str, Any]:
        return self.get_symbol_info_tick(symbol) or {}

    def get_symbol_price(self, symbol: str, bid_ask: str = 'bid') -> Optional[float]:
        tick = self.get_current_price(symbol)
        return tick.get('bid' if bid_ask.lower() == 'bid' else 'ask') if tick else None

    def symbol_select(self, symbol: str, select: bool = True) -> bool:
        return mt5.symbol_select(symbol, select) if self.is_connected else False

    def get_symbols_total(self) -> int:
        return mt5.symbols_total() if self.is_connected else 0

    def get_symbol_lot_size(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        return info.get('volume_step', 0.01) if info else 0.01

    def get_spread(self, symbol: str) -> float:
        tick = self.get_current_price(symbol)
        info = self.get_symbol_info(symbol)
        if not tick or not info or info.get('point', 0) == 0: return 0.0
        return (tick.get('ask', 0) - tick.get('bid', 0)) / info.get('point', 0.00001)

    def is_market_open(self, symbol: str) -> bool:
        info = self.get_symbol_info(symbol)
        return info.get('trade_mode', 0) > 0 if info else False

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        tf = self.TIMEFRAMES.get(timeframe.upper(), mt5.TIMEFRAME_D1)
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None: return None
        return [{'time': datetime.fromtimestamp(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in rates]

    def get_rates_from_pos(self, symbol: str, timeframe: str, start_pos: int, count: int) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        tf = self.TIMEFRAMES.get(timeframe.upper(), mt5.TIMEFRAME_D1)
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
        if rates is None: return None
        return [{'time': datetime.fromtimestamp(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in rates]

    def get_rates_time(self, symbol: str, timeframe: str, start_date: datetime = None, end_date: datetime = None) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        tf = self.TIMEFRAMES.get(timeframe.upper(), mt5.TIMEFRAME_D1)
        mt5.symbol_select(symbol, True)
        if start_date and end_date:
            rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
        elif start_date:
            rates = mt5.copy_rates_from(symbol, tf, start_date, 1000)
        else:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, 100)
        if rates is None: return None
        return [{'time': datetime.fromtimestamp(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in rates]

    def get_rates_from(self, symbol: str, timeframe: str, date_from: datetime, count: int) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        tf = self.TIMEFRAMES.get(timeframe.upper(), mt5.TIMEFRAME_D1)
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from(symbol, tf, date_from, count)
        if rates is None: return None
        return [{'time': datetime.fromtimestamp(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in rates]

    def get_rates_range(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        tf = self.TIMEFRAMES.get(timeframe.upper(), mt5.TIMEFRAME_D1)
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
        if rates is None: return None
        return [{'time': datetime.fromtimestamp(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in rates]

    def get_ticks_from(self, symbol: str, date_from: datetime, count: int, flags: int = mt5.COPY_TICKS_ALL) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        mt5.symbol_select(symbol, True)
        ticks = mt5.copy_ticks_from(symbol, date_from, count, flags)
        if ticks is None: return None
        return [{'time': datetime.fromtimestamp(t[0] / 1000), 'bid': float(t[1]), 'ask': float(t[2]), 'last': float(t[3]), 'volume': float(t[4]), 'flags': int(t[5])} for t in ticks]

    def get_ticks_range(self, symbol: str, date_from: datetime, date_to: datetime, flags: int = mt5.COPY_TICKS_ALL) -> Optional[List[Dict]]:
        if not self.is_connected: return None
        mt5.symbol_select(symbol, True)
        ticks = mt5.copy_ticks_range(symbol, date_from, date_to, flags)
        if ticks is None: return None
        return [{'time': datetime.fromtimestamp(t[0] / 1000), 'bid': float(t[1]), 'ask': float(t[2]), 'last': float(t[3]), 'volume': float(t[4]), 'flags': int(t[5])} for t in ticks]

    def verify_symbol_timeframe(self, symbol: str, timeframe: str) -> dict:
        if not self.is_connected: return {"valid": False, "error": "Not connected", "bars": 0}
        rates = self.get_rates(symbol, timeframe, 1)
        return {"valid": True, "error": "", "bars": 1} if rates and len(rates) > 0 else {"valid": False, "error": "No data available", "bars": 0}

    def place_order(self, symbol: str, order_type: str, volume: float, price: float = 0.0, sl: float = 0.0, tp: float = 0.0, deviation: int = 10, magic: int = 0, comment: str = "", expiration: datetime = None, fill_type: str = 'IOC', time_type: str = 'GTC') -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}

        mt5.symbol_select(symbol, True)
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info: return {"status": "error", "message": f"Cannot get symbol info for {symbol}"}

        if order_type in ['BUY', 'SELL']:
            action = mt5.TRADE_ACTION_DEAL
            order_type_mt5 = mt5.ORDER_TYPE_BUY if order_type == 'BUY' else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(symbol)
            price = price or tick.ask if order_type == 'BUY' else tick.bid
        else:
            action = mt5.TRADE_ACTION_PENDING
            order_type_mt5 = self.ORDER_TYPES.get(order_type, mt5.ORDER_TYPE_BUY_LIMIT)
            if price == 0: return {"status": "error", "message": "Price must be specified for pending orders"}

        request = {
            "action": action, "symbol": symbol, "volume": float(volume), "type": order_type_mt5,
            "price": float(price), "sl": float(sl), "tp": float(tp), "deviation": deviation,
            "magic": magic, "comment": comment,
            "type_filling": self.ORDER_FILLING.get(fill_type, mt5.ORDER_FILLING_IOC),
            "type_time": self.ORDER_TIME.get(time_type, mt5.ORDER_TIME_GTC),
        }
        if expiration: request["expiration"] = int(expiration.timestamp())

        result = mt5.order_send(request)
        if not result: return {"status": "error", "message": "Order send failed (no result)"}
        if result.retcode != mt5.TRADE_RETCODE_DONE: return {"status": "error", "message": f"Order failed: code {result.retcode}"}
        return {"status": "success", "message": f"Order #{result.order} placed", "order": result.order}

    def send_trade(self, action_type: str, symbol: str, lot_size: float, stop_loss: float = 0.0, take_profit: float = 0.0) -> Tuple[bool, str]:
        result = self.place_order(symbol=symbol, order_type=action_type, volume=lot_size, sl=stop_loss, tp=take_profit)
        return result.get("status") == "success", result.get("message", "")

    def modify_order(self, order_ticket: int, price: float = None, sl: float = None, tp: float = None, expiration: datetime = None) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        orders = mt5.orders_get(ticket=order_ticket)
        if not orders: return {"status": "error", "message": f"Order #{order_ticket} not found"}

        order = orders[0]
        request = {
            "action": mt5.TRADE_ACTION_MODIFY, "order": order_ticket,
            "price": float(price) if price is not None else order.price_open,
            "sl": float(sl) if sl is not None else order.sl,
            "tp": float(tp) if tp is not None else order.tp,
            "type_time": order.type_time,
            "expiration": int(expiration.timestamp()) if expiration else 0
        }
        result = mt5.order_send(request)
        if not result: return {"status": "error", "message": "Modify order failed (no result)"}
        if result.retcode != mt5.TRADE_RETCODE_DONE: return {"status": "error", "message": f"Modify failed: code {result.retcode}"}
        return {"status": "success", "message": f"Order #{order_ticket} modified"}

    def delete_order(self, order_ticket: int) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": order_ticket}
        result = mt5.order_send(request)
        if not result: return {"status": "error", "message": "Delete order failed (no result)"}
        if result.retcode != mt5.TRADE_RETCODE_DONE: return {"status": "error", "message": f"Delete failed: code {result.retcode}"}
        return {"status": "success", "message": f"Order #{order_ticket} deleted"}

    def close_position(self, ticket: int, deviation: int = 10) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        positions = mt5.positions_get(ticket=ticket)
        if not positions: return {"status": "error", "message": f"Position #{ticket} not found"}

        position = positions[0]
        tick = mt5.symbol_info_tick(position.symbol)
        if not tick: return {"status": "error", "message": f"Cannot get tick for {position.symbol}"}

        close_type = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol, "volume": position.volume,
            "type": close_type, "position": position.ticket, "price": price, "deviation": deviation,
            "magic": position.magic, "comment": f"Close position #{ticket}",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if not result: return {"status": "error", "message": "Close order failed (no result)"}
        if result.retcode != mt5.TRADE_RETCODE_DONE: return {"status": "error", "message": f"Close failed: code {result.retcode}"}
        return {"status": "success", "message": f"Position #{ticket} closed at {result.price}"}

    def close_symbol_positions(self, symbol: str) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        positions = mt5.positions_get(symbol=symbol)
        if not positions: return {"status": "success", "message": f"No positions for {symbol}"}

        closed_count, errors = 0, []
        for position in positions:
            result = self.close_position(position.ticket)
            if result.get("status") == "success": closed_count += 1
            else: errors.append(f"#{position.ticket}: {result.get('message')}")

        if errors: return {"status": "error", "message": f"Closed {closed_count}/{len(positions)}. Errors: {'; '.join(errors)}"}
        return {"status": "success", "message": f"Closed {closed_count} positions for {symbol}"}

    def close_all_positions(self) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        positions = mt5.positions_get()
        if not positions: return {"status": "success", "message": "No positions to close"}

        closed_count, errors = 0, []
        for position in positions:
            result = self.close_position(position.ticket)
            if result.get("status") == "success": closed_count += 1
            else: errors.append(f"#{position.ticket}: {result.get('message')}")

        if errors: return {"status": "error", "message": f"Closed {closed_count}/{len(positions)}. Errors: {'; '.join(errors)}"}
        return {"status": "success", "message": f"Closed {closed_count} positions"}

    def modify_position(self, ticket: int, sl: float = None, tp: float = None) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected"}
        positions = mt5.positions_get(ticket=ticket)
        if not positions: return {"status": "error", "message": f"Position #{ticket} not found"}

        position = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol,
            "sl": float(sl) if sl is not None else position.sl,
            "tp": float(tp) if tp is not None else position.tp,
            "position": position.ticket
        }
        result = mt5.order_send(request)
        if not result: return {"status": "error", "message": "Modify position failed (no result)"}
        if result.retcode != mt5.TRADE_RETCODE_DONE: return {"status": "error", "message": f"Modify failed: code {result.retcode}"}
        return {"status": "success", "message": f"Position #{ticket} modified (SL: {sl}, TP: {tp})"}

    def calculate_position_size(self, symbol: str, risk_percent: float, stop_loss_pips: float) -> dict:
        if not self.is_connected: return {"status": "error", "message": "MT5 not connected", "volume": 0.0}

        account_info = self.get_account_info()
        if not account_info: return {"status": "error", "message": "Cannot get account info", "volume": 0.0}

        symbol_info = self.get_symbol_info(symbol)
        if not symbol_info: return {"status": "error", "message": f"Cannot get symbol info for {symbol}", "volume": 0.0}

        balance = account_info.get('balance', 0)
        risk_amount = balance * (risk_percent / 100)
        tick_value = symbol_info.get('trade_tick_value', 0)
        if tick_value == 0: return {"status": "error", "message": "Cannot determine tick value", "volume": 0.0}

        volume = risk_amount / (stop_loss_pips * tick_value)
        volume_step = symbol_info.get('volume_step', 0.01)
        volume = round(volume / volume_step) * volume_step
        volume = max(symbol_info.get('volume_min', 0.01), min(volume, symbol_info.get('volume_max', 100)))

        return {"status": "success", "message": f"Calculated volume: {volume}", "volume": volume, "risk_amount": risk_amount, "balance": balance, "risk_percent": risk_percent, "stop_loss_pips": stop_loss_pips}

    def get_positions(self, symbol: str = "") -> List[Dict]:
        if not self.is_connected: return []
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions is None: return []
        return [p._asdict() for p in positions]

    def get_orders(self, symbol: str = "") -> List[Dict]:
        if not self.is_connected: return []
        orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        if orders is None: return []
        return [o._asdict() for o in orders]

    def get_positions_total(self) -> int:
        return mt5.positions_total() if self.is_connected else 0

    def get_orders_total(self) -> int:
        return mt5.orders_total() if self.is_connected else 0

    def get_position_tickets(self, symbol: str = "") -> List[int]:
        return [p['ticket'] for p in self.get_positions(symbol=symbol)]

    def get_order_tickets(self, symbol: str = "") -> List[int]:
        return [o['ticket'] for o in self.get_orders(symbol=symbol)]

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        positions = self.get_positions(symbol=symbol)
        return positions[0] if positions else None

    def get_order_by_symbol(self, symbol: str) -> Optional[Dict]:
        orders = self.get_orders(symbol=symbol)
        return orders[0] if orders else None

    def get_total_profit(self) -> float:
        return sum(p.get('profit', 0) for p in self.get_positions())

    def get_history_orders(self, date_from: datetime = None, date_to: datetime = None, group: str = "", ticket: int = 0) -> List[Dict]:
        if not self.is_connected: return []
        if ticket > 0: orders = mt5.history_orders_get(ticket=ticket)
        elif date_from and date_to: orders = mt5.history_orders_get(date_from, date_to)
        elif group: orders = mt5.history_orders_get(group=group)
        else: orders = mt5.history_orders_get()
        if orders is None: return []
        return [o._asdict() for o in orders]

    def get_history_deals(self, date_from: datetime = None, date_to: datetime = None, group: str = "", ticket: int = 0) -> List[Dict]:
        if not self.is_connected: return []
        if ticket > 0: deals = mt5.history_deals_get(ticket=ticket)
        elif date_from and date_to: deals = mt5.history_deals_get(date_from, date_to)
        elif group: deals = mt5.history_deals_get(group=group)
        else: deals = mt5.history_deals_get()
        if deals is None: return []
        return [d._asdict() for d in deals]

    def get_history_orders_total(self, date_from: datetime, date_to: datetime) -> int:
        return mt5.history_orders_total(date_from, date_to) if self.is_connected else 0

    def get_history_deals_total(self, date_from: datetime, date_to: datetime) -> int:
        return mt5.history_deals_total(date_from, date_to) if self.is_connected else 0

    def get_last_error(self) -> Tuple[int, str]:
        self.last_error_code = mt5.last_error()
        self.last_error_msg = f"Error code: {self.last_error_code}"
        return self.last_error_code, self.last_error_msg

    def check_order_conditions(self, symbol: str, order_type: str, volume: float, price: float = 0.0) -> Tuple[bool, str]:
        if not self.is_connected: return False, "MT5 not connected"
        symbol_info = self.get_symbol_info(symbol)
        if not symbol_info: return False, f"Symbol {symbol} not found"
        if volume < symbol_info.get('volume_min', 0.01): return False, f"Volume too small (min: {symbol_info.get('volume_min', 0.01)})"
        if volume > symbol_info.get('volume_max', 100): return False, f"Volume too large (max: {symbol_info.get('volume_max', 100)})"
        if not symbol_info.get('trade_mode', False): return False, f"Symbol {symbol} is not tradeable"
        if order_type not in ['BUY', 'SELL'] and price == 0: return False, "Price must be specified for pending orders"
        return True, "Order conditions OK"

    def calculate_margin(self, symbol: str, order_type: str, volume: float, price: float = 0.0) -> float:
        if not self.is_connected: return 0.0
        if price == 0:
            tick = self.get_current_price(symbol)
            if not tick: return 0.0
            price = tick.get('ask') if order_type in ['BUY', 'BUY_LIMIT', 'BUY_STOP'] else tick.get('bid')

        order_type_mt5 = self.ORDER_TYPES.get(order_type, mt5.ORDER_TYPE_BUY)
        margin = mt5.order_calc_margin(order_type_mt5, symbol, volume, price)
        return margin if margin is not None else 0.0

    def calculate_profit(self, symbol: str, order_type: str, volume: float, open_price: float, close_price: float) -> float:
        if not self.is_connected: return 0.0
        order_type_mt5 = self.ORDER_TYPES.get(order_type, mt5.ORDER_TYPE_BUY)
        profit = mt5.order_calc_profit(order_type_mt5, symbol, volume, open_price, close_price)
        return profit if profit is not None else 0.0

    def send_batch_orders(self, orders: List[Dict]) -> List[dict]:
        results = []
        for order in orders:
            results.append(self.place_order(**order))
            time.sleep(0.1)
        return results

    def close_multiple_positions(self, tickets: List[int]) -> List[dict]:
        results = []
        for ticket in tickets:
            results.append(self.close_position(ticket))
            time.sleep(0.1)
        return results

    def get_trading_status(self) -> Dict[str, Any]:
        return {
            'connected': self.is_connected, 'account': self.get_account_info(), 'terminal': self.terminal_info(),
            'positions_total': self.get_positions_total(), 'orders_total': self.get_orders_total(),
            'balance': self.get_account_balance(), 'equity': self.get_account_equity(),
            'margin': self.get_account_margin(), 'free_margin': self.get_free_margin(),
            'margin_level': self.get_margin_level(), 'timestamp': datetime.now().isoformat()
        }

    def monitor_positions(self, callback: callable, interval: int = 5) -> None:
        def monitor():
            last_count = 0
            while self.is_connected:
                current_count = self.get_positions_total()
                if current_count != last_count:
                    callback(self.get_positions(), current_count)
                    last_count = current_count
                time.sleep(interval)
        threading.Thread(target=monitor, daemon=True).start()

    def export_positions_to_csv(self, filename: str) -> bool:
        positions = self.get_positions()
        if not positions: return False
        pd.DataFrame(positions).to_csv(filename, index=False)
        print(f"✅ Exported {len(positions)} positions to {filename}")
        return True

    def export_orders_to_csv(self, filename: str) -> bool:
        orders = self.get_orders()
        if not orders: return False
        pd.DataFrame(orders).to_csv(filename, index=False)
        print(f"✅ Exported {len(orders)} orders to {filename}")
        return True

    def export_history_to_csv(self, filename: str, days: int = 7) -> bool:
        date_from, date_to = datetime.now() - timedelta(days=days), datetime.now()
        deals, orders = self.get_history_deals(date_from, date_to), self.get_history_orders(date_from, date_to)
        if not deals and not orders: return False

        all_data = [{'type': 'deal', **d} for d in deals] + [{'type': 'order', **o} for o in orders]
        pd.DataFrame(all_data).to_csv(filename, index=False)
        print(f"✅ Exported {len(all_data)} history items to {filename}")
        return True

    def ping(self) -> dict:
        return {"status": "error", "message": "Not connected"} if not self.is_connected else {"status": "success", "message": "pong"}

    def test_connection(self) -> dict:
        return {"status": "error", "message": "Not connected"} if not self.is_connected else {"status": "success", "message": "Connection OK"}

    def heartbeat(self) -> dict:
        return {"status": "error", "message": "Not connected"} if not self.is_connected else {"status": "success", "message": "alive"}

    def get_available_methods(self) -> dict:
        return {"status": "success", "methods": [m for m in dir(self) if not m.startswith('_') and callable(getattr(self, m))]}

    def test_method(self, method_name: str = "") -> dict:
        if not self.is_connected: return {"status": "error", "message": "Not connected"}
        if not method_name: return {"status": "error", "message": "Method name required"}
        return {"status": "success", "message": f"Method '{method_name}' exists"} if hasattr(self, method_name) else {"status": "error", "message": f"Method '{method_name}' not found"}


class MT5APIEmbed:
    """Embeddable GUI for direct MT5 API integration using CustomTkinter."""

    def __init__(self, parent_frame, on_connect_callback=None, on_disconnect_callback=None, config_file="mt5_connector.ini"):
        self.parent = parent_frame
        self.config_file = config_file
        self.mt5_api = None
        self.connected = False
        self.auto_connect_var = ctk.BooleanVar(value=False)
        self.data_dir = DEFAULT_DATA_DIR

        self.current_rate_symbol = None
        self.rate_update_job = None
        self.rate_update_count = 0
        self.max_rate_updates = 5
        self.rate_update_interval = 1000

        self.on_connect_callback = on_connect_callback
        self.on_disconnect_callback = on_disconnect_callback
        self.saved_state = self._load_config()
        self._create_widgets()
        self._apply_config()

    def _create_widgets(self):
        self.api_frame = ctk.CTkFrame(self.parent, fg_color=ConfigCtk.BG1, corner_radius=0)
        self.api_frame.pack(fill="x", padx=1, pady=1)

        title_frame = ctk.CTkFrame(self.api_frame, fg_color="transparent")
        title_frame.pack(fill="x", padx=5, pady=5)

        ctk.CTkLabel(title_frame, text="Copyright Su Nie | BSD-C-3 License | https://github.com/can87683", font=("Ubuntu", 18), text_color="yellow").pack(side="top")

        ctk.CTkLabel(title_frame, text="MT5 Connector", font=("Roboto", 24, "bold"), text_color="white").pack(side="top")
        ctk.CTkLabel(title_frame, text="", font=("Ubuntu", 16), text_color="yellow").pack(side="top")

        self.path_frame = ctk.CTkFrame(self.api_frame, fg_color="transparent")
        self.path_frame.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(self.path_frame, text="Terminal Path:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.path_entry = ctk.CTkEntry(self.path_frame, width=250, font=("Roboto", 12), fg_color=ConfigCtk.BG2, text_color=ConfigCtk.FG, border_color=ConfigCtk.BG3, border_width=1)
        self.path_entry.pack(side="left", padx=(0, 5))
        self.path_browse_btn = ctk.CTkButton(self.path_frame, text="Browse", command=self._browse_path, width=70, font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.path_browse_btn.pack(side="left")

        self.data_frame = ctk.CTkFrame(self.api_frame, fg_color="transparent")
        self.data_frame.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(self.data_frame, text="MT5 Data:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.data_dir_label = ctk.CTkLabel(self.data_frame, text=os.path.basename(self.data_dir), fg_color=ConfigCtk.BG2, corner_radius=3, padx=10, pady=3, font=("Roboto", 12), text_color=ConfigCtk.FG, width=250)
        self.data_dir_label.pack(side="left", padx=(0, 5))
        self.data_dir_btn = ctk.CTkButton(self.data_frame, text="Browse", command=self._browse_data_dir, width=70, font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.data_dir_btn.pack(side="left")

        self.buttons_frame = ctk.CTkFrame(self.api_frame, fg_color="transparent")
        self.buttons_frame.pack(fill="x", padx=5, pady=5)
        self.connect_btn = ctk.CTkButton(self.buttons_frame, text="Connect", command=self._connect_mt5, width=90, font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.connect_btn.pack(side="left", padx=(0, 2))
        self.disconnect_btn = ctk.CTkButton(self.buttons_frame, text="Disconnect", command=self._disconnect_mt5, width=90, state="disabled", font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.disconnect_btn.pack(side="left", padx=(0, 10))
        self.auto_connect_cb = ctk.CTkCheckBox(self.buttons_frame, text="Auto", variable=self.auto_connect_var, command=self._on_auto_connect_change, font=("Roboto", 12), fg_color=ConfigCtk.BG2, border_color=ConfigCtk.BG3, hover_color=ConfigCtk.BG3, text_color=ConfigCtk.FG)
        self.auto_connect_cb.pack(side="left")

        self.symbol_frame_row = ctk.CTkFrame(self.api_frame, fg_color="transparent")
        self.symbol_frame_row.pack(fill="x", padx=5, pady=2)
        ctk.CTkLabel(self.symbol_frame_row, text="Market Watch Symbols:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.mw_symbol_var = ctk.StringVar()
        self.mw_symbol_combo = ctk.CTkComboBox(self.symbol_frame_row, variable=self.mw_symbol_var, width=150, state="readonly", values=["Select Symbol"], font=("Roboto", 12), fg_color=ConfigCtk.BG2, text_color=ConfigCtk.FG, border_color=ConfigCtk.BG3, button_color=ConfigCtk.ACCENT, button_hover_color=ConfigCtk.ACCENT2, dropdown_fg_color=ConfigCtk.BG1, dropdown_text_color=ConfigCtk.FG, dropdown_hover_color=ConfigCtk.BG3)
        self.mw_symbol_combo.pack(side="left", padx=(0, 10))
        self.mw_symbol_combo.set("Select Symbol")
        self.mw_symbol_combo.configure(command=self._on_mw_symbol_selected)

        ctk.CTkLabel(self.symbol_frame_row, text="All Symbols:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.all_symbols_var = ctk.StringVar()
        self.all_symbols_combo = ctk.CTkComboBox(self.symbol_frame_row, variable=self.all_symbols_var, width=150, state="readonly", values=["Select Symbol"], font=("Roboto", 12), fg_color=ConfigCtk.BG2, text_color=ConfigCtk.FG, border_color=ConfigCtk.BG3, button_color=ConfigCtk.ACCENT, button_hover_color=ConfigCtk.ACCENT2, dropdown_fg_color=ConfigCtk.BG1, dropdown_text_color=ConfigCtk.FG, dropdown_hover_color=ConfigCtk.BG3)
        self.all_symbols_combo.pack(side="left")
        self.all_symbols_combo.set("Select Symbol")

        self.rate_frame = ctk.CTkFrame(self.api_frame, height=35, fg_color=ConfigCtk.BG2)
        self.rate_frame.pack(fill="x", padx=5, pady=3)
        self.rate_label = ctk.CTkLabel(self.rate_frame, text="Rate: N/A", font=("Courier", 12, "bold"), text_color=ConfigCtk.GREEN)
        self.rate_label.pack(fill="x", padx=5, pady=3)

        self.fetch_frame = ctk.CTkFrame(self.parent, fg_color=ConfigCtk.BG1, corner_radius=0)
        self.fetch_frame.pack(fill="x", padx=1, pady=1)

        row1 = ctk.CTkFrame(self.fetch_frame, fg_color="transparent")
        row1.pack(fill="x", padx=5, pady=2)
        self.fetch_btn = ctk.CTkButton(row1, text="FETCH ALL", command=self._fetch_all_symbols, width=90, state="disabled", font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.fetch_btn.pack(side="left", padx=(0, 5))
        self.fetch_selection_btn = ctk.CTkButton(row1, text="FETCH SELECTION", command=self._fetch_selected_symbol, width=120, state="disabled", font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.fetch_selection_btn.pack(side="left", padx=(0, 5))
        ctk.CTkLabel(row1, text="Symbol:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.symbol_combo = ctk.CTkComboBox(row1, width=120, state="readonly", values=["Select Symbol"], font=("Roboto", 12), fg_color=ConfigCtk.BG2, text_color=ConfigCtk.FG, border_color=ConfigCtk.BG3, button_color=ConfigCtk.ACCENT, button_hover_color=ConfigCtk.ACCENT2, dropdown_fg_color=ConfigCtk.BG1, dropdown_text_color=ConfigCtk.FG, dropdown_hover_color=ConfigCtk.BG3)
        self.symbol_combo.pack(side="left", padx=(0, 5))
        self.symbol_combo.set("Select Symbol")
        self.symbol_combo.configure(command=self._on_symbol_selected)
        self.refresh_symbols_btn = ctk.CTkButton(row1, text="↻", command=self._refresh_symbols, width=25, state="disabled", font=("Roboto", 12), fg_color=ConfigCtk.DARK_BLUE, hover_color="#2e4aad", text_color=ConfigCtk.FG)
        self.refresh_symbols_btn.pack(side="left", padx=(0, 5))
        ctk.CTkLabel(row1, text="Bars:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 2))
        self.bars_entry = ctk.CTkEntry(row1, width=60, font=("Roboto", 12), fg_color=ConfigCtk.BG2, text_color=ConfigCtk.FG, border_color=ConfigCtk.BG3, border_width=1)
        self.bars_entry.pack(side="left")
        self.bars_entry.delete(0, 'end')
        self.bars_entry.insert(0, str(DEFAULT_MAX_BARS))

        row2 = ctk.CTkFrame(self.fetch_frame, fg_color="transparent")
        row2.pack(fill="x", padx=5, pady=3)
        ctk.CTkLabel(row2, text="TF:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 3))
        tf_checkbox_frame = ctk.CTkFrame(row2, fg_color="transparent")
        tf_checkbox_frame.pack(side="left", fill="x", expand=True)
        self.timeframe_vars = {}
        for tf in TIMEFRAME_ORDER:
            var = ctk.BooleanVar(value=True)
            self.timeframe_vars[tf] = var
            ctk.CTkCheckBox(tf_checkbox_frame, text=tf, variable=var, font=("Roboto", 12), fg_color=ConfigCtk.BG2, border_color=ConfigCtk.BG3, hover_color=ConfigCtk.BG3, text_color=ConfigCtk.FG).pack(side="left", padx=2)

        row3 = ctk.CTkFrame(self.fetch_frame, fg_color="transparent")
        row3.pack(fill="x", padx=5, pady=3)
        ctk.CTkLabel(row3, text="Ind:", font=("Roboto", 12), text_color=ConfigCtk.FG_DIM).pack(side="left", padx=(0, 8))
        ind_checkbox_frame = ctk.CTkFrame(row3, fg_color="transparent")
        ind_checkbox_frame.pack(side="left", fill="x", expand=True)
        self.indicators_vars = {}
        for display_text, key in [('MACD', 'macd'), ('RSI', 'rsi'), ('Stochastic', 'stochastic'), ('BB', 'bb'), ('MAs', 'mas')]:
            var = ctk.BooleanVar(value=False)
            self.indicators_vars[key] = var
            ctk.CTkCheckBox(ind_checkbox_frame, text=display_text, variable=var, font=("Roboto", 12), command=self._update_indicators_config, fg_color=ConfigCtk.BG2, border_color=ConfigCtk.BG3, hover_color=ConfigCtk.BG3, text_color=ConfigCtk.FG).pack(side="left", padx=5)

        self.table_frame = ctk.CTkFrame(self.fetch_frame, height=90, fg_color=ConfigCtk.BG2)
        self.table_frame.pack(fill="x", padx=5, pady=5)
        self.table_frame.pack_propagate(False)
        self.table_header_label = ctk.CTkLabel(self.table_frame, text="", font=("Courier", 12, "bold"), text_color=ConfigCtk.FG)
        self.table_header_label.pack(fill="x", padx=2, pady=1)
        self.table_available_label = ctk.CTkLabel(self.table_frame, text="", font=("Courier", 12), text_color="#00FFFF", anchor="w")
        self.table_available_label.pack(fill="x", padx=2, pady=1)
        self.table_fetched_label = ctk.CTkLabel(self.table_frame, text="", font=("Courier", 12, "bold"), text_color=ConfigCtk.GREEN, anchor="w")
        self.table_fetched_label.pack(fill="x", padx=2, pady=1)

        self.log_frame = ctk.CTkFrame(self.parent, fg_color=ConfigCtk.BG1, corner_radius=0)
        self.log_frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.log_text = ctk.CTkTextbox(self.log_frame, height=200, font=("Consolas", 12), fg_color=ConfigCtk.BG1, text_color=ConfigCtk.YELLOW, border_color=ConfigCtk.BG3, border_width=1)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_text.bind("<MouseWheel>", self._on_mousewheel)
        self.log_text.bind("<Button-4>", self._on_mousewheel)
        self.log_text.bind("<Button-5>", self._on_mousewheel)
        self._setup_log_menu()

    def _fetch_selected_symbol(self):
        if not self.is_connected():
            self.log("❌ Not connected")
            return

        symbol = self.symbol_combo.get()
        if not symbol or symbol == "Select Symbol":
            self.log("❌ No symbol selected")
            return

        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs:
            self.log("❌ Select at least one timeframe")
            return

        count = int(self.bars_entry.get())
        if count > DEFAULT_MAX_BARS:
            count = DEFAULT_MAX_BARS
            self.bars_entry.delete(0, 'end')
            self.bars_entry.insert(0, str(DEFAULT_MAX_BARS))
            self.log(f"📊 Capping bars to {DEFAULT_MAX_BARS} (max limit)")

        self.log(f"🚀 Fetching {count} bars for selected symbol: {symbol}")
        self.log(f"📊 Timeframes: {', '.join(selected_tfs)}")

        self.fetch_btn.configure(state="disabled", text="FETCHING...")
        self.fetch_selection_btn.configure(state="disabled", text="FETCHING...")

        def fetch_thread():
            tf_results = {}
            for tf_idx, tf in enumerate(selected_tfs, 1):
                self.log(f"  🔄 Fetching {tf} ({tf_idx}/{len(selected_tfs)})...")
                data = self.mt5_api.get_rates(symbol, tf, count)

                if data is not None:
                    actual_bars = len(data)
                    tf_results[tf] = actual_bars
                    if actual_bars < count:
                        self.log(f"  ⚠️ {tf}: Got {actual_bars}/{count} bars (MT5 limit)")
                    else:
                        self.log(f"  ✅ {tf}: {actual_bars} bars")
                    self._save_to_csv(symbol, tf, data, actual_bars)
                else:
                    tf_results[tf] = 0
                    self.log(f"  ❌ {tf}: No data received")
                time.sleep(0.1)

            self.parent.after(0, self._update_fetched_display, tf_results)
            self.parent.after(0, lambda: self._update_fetch_selection_complete(tf_results, symbol))
            self.parent.after(0, lambda: self.fetch_btn.configure(state="normal", text="FETCH ALL"))
            self.parent.after(0, lambda: self.fetch_selection_btn.configure(state="normal", text="FETCH SELECTION"))

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _fetch_all_symbols(self):
        if not self.is_connected():
            self.log("❌ Not connected")
            return
        symbols = self.mt5_api.get_market_watch_symbols()
        if not symbols:
            self.log("❌ No Market Watch symbols available")
            return
        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs:
            self.log("❌ Select at least one timeframe")
            return

        count = int(self.bars_entry.get())
        if count > DEFAULT_MAX_BARS:
            count = DEFAULT_MAX_BARS
            self.bars_entry.delete(0, 'end')
            self.bars_entry.insert(0, str(DEFAULT_MAX_BARS))
            self.log(f"📊 Capping bars to {DEFAULT_MAX_BARS} (max limit)")

        self.log(f"🚀 Fetching {count} bars for ALL {len(symbols)} Market Watch symbols")
        self.log(f"📊 Timeframes: {', '.join(selected_tfs)}")
        self.fetch_btn.configure(state="disabled", text="FETCHING...")

        def fetch_thread():
            results = {}
            for symbol_idx, symbol in enumerate(symbols, 1):
                self.log(f"\n📈 Processing symbol {symbol_idx}/{len(symbols)}: {symbol}")
                symbol_results = {}
                for tf_idx, tf in enumerate(selected_tfs, 1):
                    self.log(f"  🔄 Fetching {tf} ({tf_idx}/{len(selected_tfs)})...")
                    data = self.mt5_api.get_rates(symbol, tf, count)
                    if data is not None:
                        actual_bars = len(data)
                        symbol_results[tf] = actual_bars
                        if actual_bars < count:
                            self.log(f"  ⚠️ {tf}: Got {actual_bars}/{count} bars (MT5 limit)")
                        else:
                            self.log(f"  ✅ {tf}: {actual_bars} bars")
                        self._save_to_csv(symbol, tf, data, actual_bars)
                    else:
                        symbol_results[tf] = 0
                        self.log(f"  ❌ {tf}: No data received")
                    time.sleep(0.1)
                results[symbol] = symbol_results
                time.sleep(0.2)

            if symbols and symbols[0] in results:
                self.parent.after(0, self._update_fetched_display, results[symbols[0]])
            self.parent.after(0, self._update_fetch_complete, results, len(symbols))
            self.parent.after(0, lambda: self.fetch_btn.configure(state="normal", text="FETCH ALL"))

        threading.Thread(target=fetch_thread, daemon=True).start()

    def _on_symbol_selected(self, choice=None):
        if not self.is_connected(): return
        symbol = self.symbol_combo.get()
        if not symbol or symbol == "Select Symbol": return
        selected_tfs = self.get_selected_timeframes()
        if not selected_tfs: return

        def verify_thread():
            self.log(f"🔍 Checking available bars for {symbol}...")
            available = {}
            for tf in selected_tfs:
                data = self.mt5_api.get_rates(symbol, tf, DEFAULT_MAX_BARS)
                available[tf] = len(data) if data is not None else 0
                time.sleep(0.2)
            self.parent.after(0, self._update_available_display, available)
        threading.Thread(target=verify_thread, daemon=True).start()

    def _update_fetch_selection_complete(self, tf_results, symbol):
        total_bars = sum(tf_results.values())
        self.log(f"\n{'='*50}")
        self.log(f"✅ FETCH COMPLETE: {symbol}")
        self.log(f"📊 Total bars fetched: {total_bars}")
        self.log(f"{'='*50}")
        fetched_tfs = [tf for tf, bars in tf_results.items() if bars > 0]
        if fetched_tfs:
            self.log(f"  • Fetched timeframes: {', '.join(fetched_tfs)}")

    def _browse_path(self):
        filename = filedialog.askopenfilename(title="Select MetaTrader 5 Terminal", filetypes=[("Executable files", "*.exe"), ("All files", "*.*")])
        if filename:
            self.path_entry.delete(0, 'end')
            self.path_entry.insert(0, filename)
            self.log(f"📁 Terminal path set to: {filename}")

    def _browse_data_dir(self):
        directory = filedialog.askdirectory(title="Select Data Output Folder", initialdir=self.data_dir)
        if directory:
            self.data_dir = directory
            self.data_dir_label.configure(text=os.path.basename(directory))
            self.log(f"📁 Data folder set to: {directory}")

    def _on_mousewheel(self, event):
        if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
            self.log_text.yview_scroll(-1, "units")
        elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
            self.log_text.yview_scroll(1, "units")
        return "break"

    def _setup_log_menu(self):
        menu = tk.Menu(self.log_text, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_log)
        menu.add_command(label="Clear", command=self._clear_log)
        self.log_text.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    def _copy_log(self):
        text = self.log_text.selection_get() if self.log_text.tag_ranges("sel") else self.log_text.get('1.0', 'end-1c')
        self.parent.clipboard_clear()
        self.parent.clipboard_append(text)
        self.log("✅ Log copied to clipboard")

    def _clear_log(self):
        self.log_text.delete('1.0', 'end')

    def _on_auto_connect_change(self):
        self.log("Auto Connect enabled" if self.auto_connect_var.get() else "Auto Connect disabled")

    def _on_mw_symbol_selected(self, choice=None):
        selected_symbol = self.mw_symbol_var.get()
        if selected_symbol and selected_symbol != "Select Symbol":
            self._stop_rate_updates()
            self.rate_update_count = 0
            self.current_rate_symbol = selected_symbol
            self.log(f"🎯 Selected Market Watch symbol: {selected_symbol}")
            self.log(f"📊 Will fetch bid/ask {self.max_rate_updates} times")
            self._update_rate_display()
        else:
            self.current_rate_symbol = None
            self.rate_label.configure(text="Rate: N/A")
            self._stop_rate_updates()

    def _stop_rate_updates(self):
        if self.rate_update_job:
            self.parent.after_cancel(self.rate_update_job)
            self.rate_update_job = None
        self.rate_update_count = 0

    def _update_rate_display(self):
        if not self.current_rate_symbol or not self.is_connected():
            self.rate_label.configure(text="Rate: N/A")
            return

        if self.rate_update_count >= self.max_rate_updates:
            self.rate_label.configure(text=f"Rate: {self.current_rate_symbol} - Completed {self.max_rate_updates} updates", text_color=ConfigCtk.GREEN)
            self.log(f"✅ Completed {self.max_rate_updates} bid/ask updates for {self.current_rate_symbol}")
            self.rate_update_job = None
            return

        self.rate_update_count += 1
        price_data = self.mt5_api.get_current_price(self.current_rate_symbol)

        if price_data and 'bid' in price_data and 'ask' in price_data:
            bid, ask = float(price_data['bid']), float(price_data['ask'])
            spread = round((ask - bid) * 10000, 1)
            timestamp = datetime.now().strftime("%H:%M:%S")

            self.log_text.insert('end', f"[{timestamp}] {self.current_rate_symbol}: Bid={bid:.5f} Ask={ask:.5f} ({self.rate_update_count}/{self.max_rate_updates})\n")
            self.log_text.see('end')

            rate_text = f"{self.current_rate_symbol}:  BID={bid:.5f}  ASK={ask:.5f}  SPREAD={spread}"
            self.rate_label.configure(text=rate_text)

            if spread <= 1.0: self.rate_label.configure(text_color=ConfigCtk.GREEN)
            elif spread <= 5.0: self.rate_label.configure(text_color=ConfigCtk.YELLOW)
            else: self.rate_label.configure(text_color=ConfigCtk.RED)
        else:
            self.rate_label.configure(text=f"Rate: No data for {self.current_rate_symbol}")
            self.log(f"⚠️ No price data for {self.current_rate_symbol}")

        if self.rate_update_count < self.max_rate_updates:
            self.rate_update_job = self.parent.after(self.rate_update_interval, self._update_rate_display)
        else:
            self.rate_update_job = None
            self.log(f"⏹️ Stopped updates for {self.current_rate_symbol} after {self.max_rate_updates} requests")

    def _load_config(self):
        defaults = {'mt5_path': '', 'auto_connect': 'false', 'data_dir': DEFAULT_DATA_DIR, 'window_x': '100', 'window_y': '100'}
        if not os.path.exists(self.config_file): return defaults

        config = configparser.ConfigParser()
        config.read(self.config_file)
        loaded = defaults.copy()
        if 'MT5' in config:
            for key in ['mt5_path', 'auto_connect', 'data_dir']:
                if key in config['MT5']: loaded[key] = config['MT5'][key]
        if 'Window' in config:
            for key in ['window_x', 'window_y']:
                if key in config['Window']: loaded[key] = config['Window'][key]
        return loaded

    def _apply_config(self):
        if 'mt5_path' in self.saved_state:
            self.path_entry.delete(0, 'end')
            self.path_entry.insert(0, self.saved_state['mt5_path'])
        if 'auto_connect' in self.saved_state:
            self.auto_connect_var.set(self.saved_state['auto_connect'].lower() == 'true')
        if 'data_dir' in self.saved_state and self.saved_state['data_dir']:
            self.data_dir = self.saved_state['data_dir']
            self.data_dir_label.configure(text=os.path.basename(self.data_dir))

    def save_config(self):
        config = configparser.ConfigParser()
        config['MT5'] = {'mt5_path': self.path_entry.get(), 'auto_connect': str(self.auto_connect_var.get()), 'data_dir': self.data_dir}
        try:
            config['Window'] = {'window_x': str(self.parent.winfo_x()), 'window_y': str(self.parent.winfo_y())}
        except Exception:
            pass
        with open(self.config_file, 'w') as f:
            config.write(f)
        self.log("✅ Configuration saved")

    def log(self, message: str):
        self.log_text.insert('end', f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see('end')

    def update_symbol_dropdowns(self, market_watch_symbols=None, all_symbols=None):
        if market_watch_symbols is not None:
            self.mw_symbol_combo.configure(values=market_watch_symbols)
            if market_watch_symbols:
                self.mw_symbol_combo.set(market_watch_symbols[0])
                self._stop_rate_updates()
                self.rate_update_count = 0
                self.current_rate_symbol = market_watch_symbols[0]
                self._update_rate_display()
                ext_symbols = [s for s in market_watch_symbols if '.' in str(s)]
                if ext_symbols: self.log(f"📊 Includes {len(ext_symbols)} symbols with extensions: {ext_symbols[:5]}")
        if all_symbols is not None:
            self.all_symbols_combo.configure(values=all_symbols)
            if all_symbols:
                self.all_symbols_combo.set(all_symbols[0])
                ext_symbols = [s for s in all_symbols if '.' in str(s)]
                if ext_symbols: self.log(f"📊 Includes {len(ext_symbols)} symbols with extensions: {ext_symbols[:5]}")

    def get_selected_market_watch_symbol(self): return self.mw_symbol_var.get()
    def get_selected_all_symbol(self): return self.all_symbols_var.get()

    def _connect_mt5(self):
        self.connect_btn.configure(state="disabled", text="Connecting...")
        def connect_thread():
            path = self.path_entry.get().strip() or None
            self.log("Connecting to MT5...")
            self.mt5_api = MT5API(path=path)
            if self.mt5_api.initialize():
                self.connected = True
                market_watch_symbols = self.mt5_api.get_market_watch_symbols()
                all_symbols = self.mt5_api.get_all_symbols()
                self._schedule_gui_update(self._update_symbols_gui, market_watch_symbols, all_symbols)
                self._schedule_gui_update(self._on_connect_success)
            else:
                self._schedule_gui_update(self._on_connect_failure, "Connection failed")
        threading.Thread(target=connect_thread, daemon=True).start()

    def _update_symbols_gui(self, market_watch_symbols, all_symbols):
        self.update_symbol_dropdowns(market_watch_symbols, all_symbols)
        if market_watch_symbols: self.log(f"✅ Loaded {len(market_watch_symbols)} Market Watch symbols")
        if all_symbols: self.log(f"✅ Loaded {len(all_symbols)} all symbols")
        if market_watch_symbols:
            self.symbol_combo.configure(values=market_watch_symbols)
            self.symbol_combo.set(market_watch_symbols[0])
            self.refresh_symbols_btn.configure(state="normal")
            self.fetch_btn.configure(state="normal")
            self.fetch_selection_btn.configure(state="normal")
            self.parent.after(100, self._on_symbol_selected)

    def _schedule_gui_update(self, method, *args):
        self.parent.after(0, lambda: method(*args))

    def _on_connect_success(self):
        self.connect_btn.configure(state="disabled", text="Connected")
        self.disconnect_btn.configure(state="normal")
        self.log("✅ MT5 connected successfully")
        self.save_config()
        if self.on_connect_callback: self.on_connect_callback(self.mt5_api)

    def _on_connect_failure(self, error: str):
        self.connect_btn.configure(state="normal", text="Connect")
        self.disconnect_btn.configure(state="disabled")
        self.log(f"❌ Connection failed: {error}")

    def _disconnect_mt5(self):
        self._stop_rate_updates()
        self.rate_label.configure(text="Rate: N/A")
        self.current_rate_symbol = None
        if self.mt5_api:
            self.log("Disconnecting from MT5...")
            self.mt5_api.shutdown()
            self.log("✅ MT5 disconnected")
            self.mt5_api = None
        self.connected = False
        self.connect_btn.configure(state="normal", text="Connect")
        self.disconnect_btn.configure(state="disabled")
        self.mw_symbol_combo.set("Select Symbol")
        self.all_symbols_combo.set("Select Symbol")
        self.mw_symbol_combo.configure(values=["Select Symbol"])
        self.all_symbols_combo.configure(values=["Select Symbol"])
        self.symbol_combo.configure(values=["Select Symbol"])
        self.symbol_combo.set("Select Symbol")
        self.refresh_symbols_btn.configure(state="disabled")
        self.fetch_btn.configure(state="disabled")
        self.table_header_label.configure(text="")
        self.table_available_label.configure(text="")
        self.table_fetched_label.configure(text="")
        self.save_config()
        if self.on_disconnect_callback: self.on_disconnect_callback()

    def get_api(self): return self.mt5_api
    def is_connected(self): return self.connected and self.mt5_api and self.mt5_api.is_connected
    def set_callbacks(self, on_connect=None, on_disconnect=None):
        self.on_connect_callback = on_connect
        self.on_disconnect_callback = on_disconnect

    def refresh_prices(self):
        if self.current_rate_symbol and self.is_connected():
            self._stop_rate_updates()
            self.rate_update_count = 0
            self.log(f"🔄 Manual refresh - fetching {self.max_rate_updates} updates for {self.current_rate_symbol}")
            self._update_rate_display()
            return True
        return False

    def get_selected_timeframes(self):
        return [tf for tf, var in self.timeframe_vars.items() if var.get()]

    def get_selected_indicators(self):
        return {key: var.get() for key, var in self.indicators_vars.items()}

    def _update_indicators_config(self):
        selected = [name for name, selected in self.get_selected_indicators().items() if selected]
        self.log(f"📊 Indicators enabled: {', '.join(selected)}" if selected else "📊 Indicators disabled")

    def _refresh_symbols(self):
        if not self.is_connected():
            self.log("❌ Not connected")
            return
        def get_thread():
            self.log("📋 Fetching symbols...")
            symbols = self.mt5_api.get_market_watch_symbols()
            if symbols:
                self.parent.after(0, self._update_symbols_dropdown, symbols)
                self.log(f"✅ Got {len(symbols)} symbols")
            else:
                self.log("❌ No symbols received")
        threading.Thread(target=get_thread, daemon=True).start()

    def _update_symbols_dropdown(self, symbols):
        self.symbol_combo.configure(values=symbols)
        if symbols:
            self.symbol_combo.set(symbols[0])
            self.parent.after(100, self._on_symbol_selected)

    def _update_available_display(self, available):
        col_width = 8
        header = "TF:" + "".join(" " * (col_width // 2 - len(tf) // 2) + tf + " " * (col_width - len(tf) - (col_width // 2 - len(tf) // 2)) for tf in TIMEFRAME_ORDER)
        self.table_header_label.configure(text=header)

        available_row = "Avail:"
        for tf in TIMEFRAME_ORDER:
            bars = available.get(tf, 0)
            if bars > 0:
                bars_str = f"{bars/1000000:.1f}M" if bars >= 1000000 else f"{bars/1000:.1f}K" if bars >= 1000 else str(bars)
                available_row += " " * (col_width - len(bars_str)) + bars_str
            else:
                na_text = "N/A"
                padding = col_width - len(na_text)
                available_row += " " * (padding // 2) + na_text + " " * (padding - padding // 2)
        self.table_available_label.configure(text=available_row)

        fetched_row = "Fetched:" + " " * col_width * len(TIMEFRAME_ORDER)
        self.table_fetched_label.configure(text=fetched_row)
        self.log("📊 Available bars:")
        for tf in TIMEFRAME_ORDER:
            if tf in available and available[tf] > 0:
                self.log(f"   • {tf}: {available[tf]} bars")

    def _update_fetched_display(self, fetched):
        col_width = 8
        fetched_row = "Fetched:"
        for tf in TIMEFRAME_ORDER:
            bars = fetched.get(tf, 0)
            if bars > 0:
                bars_str = f"{bars/1000000:.1f}M" if bars >= 1000000 else f"{bars/1000:.1f}K" if bars >= 1000 else str(bars)
                fetched_row += " " * (col_width - len(bars_str)) + bars_str
            else:
                na_text = "N/A"
                padding = col_width - len(na_text)
                fetched_row += " " * (padding // 2) + na_text + " " * (padding - padding // 2)
        self.table_fetched_label.configure(text=fetched_row)
        self.log("🎯 Fetch complete!")

    def _update_fetch_complete(self, results, total_symbols):
        total_bars = sum(sum(symbol_results.values()) for symbol_results in results.values())
        self.log(f"\n{'='*50}")
        self.log(f"✅ FETCH COMPLETE: Processed {total_symbols} symbols")
        self.log(f"📊 Total bars fetched: {total_bars}")
        self.log(f"{'='*50}")
        for symbol, tf_results in results.items():
            fetched_tfs = [tf for tf, bars in tf_results.items() if bars > 0]
            if fetched_tfs: self.log(f"  • {symbol}: {len(fetched_tfs)} timeframes")

    def _save_to_csv(self, symbol, timeframe, data, actual_bars):
        os.makedirs(self.data_dir, exist_ok=True)
        safe_symbol = symbol.replace('/', '_').replace('.', '_').replace('\\', '_')
        symbol_dir = os.path.join(self.data_dir, safe_symbol)
        os.makedirs(symbol_dir, exist_ok=True)

        df = pd.DataFrame(data)
        expected_columns = ['time', 'open', 'high', 'low', 'close', 'volume']
        df = df[expected_columns] if set(expected_columns).issubset(set(df.columns)) else df.iloc[:, :6]
        df.columns = expected_columns

        filepath = os.path.join(symbol_dir, f"{safe_symbol}_{timeframe}.csv")
        rel_path = os.path.relpath(filepath, os.path.expanduser("~"))
        if os.path.exists(filepath):
            self.log(f"   ⚠️ File exists, overwriting: ~/{rel_path}")
        else:
            self.log(f"   💾 Saved {actual_bars} bars to ~/{rel_path}")
        df.to_csv(filepath, index=False)


class StandaloneMode:
    """Standalone GUI mode that creates its own window using CustomTkinter."""

    def __init__(self, root_window=None, config_file="mt5_connector.ini"):
        self.root = root_window or ctk.CTk()
        self.root.title("MT5 Connector")
        self.root.configure(fg_color=ConfigCtk.BG0)
        self.root.geometry(f"{ConfigCtk.WIN_W}x{ConfigCtk.WIN_H}")
        self.root.minsize(ConfigCtk.WIN_W, ConfigCtk.WIN_H)
        self.root.maxsize(ConfigCtk.WIN_W, ConfigCtk.WIN_H)
        self.root.resizable(False, False)

        self.saved_window_state = self._load_window_config(config_file)
        if 'window_x' in self.saved_window_state and 'window_y' in self.saved_window_state:
            try:
                self.root.geometry(f"+{int(self.saved_window_state['window_x'])}+{int(self.saved_window_state['window_y'])}")
            except Exception:
                pass

        main_container = ctk.CTkFrame(self.root, fg_color=ConfigCtk.BG0, corner_radius=0)
        main_container.pack(fill="both", expand=True, padx=0, pady=0)
        self.gui = MT5APIEmbed(main_container, config_file=config_file)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.gui.log("Standalone Mode initialized - Dark Industrial theme with Amber accent")
        self.gui.log("Direct MT5 API - No login/password required")

    def _load_window_config(self, config_file):
        defaults = {'window_x': '100', 'window_y': '100'}
        if not os.path.exists(config_file): return defaults
        config = configparser.ConfigParser()
        config.read(config_file)
        loaded = defaults.copy()
        if 'Window' in config:
            for key in ['window_x', 'window_y']:
                if key in config['Window']: loaded[key] = config['Window'][key]
        return loaded

    def save_config(self):
        self.gui.save_config()
        config = configparser.ConfigParser()
        if os.path.exists(self.gui.config_file): config.read(self.gui.config_file)
        if 'Window' not in config: config['Window'] = {}
        config['Window']['window_x'] = str(self.root.winfo_x())
        config['Window']['window_y'] = str(self.root.winfo_y())
        with open(self.gui.config_file, 'w') as f:
            config.write(f)
        self.gui.log("✅ Configuration saved")

    def _on_closing(self):
        if messagebox.askokcancel("Exit", "Are you sure you want to exit?"):
            self.save_config()
            if self.gui.mt5_api:
                self.gui.mt5_api.shutdown()
            self.root.destroy()

    def run(self):
        self.root.mainloop()

    def log(self, message: str): self.gui.log(message)
    def get_api(self): return self.gui.get_api()
    def is_connected(self): return self.gui.is_connected()
    def get_selected_market_watch_symbol(self): return self.gui.get_selected_market_watch_symbol()
    def get_selected_all_symbol(self): return self.gui.get_selected_all_symbol()
    def update_symbol_dropdowns(self, market_watch_symbols=None, all_symbols=None): self.gui.update_symbol_dropdowns(market_watch_symbols, all_symbols)
    def set_callbacks(self, on_connect=None, on_disconnect=None): self.gui.set_callbacks(on_connect, on_disconnect)


def setup_ctk_theme():
    ctk.set_appearance_mode(ConfigCtk.APPEARANCE_MODE)
    ctk.set_default_color_theme(ConfigCtk.COLOR_THEME)

def launch_mt5_api_client():
    setup_ctk_theme()
    app = StandaloneMode()
    app.run()

if __name__ == "__main__":
    launch_mt5_api_client()