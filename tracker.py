import customtkinter as ctk
import pyautogui
import requests
import time
import threading
import ctypes
import os
import tempfile
import json
import multiprocessing
import webview
from datetime import datetime
from pynput import keyboard, mouse
from PIL import Image, ImageFilter

# --- CONFIGURATION & THEME ---
SERVER_URL = "http://127.0.0.1:5000"
LOCAL_LOG_FILE = "local_logs.json"
CONFIG_FILE = "agent_config.json"

# DeskTime Premium Colors
THEME_COLOR = "#00d4aa"  
DARK_BG = "#15171e"      
CARD_BG = "#222631"      
TEXT_MAIN = "#ffffff"
TEXT_SECONDARY = "#8b949e"
BTN_STOP = "#ff4757"     
BTN_PAUSE = "#f39c12"
HOVER_GHOST = "#1e222d" # NAYA: Transparent buttons ke liye hover color

# Global Variables
USER_ID = None
TRACKING_INTERVAL = 10 
IDLE_TIMEOUT_SECONDS = 30 
AVAILABLE_PROJECTS = ["General Work"]

# --- ACTIVITY TRACKING ---
activity_events = 0

def on_press(key):
    global activity_events
    activity_events += 1

def on_click(x, y, button, pressed):
    global activity_events
    if pressed: activity_events += 1

keyboard.Listener(on_press=on_press).start()
mouse.Listener(on_click=on_click).start()

ctk.set_appearance_mode("dark")

# --- IDLE DETECTION ---
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def get_idle_duration():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0

def open_dashboard(url):
    webview.create_window(f"My Personal Workspace", url, width=1200, height=800)
    webview.start()

# --- CONFIG MANAGEMENT ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f)

# --- LOGIN APP ---
class LoginApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DeskTime - Agent Login")
        self.geometry("350x400")
        self.resizable(False, False)
        self.configure(fg_color=DARK_BG)
        self.user_authenticated = False
        
        ctk.CTkLabel(self, text="DeskTime", font=("Playfair Display", 30, "bold"), text_color=THEME_COLOR).pack(pady=(40, 5))
        ctk.CTkLabel(self, text="Workspace Setup", font=("Outfit", 14), text_color=TEXT_SECONDARY).pack(pady=(0, 30))
        
        ctk.CTkLabel(self, text="Enter Employee ID / Username:", font=("Outfit", 12, "bold"), text_color=TEXT_MAIN).pack(anchor="w", padx=40)
        self.username_entry = ctk.CTkEntry(self, font=("Outfit", 14), height=40, fg_color=CARD_BG, border_color="#4a5568")
        self.username_entry.pack(fill="x", padx=40, pady=(5, 20))
        
        self.error_label = ctk.CTkLabel(self, text="", font=("Outfit", 11), text_color=BTN_STOP)
        self.error_label.pack()
        
        btn = ctk.CTkButton(self, text="Connect Agent", font=("Outfit", 14, "bold"), fg_color=THEME_COLOR, text_color="#000", height=45, command=self.do_login)
        btn.pack(fill="x", padx=40, pady=10)

    def do_login(self):
        uid = self.username_entry.get().strip()
        if not uid:
            self.error_label.configure(text="Employee ID cannot be empty!")
            return
        
        config = load_config()
        config['user_id'] = uid
        save_config(config)
        
        global USER_ID
        USER_ID = uid
        self.user_authenticated = True
        self.destroy()

# --- MAIN TRACKER APP ---
class TrackerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("DeskTime Agent Pro")
        self.geometry("380x660") 
        self.resizable(False, False)
        self.configure(fg_color=DARK_BG)
        
        self.is_tracking = False
        self.server_ss_enabled = True 
        self.nudge_shown = False
        self.session_seconds = 0 
        self.mini_win = None 
        
        self.setup_ui()
        threading.Thread(target=self.initial_fetch, daemon=True).start()

    def setup_ui(self):
        # 1. Top Bar
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.pack(fill="x", padx=20, pady=(15, 0))
        
        self.mini_var = ctk.BooleanVar(value=False)
        self.mini_switch = ctk.CTkSwitch(
            self.top_bar, text="📱 Mini Mode", variable=self.mini_var, 
            command=self.toggle_mini_mode, font=("Outfit", 11), 
            progress_color=THEME_COLOR, switch_width=30, switch_height=15
        )
        self.mini_switch.pack(side="right")

        # 2. Header
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(pady=(10, 5))
        
        ctk.CTkLabel(self.header, text="DeskTime", font=("Outfit", 26, "bold"), text_color=THEME_COLOR).pack(side="left")
        ctk.CTkLabel(self.header, text=" Agent", font=("Outfit", 26), text_color=TEXT_MAIN).pack(side="left")
        
        ctk.CTkLabel(self, text=f"Logged in as: {USER_ID}", font=("Outfit", 12), text_color=TEXT_SECONDARY).pack()

        # 3. Live Session Timer
        self.timer_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.timer_frame.pack(pady=(20, 10))
        
        ctk.CTkLabel(self.timer_frame, text="CURRENT SESSION", font=("Outfit", 10, "bold"), text_color=TEXT_SECONDARY).pack()
        self.timer_label = ctk.CTkLabel(self.timer_frame, text="00:00:00", font=("Courier", 42, "bold"), text_color=TEXT_MAIN)
        self.timer_label.pack()

        # 4. Control Card
        self.card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=15)
        self.card.pack(fill="x", padx=25, pady=15)

        ctk.CTkLabel(self.card, text="PROJECT SELECTION", font=("Outfit", 11, "bold"), text_color=TEXT_SECONDARY).pack(padx=20, pady=(15, 2), anchor="w")
        
        self.project_var = ctk.StringVar(value="General Work")
        self.project_dropdown = ctk.CTkOptionMenu(
            self.card, values=AVAILABLE_PROJECTS, variable=self.project_var,
            fg_color=DARK_BG, button_color=THEME_COLOR, button_hover_color="#00b892",
            font=("Outfit", 13), height=38, corner_radius=8
        )
        self.project_dropdown.pack(fill="x", padx=20, pady=(0, 15))

        self.blur_enabled = ctk.BooleanVar(value=False) 
        self.blur_switch = ctk.CTkSwitch(
            self.card, text="Privacy Mode (Blur Screen)", variable=self.blur_enabled,
            font=("Outfit", 12), progress_color=THEME_COLOR, text_color=TEXT_MAIN
        )
        self.blur_switch.pack(padx=20, pady=(0, 20), anchor="w")

        # 5. Main Buttons
        self.btn = ctk.CTkButton(
            self, text="START TRACKING", command=self.toggle,
            fg_color=THEME_COLOR, hover_color="#00b892", text_color=DARK_BG,
            height=55, font=("Outfit", 16, "bold"), corner_radius=12
        )
        self.btn.pack(fill="x", padx=25, pady=(10, 5))

        # Reset button initially hidden
        self.reset_btn = ctk.CTkButton(
            self, text="End Shift & Reset Timer", command=self.reset_timer,
            fg_color="transparent", border_width=1, border_color=BTN_STOP,
            text_color=BTN_STOP, hover_color="#3b1f23", height=35, corner_radius=8
        )

        self.status_label = ctk.CTkLabel(self, text="Status: Ready to Track", text_color=TEXT_SECONDARY, font=("Outfit", 12))
        self.status_label.pack(pady=(5, 15))

        # 6. Secondary Actions
        self.stats_btn = ctk.CTkButton(
            self, text="📊 View My Stats Dashboard", command=self.show_stats,
            fg_color="transparent", border_width=1, border_color=TEXT_SECONDARY,
            text_color=TEXT_MAIN, hover_color=CARD_BG, height=40, corner_radius=8
        )
        self.stats_btn.pack(fill="x", padx=25, pady=5)

        # Logout Button - FIXED: hover_color is now solid color
        self.logout_btn = ctk.CTkButton(
            self, text="Switch User", command=self.logout,
            fg_color="transparent", text_color=BTN_STOP, 
            hover_color=HOVER_GHOST, font=("Outfit", 11, "underline")
        )
        self.logout_btn.pack(pady=(10, 0))

    def logout(self):
        self.is_tracking = False
        config = load_config()
        if 'user_id' in config:
            del config['user_id']
            save_config(config)
        self.destroy()

    # --- MINI MODE ---
    def toggle_mini_mode(self):
        if self.mini_var.get():
            self.withdraw()
            self.create_mini_widget()
        else:
            self.restore_main_window()

    def create_mini_widget(self):
        self.mini_win = ctk.CTkToplevel(self)
        self.mini_win.geometry("260x50")
        self.mini_win.overrideredirect(True) 
        self.mini_win.attributes("-topmost", True) 
        self.mini_win.configure(fg_color=CARD_BG)
        self.mini_win.attributes("-alpha", 0.7) 
        
        self.mini_win.bind("<Enter>", lambda e: self.mini_win.attributes("-alpha", 1.0))
        self.mini_win.bind("<Leave>", lambda e: self.mini_win.attributes("-alpha", 0.7))

        self.mini_win.bind("<ButtonPress-1>", self.start_move)
        self.mini_win.bind("<B1-Motion>", self.do_move)

        main_frame = ctk.CTkFrame(self.mini_win, fg_color="transparent")
        main_frame.pack(fill="both", expand=True, padx=10)

        drag_lbl = ctk.CTkLabel(main_frame, text="⋮⋮", font=("Arial", 16), text_color=TEXT_SECONDARY)
        drag_lbl.pack(side="left", padx=(0, 10))
        drag_lbl.bind("<ButtonPress-1>", self.start_move)
        drag_lbl.bind("<B1-Motion>", self.do_move)

        time_str = "00:00:00"
        if self.session_seconds > 0:
            hrs, rem = divmod(self.session_seconds, 3600)
            mins, secs = divmod(rem, 60)
            time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
        
        self.mini_timer_label = ctk.CTkLabel(main_frame, text=time_str, font=("Courier", 16, "bold"), text_color=THEME_COLOR if self.is_tracking else TEXT_SECONDARY)
        self.mini_timer_label.pack(side="left", padx=5)

        expand_btn = ctk.CTkButton(main_frame, text="⛶", width=30, height=30, fg_color="#374151", hover_color="#4b5563", command=self.restore_main_window)
        expand_btn.pack(side="right", padx=(5, 0))

        btn_color = BTN_PAUSE if self.is_tracking else THEME_COLOR
        btn_txt = "⏸" if self.is_tracking else "▶"
        self.mini_toggle_btn = ctk.CTkButton(main_frame, text=btn_txt, width=30, height=30, fg_color=btn_color, text_color=DARK_BG, command=self.toggle)
        self.mini_toggle_btn.pack(side="right")

    def restore_main_window(self):
        if self.mini_win:
            self.mini_win.destroy()
            self.mini_win = None
        self.mini_var.set(False)
        self.deiconify() 

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.mini_win.winfo_x() + deltax
        y = self.mini_win.winfo_y() + deltay
        self.mini_win.geometry(f"+{x}+{y}")

    def update_timer(self):
        if self.is_tracking:
            self.session_seconds += 1
            hrs, rem = divmod(self.session_seconds, 3600)
            mins, secs = divmod(rem, 60)
            time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
            
            idle_dur = get_idle_duration()
            t_color = "#ffb703" if idle_dur >= IDLE_TIMEOUT_SECONDS else THEME_COLOR
            
            self.timer_label.configure(text=time_str, text_color=t_color)
            if self.mini_win and hasattr(self, 'mini_timer_label') and self.mini_timer_label.winfo_exists():
                self.mini_timer_label.configure(text=time_str, text_color=t_color)
            
            self.after(1000, self.update_timer)

    def initial_fetch(self):
        self.fetch_latest_settings()

    def reset_timer(self):
        self.session_seconds = 0
        self.timer_label.configure(text="00:00:00", text_color=TEXT_MAIN)
        self.status_label.configure(text="Status: Ready to Track", text_color=TEXT_SECONDARY)
        self.btn.configure(text="START TRACKING")
        self.reset_btn.pack_forget()

    def toggle(self):
        self.is_tracking = not self.is_tracking
        if self.is_tracking:
            self.btn.configure(text="PAUSE TRACKING", fg_color=BTN_PAUSE, hover_color="#e67e22", text_color=TEXT_MAIN)
            self.status_label.configure(text="Status: Connecting...", text_color=THEME_COLOR)
            self.project_dropdown.configure(state="disabled") 
            self.reset_btn.pack_forget()
            
            if self.mini_win and hasattr(self, 'mini_toggle_btn') and self.mini_toggle_btn.winfo_exists():
                self.mini_toggle_btn.configure(text="⏸", fg_color=BTN_PAUSE)

            self.update_timer()
            threading.Thread(target=self.run_logic, daemon=True).start()
        else:
            self.btn.configure(text="RESUME TRACKING", fg_color=THEME_COLOR, hover_color="#00b892", text_color=DARK_BG)
            self.status_label.configure(text="Status: Paused", text_color=TEXT_SECONDARY)
            self.project_dropdown.configure(state="normal")
            self.timer_label.configure(text_color=TEXT_SECONDARY)
            self.reset_btn.pack(fill="x", padx=40, pady=(5, 5), before=self.status_label)
            
            if self.mini_win and hasattr(self, 'mini_toggle_btn') and self.mini_toggle_btn.winfo_exists():
                self.mini_toggle_btn.configure(text="▶", fg_color=THEME_COLOR)

    def show_stats(self):
        url = f"{SERVER_URL}/my-dashboard/{USER_ID}"
        multiprocessing.Process(target=open_dashboard, args=(url,)).start()

    def save_offline_log(self, payload):
        logs = []
        if os.path.exists(LOCAL_LOG_FILE):
            try:
                with open(LOCAL_LOG_FILE, 'r') as f: logs = json.load(f)
            except: pass
        logs.append(payload)
        with open(LOCAL_LOG_FILE, 'w') as f: json.dump(logs, f)

    def sync_offline_logs(self):
        if os.path.exists(LOCAL_LOG_FILE):
            try:
                with open(LOCAL_LOG_FILE, 'r') as f: logs = json.load(f)
                if logs:
                    for log in logs: requests.post(f"{SERVER_URL}/api/heartbeat", json=log, timeout=5)
                    os.remove(LOCAL_LOG_FILE)
            except: pass

    def fetch_latest_settings(self):
        global TRACKING_INTERVAL, IDLE_TIMEOUT_SECONDS
        try:
            response = requests.get(f"{SERVER_URL}/api/get_config?user_id={USER_ID}", timeout=5)
            if response.status_code == 200:
                config = response.json()
                TRACKING_INTERVAL = int(config.get('screenshot_interval', 10))
                IDLE_TIMEOUT_SECONDS = int(config.get('idle_timeout', 30))
                self.server_ss_enabled = config.get('screenshots_enabled', True)
                
                projects_from_server = config.get('projects', [])
                if projects_from_server: self.project_dropdown.configure(values=projects_from_server)
                
                server_theme = config.get('theme', 'dark')
                ctk.set_appearance_mode(server_theme)
        except Exception: pass

    def show_nudge(self):
        nudge_win = ctk.CTkToplevel(self)
        nudge_win.title("DeskTime Alert")
        nudge_win.geometry("350x180")
        nudge_win.attributes("-topmost", True)
        nudge_win.configure(fg_color=DARK_BG)
        
        ctk.CTkLabel(nudge_win, text="Are you still working?", font=("Outfit", 20, "bold"), text_color=BTN_STOP).pack(pady=(30, 15))
        
        def close_nudge():
            self.nudge_shown = False
            nudge_win.destroy()
            
        ctk.CTkButton(nudge_win, text="Yes, I am working", command=close_nudge, fg_color=THEME_COLOR, text_color=DARK_BG, height=40).pack()

    def run_logic(self):
        global activity_events
        while self.is_tracking:
            try:
                self.fetch_latest_settings()
                idle_seconds = get_idle_duration()
                
                max_expected_events = TRACKING_INTERVAL * 4 
                activity_pct = int((activity_events / max_expected_events) * 100) if max_expected_events > 0 else 0
                if activity_pct > 100: activity_pct = 100 
                
                if idle_seconds >= IDLE_TIMEOUT_SECONDS:
                    current_status = "idle"
                    app_name = "System Idle / Away"
                    activity_pct = 0 
                    
                    if idle_seconds > (IDLE_TIMEOUT_SECONDS + 30) and not self.nudge_shown:
                        self.nudge_shown = True
                        self.show_nudge()
                else:
                    current_status = "active"
                    self.nudge_shown = False
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                    buff = ctypes.create_unicode_buffer(255)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, 255)
                    app_name = buff.value if buff.value else "Desktop"
                
                activity_events = 0 
                payload = {
                    "user_id": USER_ID, "status": current_status, "app_name": app_name,
                    "project_name": self.project_var.get(), "activity_pct": activity_pct 
                }

                try:
                    requests.post(f"{SERVER_URL}/api/heartbeat", json=payload, timeout=5)
                    self.status_label.configure(text=f"Status: Online ({activity_pct}% Active)", text_color=THEME_COLOR)
                    self.sync_offline_logs()

                    if current_status == "active" and self.server_ss_enabled:
                        timestamp = datetime.now().strftime('%H-%M-%S')
                        img_name = f"{USER_ID}_{timestamp}.png"
                        img_path = os.path.join(tempfile.gettempdir(), img_name)
                        
                        pyautogui.screenshot(img_path)
                        if self.blur_enabled.get():
                            img = Image.open(img_path)
                            img = img.filter(ImageFilter.GaussianBlur(radius=12))
                            img.save(img_path)
                        
                        with open(img_path, 'rb') as f:
                            files = {'screenshot': (img_name, f, 'image/png')}
                            requests.post(f"{SERVER_URL}/api/upload", files=files, timeout=10)
                        os.remove(img_path)

                except requests.exceptions.RequestException:
                    self.status_label.configure(text="Status: Offline (Local Backup)", text_color="#ffb703")
                    self.save_offline_log(payload)

            except Exception: pass
            time.sleep(TRACKING_INTERVAL)

if __name__ == "__main__":
    multiprocessing.freeze_support() 
    
    config = load_config()
    USER_ID = config.get('user_id')
    
    if not USER_ID:
        login_app = LoginApp()
        login_app.mainloop()
        
        # User login hone ke baad hi check karein
        config = load_config()
        USER_ID = config.get('user_id')
        if USER_ID:
            app = TrackerApp()
            app.mainloop()
    else:
        app = TrackerApp()
        app.mainloop()