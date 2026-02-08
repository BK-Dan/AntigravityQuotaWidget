import os
import shutil
import sqlite3
import json
import requests
import tkinter as tk
import tempfile
import time
import threading
from datetime import datetime, timezone
import locale
from typing import Optional, Tuple, List, Dict, Any

# ================= Configuration & Constants =================

# Paths to search for Antigravity state

POSSIBLE_PATHS = [
    os.path.expandvars(r'%APPDATA%\Antigravity\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Google\Antigravity\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Google\Cloud Code\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Code\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Code - Insiders\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\VSCodium\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Cursor\User\globalStorage\state.vscdb'),
    os.path.expandvars(r'%APPDATA%\Windsurf\User\globalStorage\state.vscdb'),
]

# API Configuration
API_URL = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels" 
REFRESH_INTERVAL_MS = 1000 * 60 * 5  # 5 minutes

# UI Colors
COLOR_BG = '#101010'
COLOR_CARD_BG = '#1e1e1e'
COLOR_CARD_BORDER = '#333333'
COLOR_TEXT_PRIMARY = '#eeeeee'
COLOR_TEXT_SECONDARY = '#888888'
COLOR_TEXT_TERTIARY = '#555555'
COLOR_TEXT_WARNING = '#ffcc00'
COLOR_ACCENT_HEALTHY = '#00e676'   # Green
COLOR_ACCENT_LOW = '#ffcc00'       # Yellow (User Request: < 50%)
COLOR_ACCENT_EXCEEDED = '#ff4c4c'  # Red (User Request: Limit Exceeded)
COLOR_ACCENT_DEFAULT = '#4cc2ff'
COLOR_CLOSE_BTN = '#666666'
COLOR_CLOSE_BTN_HOVER = '#ffffff'

# UI Fonts
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_CARD_TITLE = ("Segoe UI", 9, "bold")
FONT_PCT_BIG = ("Segoe UI", 11, "bold")
FONT_PCT_SMALL = ("Segoe UI", 8, "bold")
FONT_STATUS = ("Segoe UI", 8)
FONT_DEFAULT = ("Segoe UI", 9)

# UI Dimensions (Pixel-Perfect 300px Layout)
LAYOUT_WIN_W = 300
LAYOUT_WIN_H_MIN = 320
LAYOUT_MARGIN = 13
LAYOUT_GAP = 10
LAYOUT_GRID_CARD_W = 132
LAYOUT_GRID_CARD_H = 120
LAYOUT_LIST_CARD_W = 274
LAYOUT_LIST_ITEM_H = 22

# Quota Thresholds
QUOTA_THRESHOLD_LOW = 0.5

# Model Group Definitions
MODEL_GROUPS = {
    'A': {'name': 'Gemini 3 Pro', 'ids': ['gemini-3-pro-high', 'gemini-3-pro-low']},
    'B': {'name': 'Gemini 3 Flash', 'ids': ['gemini-3-flash']},
    'C': {'name': 'Claude / GPT', 'ids': ['claude-sonnet-4-5', 'claude-opus-4-5-thinking', 'claude-sonnet-4-5-thinking', 'gpt-oss-120b-medium']},
    'D': {'name': 'Gemini 2.5 Flash', 'ids': ['gemini-2.5-flash', 'gemini-2.5-flash-thinking']}
}

# =================================================

class QuotaWidget(tk.Tk):
    """
    Antigravity Quota Widget
    Displays AI model quota usage in a compact, always-on-top window.
    """
    def __init__(self) -> None:
        super().__init__()

        # Localization: Set title based on system language
        self.ui_title = "Antigravity Model Quota"
        try:
            locale.setlocale(locale.LC_ALL, '')
            sys_lang = locale.getlocale()[0]
            if sys_lang and (sys_lang.startswith('ko') or sys_lang.startswith('Korean')):
                self.ui_title = "반중력 모델 사용량"
        except Exception:
            pass # Fallback to English

        # Window Setup
        self.title(self.ui_title)
        self.geometry(f"{LAYOUT_WIN_W}x{LAYOUT_WIN_H_MIN}") 
        self.overrideredirect(True)  
        self.attributes('-topmost', True) 
        self.attributes('-alpha', 0.95)
        self.configure(bg=COLOR_BG) 
        
        # Drag Logic
        self.bind('<Button-1>', self.start_move)
        self.bind('<B1-Motion>', self.do_move)
        
        # Main Canvas
        self.canvas = tk.Canvas(self, bg=COLOR_BG, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Button-1>', self.start_move)
        self.canvas.bind('<B1-Motion>', self.do_move)

        # Close Button
        self.close_btn = tk.Label(self, text="✕", font=FONT_DEFAULT, fg=COLOR_CLOSE_BTN, bg=COLOR_BG, cursor="hand2")
        self.close_btn.place(x=LAYOUT_WIN_W - 30, y=10) # Position: 270 (Left-shifted as requested)
        self.close_btn.bind('<Button-1>', lambda e: self.destroy())
        self.close_btn.bind('<Enter>', lambda e: self.close_btn.config(fg=COLOR_CLOSE_BTN_HOVER))
        self.close_btn.bind('<Leave>', lambda e: self.close_btn.config(fg=COLOR_CLOSE_BTN))

        # Title Label (Click to refresh)
        self.title_lbl = tk.Label(self, text=self.ui_title, font=FONT_TITLE, fg=COLOR_TEXT_PRIMARY, bg=COLOR_BG, cursor="hand2")
        self.title_lbl.place(x=15, y=12)
        self.title_lbl.bind('<Button-1>', lambda e: self.fetch_data())

        self.win_x = 0
        self.win_y = 0
        
        # Start Data Fetch
        self.after(100, self.fetch_data)

    def start_move(self, event: tk.Event) -> None:
        """Records initial mouse position for dragging."""
        self.win_x = event.x
        self.win_y = event.y

    def do_move(self, event: tk.Event) -> None:
        """Updates window position during drag."""
        x = self.winfo_x() + (event.x - self.win_x)
        y = self.winfo_y() + (event.y - self.win_y)
        self.geometry(f"+{x}+{y}")

    def get_token(self) -> Optional[str]:
        """Retrieves the Antigravity API token from local VS Code/Plugin state DB."""
        db_path = self.get_db_path()
        if not db_path: 
            return None
        
        temp_dir = tempfile.gettempdir()
        temp_db = os.path.join(temp_dir, f"ag_widget_{int(time.time())}.db")
        try:
            shutil.copy2(db_path, temp_db)
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            token = None
            
            for key in ['antigravityAuthStatus', 'google.antigravity']:
                try:
                    cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (key,))
                    row = cursor.fetchone()
                    if row:
                        data = json.loads(row[0])
                        token = data.get('apiKey')
                        if token: break
                except Exception: pass
            
            conn.close()
            try: os.remove(temp_db)
            except Exception: pass
            return token
        except Exception:
            return None

    def get_db_path(self) -> Optional[str]:
        """Finds the first existing state.vscdb path from POSSIBLE_PATHS."""
        for path in POSSIBLE_PATHS:
            if os.path.exists(path): return path
        return None

    def fetch_data(self) -> None:
        """Initiates background thread to fetch data."""
        self.title_lbl.config(fg=COLOR_ACCENT_EXCEEDED, text="새로고침 중...")
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self) -> None:
        """Background thread execution for API request."""
        try:
            token = self.get_token()
            if not token:
                self.after(0, lambda: self.update_ui_error("로그인 필요"))
                return

            headers = {"Authorization": f"Bearer {token}", "User-Agent": "antigravity", "Content-Type": "application/json"}
            resp = requests.post(API_URL, headers=headers, json={}, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                models = data.get('models', {})
                self.process_and_update(models)
            else:
                self.after(0, lambda: self.update_ui_error(f"오류 {resp.status_code}"))

        except Exception:
            self.after(0, lambda: self.update_ui_error("네트워크 오류"))
        
        self.after(REFRESH_INTERVAL_MS, self.fetch_data)

    def _parse_model_info(self, info: Dict[str, Any]) -> Tuple[float, str]:
        """Parses individual model info to extract remaining quota and reset time."""
        q = info.get('quotaInfo', {})
        rem = -1.0
        reset_str = q.get('resetTime')
        
        if 'remainingFraction' in q: 
            rem = float(q['remainingFraction'])
        elif reset_str: 
            rem = -2.0 # Limit Exceeded
        
        time_str = ""
        if reset_str:
                try:
                    reset_dt = datetime.strptime(reset_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    if reset_dt > now_dt:
                        diff = reset_dt - now_dt
                        if diff.total_seconds() > 0:
                            mins = int(diff.total_seconds() / 60)
                            hours = mins // 60
                            mins = mins % 60
                            time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                except Exception: pass
        return rem, time_str

    def process_and_update(self, models: Dict[str, Any]) -> None:
        """Process API response and restructure for UI."""
        all_grouped_ids = set()
        for g in MODEL_GROUPS.values():
            all_grouped_ids.update(g['ids'])

        results = []
        
        # 1. Process Main Groups
        for g_info in MODEL_GROUPS.values():
            min_rem = -1.0
            target_time = ""
            found_any = False
            
            for m_id in g_info['ids']:
                if m_id in models:
                    found_any = True
                    rem, t_str = self._parse_model_info(models[m_id])
                    
                    if min_rem == -1 and rem != -1:
                        min_rem = rem
                        target_time = t_str
                    elif min_rem != -1 and rem != -1:
                        if rem < min_rem:
                            min_rem = rem
                            target_time = t_str
                    
                    if not target_time and t_str: target_time = t_str
            
            if found_any:
                results.append({'type': 'card', 'name': g_info['name'], 'rem': min_rem, 'time': target_time})

        # 2. Process Other Models
        others = []
        sorted_all_models = sorted(models.items(), key=lambda item: item[0])
        
        for m_id, m_info in sorted_all_models:
            if m_id not in all_grouped_ids:
                rem, t_str = self._parse_model_info(m_info)
                is_unlimited = (rem == -1) or (rem == 1 and not t_str)
                
                if not is_unlimited:
                    name = m_id.replace("-", " ").title()
                    if "Gemini" in name: name = name.replace("Gemini ", "Gemini-")
                    if len(name) > 25: name = name[:23] + ".."
                    
                    others.append({'name': name, 'rem': rem, 'time': t_str})
        
        if others:
            results.append({'type': 'list', 'name': 'Other Models', 'items': others})

        self.after(0, lambda: self.update_ui(results))

    def _get_status_props(self, rem: float) -> Tuple[str, str]:
        """Returns (status_text, color) based on remaining quota."""
        if rem == -1:
            return "Active", COLOR_ACCENT_HEALTHY
        elif rem == -2:
            return "Exceeded", COLOR_ACCENT_EXCEEDED
        elif rem < QUOTA_THRESHOLD_LOW: 
            return "Low", COLOR_ACCENT_LOW
        else:
            return "Healthy", COLOR_ACCENT_HEALTHY
            
    def update_ui_error(self, msg: str) -> None:
        """Displays error message on the canvas."""
        self.title_lbl.config(fg=COLOR_TEXT_SECONDARY, text=self.ui_title)
        self.canvas.delete("all")
        self.canvas.create_text(LAYOUT_WIN_W/2, 160, text=msg, fill=COLOR_TEXT_TERTIARY, font=("Segoe UI", 11))

    def update_ui(self, items: List[Dict[str, Any]]) -> None:
        """Draws the UI based on processed data items."""
        self.title_lbl.config(fg=COLOR_TEXT_PRIMARY, text=self.ui_title)
        self.canvas.delete("all")
        
        y_start = 50
        grid_items = [i for i in items if i.get('type') != 'list']
        list_items = [i for i in items if i.get('type') == 'list']
        
        # Grid Layout (2 columns)
        for idx, item in enumerate(grid_items):
            row = idx // 2
            col = idx % 2
            
            x = LAYOUT_MARGIN + (col * (LAYOUT_GRID_CARD_W + LAYOUT_GAP))
            y = y_start + (row * (LAYOUT_GRID_CARD_H + LAYOUT_GAP))
            
            self.draw_compact_card(x, y, item['name'], item['rem'], item['time'])

        # List Layout (Below Grid)
        grid_rows_count = (len(grid_items) + 1) // 2
        y_list = y_start + (grid_rows_count * (LAYOUT_GRID_CARD_H + LAYOUT_GAP)) + 5 
        
        for item in list_items:
            height = self.draw_list_card(LAYOUT_MARGIN, y_list, item['name'], item['items'])
            y_list += height + 5

        total_h = y_list + 10
        if total_h > 900: total_h = 900 
        self.geometry(f"{LAYOUT_WIN_W}x{total_h}")

    def draw_rounded_rect(self, x1: int, y1: int, x2: int, y2: int, radius: int = 25, **kwargs: Any) -> int:
        """Draws a rounded rectangle on the canvas."""
        points = [x1+radius, y1, x1+radius, y1, x2-radius, y1, x2-radius, y1,
                  x2, y1, x2, y1+radius, x2, y1+radius, x2, y2-radius, x2, y2-radius,
                  x2, y2, x2-radius, y2, x2-radius, y2, x1+radius, y2, x1+radius, y2,
                  x1, y2, x1, y2-radius, x1, y2-radius, x1, y1+radius, x1, y1+radius, x1, y1]
        return self.canvas.create_polygon(points, **kwargs, smooth=True)

    def draw_list_card(self, x: int, y: int, title: str, items: List[Dict[str, Any]]) -> int:
        """Draws the list-style card (Other Models) and returns its height."""
        # Calculate Height
        card_h = 25 + (len(items) * LAYOUT_LIST_ITEM_H) + 5
        
        # Background
        self.draw_rounded_rect(x, y, x+LAYOUT_LIST_CARD_W, y+card_h, radius=10, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER)
        
        # Title
        self.canvas.create_text(x+15, y+15, text=title, anchor="w", fill=COLOR_TEXT_PRIMARY, font=FONT_CARD_TITLE)
        
        iy = y + 35
        for it in items:
            name = it['name']
            rem = it['rem']
            time_str = it.get('time', '')
            
            # Name
            self.canvas.create_text(x+15, iy, text=name, anchor="w", fill="#cccccc", font=FONT_STATUS)
            
            # Alignment Logic
            end_x = x + 262 
            
            # Bar Layout
            bar_w = 85
            bar_h = 6
            bar_x = end_x - 35 - bar_w
            bar_y = iy - 3

            # Status props
            _, status_col = self._get_status_props(rem)
            
            pct_text = ""
            fill_w = 0

            if rem != -1:
                if rem == -2: 
                    pct_text = "LIMIT"
                else:
                    pct_text = f"{int(rem*100)}%"
                    fill_w = int(bar_w * rem)
            else:
                 pct_text = "∞"

            # Percent Text (Right Aligned)
            self.canvas.create_text(end_x, iy-1, text=pct_text, anchor="e", fill=status_col, font=FONT_PCT_SMALL)
            
            # Bar Drawing
            if rem != -1:
                # Background
                self.canvas.create_rectangle(bar_x, bar_y, bar_x+bar_w, bar_y+bar_h, fill=COLOR_CARD_BORDER, outline="")
                # Fill
                if fill_w > 0:
                    self.canvas.create_rectangle(bar_x, bar_y, bar_x+fill_w, bar_y+bar_h, fill=status_col, outline="")
                
                # Time Text
                if time_str:
                    self.canvas.create_text(bar_x+bar_w, iy-11, text=time_str, anchor="e", fill=COLOR_TEXT_SECONDARY, font=FONT_STATUS)

            iy += LAYOUT_LIST_ITEM_H
            
        return card_h

    def draw_compact_card(self, x: int, y: int, title: str, rem: float, time_str: str) -> None:
        """Draws a compact grid card."""
        w, h = LAYOUT_GRID_CARD_W, LAYOUT_GRID_CARD_H
        self.draw_rounded_rect(x, y, x+w, y+h, radius=10, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER)
        
        status_text, status_col = self._get_status_props(rem)

        # Gauge Center
        cx, cy = x + (w/2), y + 55
        r = 26
        
        self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline=COLOR_CARD_BORDER, width=6)
        
        if rem != -1:
            if rem == -2: # Exceeded
                self.canvas.create_text(cx, cy, text="LIMIT", fill=status_col, font=FONT_CARD_TITLE)
            else:
                extent = -359.9 * rem 
                self.canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=90, extent=extent, outline=status_col, width=6, style="arc")
                pct_text = f"{int(rem*100)}%"
                self.canvas.create_text(cx, cy, text=pct_text, fill="white", font=FONT_PCT_BIG)
        else:
            self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, outline=status_col, width=6)
            self.canvas.create_text(cx, cy, text="∞", fill=status_col, font= ("Segoe UI", 16, "bold"))

        # Titles
        self.canvas.create_text(cx, y+15, text=title, anchor="center", fill=COLOR_TEXT_PRIMARY, font=FONT_CARD_TITLE)
        
        # Reset Time
        if rem != -1:
            if time_str:
                self.canvas.create_text(cx, y+93, text=f"Reset: {time_str}", anchor="center", fill=COLOR_TEXT_SECONDARY, font=FONT_STATUS)
            else:
                self.canvas.create_text(cx, y+93, text="-", anchor="center", fill=COLOR_TEXT_TERTIARY, font=FONT_STATUS)
        else:
             self.canvas.create_text(cx, y+93, text="Unlimited", anchor="center", fill=COLOR_TEXT_SECONDARY, font=FONT_STATUS)

        # Dynamic Center Alignment for Status Group
        text_w = len(status_text) * 6 # approximate char width
        dot_w = 6
        gap = 5
        total_w = dot_w + gap + text_w
        
        start_x = cx - (total_w / 2)
        
        dot_y1 = y + 102
        self.canvas.create_oval(start_x, dot_y1, start_x+dot_w, dot_y1+dot_w, fill=status_col, outline="")
        
        text_x = start_x + dot_w + gap
        self.canvas.create_text(text_x, dot_y1-1, text=status_text, anchor="nw", fill=status_col, font=FONT_STATUS)


if __name__ == "__main__":
    app = QuotaWidget()
    app.mainloop()
