"""
JH Secure - Portable Folder Lock Application
Version: 2.0.0
Theme: Clean White / User Friendly
"""

import os
import sys
import io
import json
import hashlib
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
import base64
import hmac
import datetime
import threading
import time


# ══════════════════════════════════════════════════════════════════════════════
#  CRYPTO
# ══════════════════════════════════════════════════════════════════════════════

def derive_key(pin: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 200_000, dklen=32)

def hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 200_000).hex()

def hash_answer(answer: str) -> str:
    return hashlib.sha256(answer.strip().lower().encode()).hexdigest()

def xor_encrypt(data: bytes, key: bytes) -> bytes:
    full_key = (key * (len(data) // len(key) + 1))[:len(data)]
    return bytes(a ^ b for a, b in zip(data, full_key))


# ══════════════════════════════════════════════════════════════════════════════
#  COLORS — Clean White Theme
# ══════════════════════════════════════════════════════════════════════════════

C = {
    "bg":         "#F5F7FA",
    "white":      "#FFFFFF",
    "surface":    "#FFFFFF",
    "card":       "#FFFFFF",
    "border":     "#E2E8F0",
    "border2":    "#CBD5E1",
    "blue":       "#3B82F6",
    "blue_dark":  "#2563EB",
    "blue_light": "#EFF6FF",
    "green":      "#10B981",
    "green_light":"#ECFDF5",
    "red":        "#EF4444",
    "red_light":  "#FEF2F2",
    "orange":     "#F59E0B",
    "orange_light":"#FFFBEB",
    "purple":     "#8B5CF6",
    "purple_light":"#F5F3FF",
    "text":       "#1E293B",
    "text2":      "#475569",
    "text3":      "#94A3B8",
    "shadow":     "#00000015",
}

FONT_TITLE  = ("Segoe UI", 22, "bold")
FONT_HEAD   = ("Segoe UI", 13, "bold")
FONT_SUB    = ("Segoe UI", 10)
FONT_LABEL  = ("Segoe UI", 9)
FONT_SMALL  = ("Segoe UI", 8)
FONT_MONO   = ("Consolas", 10)


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

LOCK_FILENAME    = ".jhsecure"
LOG_FILENAME     = ".jhsecure_log"
LOCKOUT_FILENAME = ".jhsecure_lockout"
MAX_ATTEMPTS     = 5
LOCKOUT_SECONDS  = 300

SECRET_QUESTIONS = [
    "What is your mother's maiden name?",
    "What was your first pet's name?",
    "What city were you born in?",
    "What is your childhood nickname?",
    "What was the name of your first school?",
    "What is your favourite book?",
    "What street did you grow up on?",
]

AUTO_LOCK_OPTIONS = {
    "Never":      0,
    "1 Minute":   60,
    "5 Minutes":  300,
    "15 Minutes": 900,
    "30 Minutes": 1800,
    "1 Hour":     3600,
}


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def _log_path(fp): return os.path.join(fp, LOG_FILENAME)
def _lockout_path(fp): return os.path.join(fp, LOCKOUT_FILENAME)

def _load_log(fp):
    try:
        with open(_log_path(fp)) as f: return json.load(f)
    except: return []

def _save_log(fp, entries):
    try:
        with open(_log_path(fp), 'w') as f:
            json.dump(entries[-200:], f, indent=2)
    except: pass

def write_log(fp, event, detail=""):
    e = _load_log(fp)
    e.append({"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "event": event, "detail": detail})
    _save_log(fp, e)


# ══════════════════════════════════════════════════════════════════════════════
#  BRUTE FORCE
# ══════════════════════════════════════════════════════════════════════════════

def get_lockout_info(fp):
    try:
        with open(_lockout_path(fp)) as f: return json.load(f)
    except: return {"attempts": 0, "locked_until": 0}

def save_lockout_info(fp, info):
    try:
        with open(_lockout_path(fp), 'w') as f: json.dump(info, f)
    except: pass

def is_locked_out(fp):
    info = get_lockout_info(fp)
    rem  = int(info.get("locked_until", 0) - time.time())
    return (True, rem) if rem > 0 else (False, 0)

def record_failed(fp):
    info = get_lockout_info(fp)
    if time.time() > info.get("locked_until", 0):
        info["attempts"] = 0
    info["attempts"] = info.get("attempts", 0) + 1
    if info["attempts"] >= MAX_ATTEMPTS:
        info["locked_until"] = time.time() + LOCKOUT_SECONDS
    save_lockout_info(fp, info)
    write_log(fp, "FAILED_ATTEMPT", f"Attempt #{info['attempts']}")
    return info["attempts"]

def reset_lockout(fp):
    save_lockout_info(fp, {"attempts": 0, "locked_until": 0})


# ══════════════════════════════════════════════════════════════════════════════
#  LOCK CORE
# ══════════════════════════════════════════════════════════════════════════════

def get_lock_file(fp): return os.path.join(fp, LOCK_FILENAME)

def is_locked(fp):
    lf = get_lock_file(fp)
    if not os.path.exists(lf): return False
    try:
        with open(lf) as f: return json.load(f).get("locked", False)
    except: return False

def _pack(fp):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(fp):
            dirs[:] = [d for d in dirs
                       if not d.startswith('.jhsecure') and d != "__decoy__"]
            for f in files:
                if f.startswith('.jhsecure'): continue
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, fp))
    return buf.getvalue()

def _remove_originals(fp):
    for root, dirs, files in os.walk(fp, topdown=False):
        dirs[:] = [d for d in dirs if not d.startswith('.jhsecure')]
        for f in files:
            if f.startswith('.jhsecure'): continue
            try: os.remove(os.path.join(root, f))
            except: pass
        if root != fp:
            try:
                if not os.listdir(root): os.rmdir(root)
            except: pass

def create_lock(fp, pin, secret_q, secret_a,
                decoy_pin="", auto_lock_seconds=0, master_pin=""):
    try:
        salt      = os.urandom(32)
        key       = derive_key(pin, salt)
        raw       = _pack(fp)
        encrypted = xor_encrypt(raw, key)

        data = {
            "locked": True, "version": "2.0",
            "salt": base64.b64encode(salt).decode(),
            "pin_hash": hash_pin(pin, salt),
            "secret_question": secret_q,
            "secret_answer_hash": hash_answer(secret_a) if secret_a else "",
            "encrypted_data": base64.b64encode(encrypted).decode(),
            "auto_lock_seconds": auto_lock_seconds,
            "unlocked_at": 0,
        }

        if decoy_pin:
            dec_folder = os.path.join(fp, "__decoy__")
            os.makedirs(dec_folder, exist_ok=True)
            sample = os.path.join(dec_folder, "readme.txt")
            if not os.path.exists(sample):
                with open(sample, 'w') as f2: f2.write("This folder is empty.\n")
            dbuf = io.BytesIO()
            with zipfile.ZipFile(dbuf, 'w') as zf:
                for df in os.listdir(dec_folder):
                    dfp = os.path.join(dec_folder, df)
                    if os.path.isfile(dfp): zf.write(dfp, df)
            dsalt = os.urandom(32)
            dkey  = derive_key(decoy_pin, dsalt)
            data["decoy_salt"]     = base64.b64encode(dsalt).decode()
            data["decoy_pin_hash"] = hash_pin(decoy_pin, dsalt)
            data["decoy_data"]     = base64.b64encode(
                xor_encrypt(dbuf.getvalue(), dkey)).decode()

        if master_pin:
            msalt = os.urandom(32)
            mkey  = derive_key(master_pin, msalt)
            data["master_salt"]     = base64.b64encode(msalt).decode()
            data["master_pin_hash"] = hash_pin(master_pin, msalt)
            data["master_data"]     = base64.b64encode(
                xor_encrypt(raw, mkey)).decode()

        with open(get_lock_file(fp), 'w') as f2:
            json.dump(data, f2, indent=2)

        _remove_originals(fp)
        write_log(fp, "LOCKED", "Folder locked")
        return True
    except Exception as e:
        print(f"Lock error: {e}"); return False

def verify_pin(fp, pin):
    try:
        with open(get_lock_file(fp)) as f: data = json.load(f)
        salt = base64.b64decode(data["salt"])
        if hmac.compare_digest(hash_pin(pin, salt), data["pin_hash"]):
            return 'real'
        if "decoy_pin_hash" in data:
            dsalt = base64.b64decode(data["decoy_salt"])
            if hmac.compare_digest(hash_pin(pin, dsalt), data["decoy_pin_hash"]):
                return 'decoy'
        if "master_pin_hash" in data:
            msalt = base64.b64decode(data["master_salt"])
            if hmac.compare_digest(hash_pin(pin, msalt), data["master_pin_hash"]):
                return 'master'
        return 'wrong'
    except: return 'wrong'

def unlock_folder(fp, pin):
    try:
        with open(get_lock_file(fp)) as f: data = json.load(f)
        pt = verify_pin(fp, pin)
        if pt == 'wrong': return False
        if pt == 'real':
            key = derive_key(pin, base64.b64decode(data["salt"]))
            raw = xor_encrypt(base64.b64decode(data["encrypted_data"]), key)
        elif pt == 'decoy':
            key = derive_key(pin, base64.b64decode(data["decoy_salt"]))
            raw = xor_encrypt(base64.b64decode(data["decoy_data"]), key)
        else:
            key = derive_key(pin, base64.b64decode(data["master_salt"]))
            raw = xor_encrypt(base64.b64decode(data["master_data"]), key)
        with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
            zf.extractall(fp)
        os.remove(get_lock_file(fp))
        reset_lockout(fp)
        write_log(fp, "UNLOCKED", f"via {pt} PIN")
        return True
    except Exception as e:
        print(f"Unlock error: {e}"); return False

def change_pin(fp, old_pin, new_pin):
    try:
        with open(get_lock_file(fp)) as f: data = json.load(f)
        old_salt = base64.b64decode(data["salt"])
        if not hmac.compare_digest(hash_pin(old_pin, old_salt), data["pin_hash"]):
            return False
        raw      = xor_encrypt(base64.b64decode(data["encrypted_data"]),
                               derive_key(old_pin, old_salt))
        new_salt = os.urandom(32)
        new_key  = derive_key(new_pin, new_salt)
        data["salt"]           = base64.b64encode(new_salt).decode()
        data["pin_hash"]       = hash_pin(new_pin, new_salt)
        data["encrypted_data"] = base64.b64encode(xor_encrypt(raw, new_key)).decode()
        with open(get_lock_file(fp), 'w') as f: json.dump(data, f, indent=2)
        write_log(fp, "PIN_CHANGED", "PIN changed")
        return True
    except: return False

def verify_secret(fp, answer):
    try:
        with open(get_lock_file(fp)) as f: data = json.load(f)
        return hmac.compare_digest(hash_answer(answer), data["secret_answer_hash"])
    except: return False

def reset_pin_via_secret(fp, new_pin):
    try:
        with open(get_lock_file(fp)) as f: data = json.load(f)
        old_salt = base64.b64decode(data["salt"])
        old_key  = derive_key(base64.b64encode(old_salt).decode(), old_salt)
        raw      = xor_encrypt(base64.b64decode(data["encrypted_data"]), old_key)
        new_salt = os.urandom(32)
        new_key  = derive_key(new_pin, new_salt)
        data["salt"]           = base64.b64encode(new_salt).decode()
        data["pin_hash"]       = hash_pin(new_pin, new_salt)
        data["encrypted_data"] = base64.b64encode(xor_encrypt(raw, new_key)).decode()
        with open(get_lock_file(fp), 'w') as f: json.dump(data, f, indent=2)
        write_log(fp, "PIN_RESET", "via secret question")
        return True
    except: return False


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-LOCK DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class AutoLockDaemon:
    def __init__(self, app):
        self.app = app
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self): self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            time.sleep(15)
            try: self.app.after(0, self.app.check_auto_locks)
            except: pass


# ══════════════════════════════════════════════════════════════════════════════
#  WIDGETS
# ══════════════════════════════════════════════════════════════════════════════

class PINEntry(tk.Frame):
    def __init__(self, master, length=6, color=None, **kwargs):
        super().__init__(master, bg=C["white"], **kwargs)
        self.length = length
        self._color = color or C["blue"]
        self._var   = tk.StringVar()
        self._dots  = []

        self._entry = tk.Entry(self, textvariable=self._var, show="",
                               width=0, bg=C["white"], fg=C["white"],
                               insertbackground=C["white"],
                               relief="flat", bd=0, highlightthickness=0)
        self._entry.pack()
        self._var.trace_add("write", self._on_change)

        row = tk.Frame(self, bg=C["white"])
        row.pack(pady=4)
        for _ in range(length):
            c = tk.Canvas(row, width=38, height=38,
                          bg=C["white"], highlightthickness=0)
            c.grid(row=0, column=_, padx=4)
            self._draw_dot(c, False)
            self._dots.append(c)

        for w in (self, row, *self._dots):
            w.bind("<Button-1>", lambda e: self._entry.focus_set())

    def _draw_dot(self, c, filled):
        c.delete("all")
        # Outer circle (border)
        c.create_oval(2, 2, 36, 36,
                      fill=self._color if filled else C["white"],
                      outline=self._color if filled else C["border2"],
                      width=2)
        if filled:
            c.create_oval(13, 13, 25, 25, fill=C["white"], outline="")

    def _on_change(self, *_):
        val   = self._var.get()
        clean = ''.join(x for x in val if x.isdigit())[:self.length]
        if clean != val:
            self._var.set(clean)
            self._entry.icursor(len(clean))
            return
        for i, dot in enumerate(self._dots):
            self._draw_dot(dot, i < len(clean))

    def get(self): return self._var.get()
    def clear(self): self._var.set("")
    def focus(self): self._entry.focus_set()


def flat_btn(parent, text, cmd, bg=None, fg=None, w=160, h=40, radius=8):
    bg = bg or C["blue"]
    fg = fg or C["white"]
    c  = tk.Canvas(parent, width=w, height=h,
                   bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")

    def draw(fill, text_col):
        c.delete("all")
        # Rounded rectangle
        r = radius
        c.create_arc(0, 0, 2*r, 2*r, start=90, extent=90, fill=fill, outline=fill)
        c.create_arc(w-2*r, 0, w, 2*r, start=0, extent=90, fill=fill, outline=fill)
        c.create_arc(0, h-2*r, 2*r, h, start=180, extent=90, fill=fill, outline=fill)
        c.create_arc(w-2*r, h-2*r, w, h, start=270, extent=90, fill=fill, outline=fill)
        c.create_rectangle(r, 0, w-r, h, fill=fill, outline=fill)
        c.create_rectangle(0, r, w, h-r, fill=fill, outline=fill)
        c.create_text(w//2, h//2, text=text, fill=text_col,
                      font=("Segoe UI", 9, "bold"))

    def darken(color):
        # Simple darken for hover
        try:
            r2 = int(color[1:3], 16)
            g2 = int(color[3:5], 16)
            b2 = int(color[5:7], 16)
            r2 = max(0, r2 - 20)
            g2 = max(0, g2 - 20)
            b2 = max(0, b2 - 20)
            return f"#{r2:02x}{g2:02x}{b2:02x}"
        except:
            return color

    draw(bg, fg)
    c.bind("<Enter>",    lambda e: draw(darken(bg), fg))
    c.bind("<Leave>",    lambda e: draw(bg, fg))
    c.bind("<Button-1>", lambda e: cmd())
    return c


def card(parent, pady=0, padx=0):
    """White card with subtle shadow border."""
    outer = tk.Frame(parent, bg=C["border"], bd=0)
    inner = tk.Frame(outer, bg=C["white"], padx=padx or 20, pady=pady or 16)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    return outer, inner


def section_label(parent, text, color=None):
    tk.Label(parent, text=text, font=("Segoe UI", 8, "bold"),
             fg=color or C["text2"], bg=C["white"]).pack(anchor="w", pady=(12, 4))


def shake_window(win):
    x, y = win.winfo_x(), win.winfo_y()
    for dx in [8, -8, 6, -6, 4, -4, 0]:
        win.geometry(f"+{x+dx}+{y}")
        win.update(); time.sleep(0.025)


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class BaseDialog(tk.Toplevel):
    def __init__(self, master, title, w=440, h=520):
        super().__init__(master)
        self.title(title)
        self.configure(bg=C["bg"])
        self.resizable(False, False)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self._build_titlebar(title)
        self.lift(); self.focus_force()

    def _build_titlebar(self, title):
        bar = tk.Frame(self, bg=C["white"], height=56)
        bar.pack(fill="x"); bar.pack_propagate(False)

        # Blue left accent
        tk.Frame(bar, bg=C["blue"], width=4).pack(side="left", fill="y")

        # Icon + title
        tk.Label(bar, text="🔒", font=("Segoe UI Emoji", 16),
                 bg=C["white"], fg=C["blue"]).pack(side="left", padx=(14, 8))
        tk.Label(bar, text=title, font=("Segoe UI", 12, "bold"),
                 fg=C["text"], bg=C["white"]).pack(side="left")

        # Separator
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")


# ══════════════════════════════════════════════════════════════════════════════
#  LOCK SETUP
# ══════════════════════════════════════════════════════════════════════════════

class LockSetupWindow(BaseDialog):
    def __init__(self, master, fp, on_success=None):
        super().__init__(master, "Lock Folder", w=480, h=700)
        self.fp         = fp
        self.on_success = on_success
        self._build()

    def _build(self):
        # Scrollable
        outer = tk.Canvas(self, bg=C["bg"], highlightthickness=0)
        sb    = tk.Scrollbar(self, orient="vertical", command=outer.yview)
        outer.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        outer.pack(fill="both", expand=True)
        body = tk.Frame(outer, bg=C["bg"])
        wid  = outer.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: outer.configure(scrollregion=outer.bbox("all")))
        outer.bind("<Configure>",
                   lambda e: outer.itemconfig(wid, width=e.width))

        P = dict(padx=20)

        # Folder name banner
        banner = tk.Frame(body, bg=C["blue_light"])
        banner.pack(fill="x", **P, pady=(16, 0))
        tk.Label(banner, text=f"📁  {Path(self.fp).name}",
                 font=("Segoe UI", 10, "bold"),
                 fg=C["blue_dark"], bg=C["blue_light"],
                 pady=10, padx=14).pack(anchor="w")

        # ── PIN card ──────────────────────────────────────────────────
        c_out, c_in = card(body, pady=16, padx=20)
        c_out.pack(fill="x", **P, pady=(12, 0))

        tk.Label(c_in, text="Set Your PIN",
                 font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in, text="6-digit PIN to protect this folder",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")

        section_label(c_in, "ENTER PIN")
        self.pin1 = PINEntry(c_in, length=6)
        self.pin1.pack(anchor="w", pady=(0, 4))
        self.pin1.focus()

        section_label(c_in, "CONFIRM PIN")
        self.pin2 = PINEntry(c_in, length=6)
        self.pin2.pack(anchor="w")

        # ── Secret Question card ──────────────────────────────────────
        c_out2, c_in2 = card(body, pady=16, padx=20)
        c_out2.pack(fill="x", **P, pady=(12, 0))

        tk.Label(c_in2, text="Recovery Setup",
                 font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in2, text="In case you forget your PIN",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")

        section_label(c_in2, "SECRET QUESTION")
        self.q_var = tk.StringVar(value=SECRET_QUESTIONS[0])
        style = ttk.Style(); style.theme_use("clam")
        style.configure("W.TCombobox",
                        fieldbackground=C["bg"],
                        background=C["white"],
                        foreground=C["text"],
                        bordercolor=C["border2"],
                        arrowcolor=C["blue"])
        cb = ttk.Combobox(c_in2, textvariable=self.q_var,
                          values=SECRET_QUESTIONS, state="readonly",
                          style="W.TCombobox", width=46)
        cb.pack(anchor="w", pady=(0, 8))

        section_label(c_in2, "YOUR ANSWER")
        self.ans_e = tk.Entry(c_in2, show="•", bg=C["bg"],
                              fg=C["text"], insertbackground=C["blue"],
                              relief="flat", bd=0, font=("Segoe UI", 11),
                              highlightthickness=1,
                              highlightbackground=C["border2"],
                              highlightcolor=C["blue"])
        self.ans_e.pack(fill="x", ipady=8, pady=(0, 4))

        # ── Advanced Options (collapsible feel) ───────────────────────
        c_out3, c_in3 = card(body, pady=16, padx=20)
        c_out3.pack(fill="x", **P, pady=(12, 0))

        tk.Label(c_in3, text="Advanced Options",
                 font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in3, text="Optional — leave blank to skip",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")

        # Decoy PIN
        section_label(c_in3, "👻  FAKE PASSWORD  (optional)")
        tk.Label(c_in3,
                 text="Enter this PIN under pressure → shows empty folder",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")
        self.decoy_pin = PINEntry(c_in3, color=C["orange"])
        self.decoy_pin.pack(anchor="w", pady=(4, 0))

        # Master PIN
        section_label(c_in3, "🗝️  MASTER PIN  (optional)")
        tk.Label(c_in3,
                 text="One PIN to unlock all your JH Secure folders",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")
        self.master_pin = PINEntry(c_in3, color=C["purple"])
        self.master_pin.pack(anchor="w", pady=(4, 0))

        # Auto Lock
        section_label(c_in3, "⏱️  AUTO-LOCK TIMER")
        self.auto_var = tk.StringVar(value="Never")
        row = tk.Frame(c_in3, bg=C["white"]); row.pack(anchor="w", pady=(4, 0))
        for opt in AUTO_LOCK_OPTIONS:
            rb = tk.Radiobutton(row, text=opt, variable=self.auto_var,
                                value=opt, font=FONT_SMALL,
                                fg=C["text2"], bg=C["white"],
                                selectcolor=C["white"],
                                activeforeground=C["blue"],
                                activebackground=C["white"],
                                relief="flat")
            rb.pack(side="left", padx=(0, 10))

        # Error + button
        self.err = tk.Label(body, text="", font=FONT_SMALL,
                            fg=C["red"], bg=C["bg"])
        self.err.pack(**P, pady=(8, 0))

        btn = flat_btn(body, "🔒  Lock Folder", self._do,
                       bg=C["blue"], w=440, h=48)
        btn.pack(**P, pady=(6, 24))

    def _do(self):
        p1     = self.pin1.get()
        p2     = self.pin2.get()
        ans    = self.ans_e.get().strip()
        decoy  = self.decoy_pin.get()
        master = self.master_pin.get()
        auto_s = AUTO_LOCK_OPTIONS[self.auto_var.get()]

        if len(p1) < 4:
            self.err.config(text="⚠ PIN must be at least 4 digits"); return
        if p1 != p2:
            self.pin2.clear()
            self.err.config(text="⚠ PINs do not match"); return
        if not ans:
            self.err.config(text="⚠ Secret answer is required"); return
        if decoy and decoy == p1:
            self.err.config(text="⚠ Fake PIN must be different from real PIN"); return

        self.err.config(text="Locking folder…", fg=C["orange"])
        self.update()

        ok = create_lock(self.fp, p1, self.q_var.get(), ans,
                         decoy_pin=decoy, auto_lock_seconds=auto_s,
                         master_pin=master)
        if ok:
            if self.on_success: self.on_success()
            self.destroy()
        else:
            self.err.config(text="⚠ Lock failed. Try again.", fg=C["red"])


# ══════════════════════════════════════════════════════════════════════════════
#  UNLOCK WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class UnlockWindow(BaseDialog):
    def __init__(self, master, fp, on_success=None):
        super().__init__(master, "Unlock Folder", w=400, h=460)
        self.fp         = fp
        self.on_success = on_success
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()

    def _build(self):
        for w in self.winfo_children():
            if not isinstance(w, tk.Frame) or w.cget("bg") == C["white"]:
                pass

        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True)

        # Folder info
        info = tk.Frame(body, bg=C["blue_light"])
        info.pack(fill="x", padx=20, pady=(20, 0))
        tk.Label(info, text=f"📁  {Path(self.fp).name}",
                 font=("Segoe UI", 10, "bold"),
                 fg=C["blue_dark"], bg=C["blue_light"],
                 pady=10, padx=14).pack(anchor="w")

        # Check lockout
        lo, rem = is_locked_out(self.fp)
        if lo:
            c_out, c_in = card(body, pady=20, padx=20)
            c_out.pack(fill="x", padx=20, pady=12)
            tk.Label(c_in, text="🔴  Too Many Attempts",
                     font=("Segoe UI", 12, "bold"),
                     fg=C["red"], bg=C["white"]).pack()
            self.lock_lbl = tk.Label(c_in,
                     text=f"Try again in {rem//60}m {rem%60:02d}s",
                     font=("Segoe UI", 10), fg=C["text2"],
                     bg=C["white"])
            self.lock_lbl.pack(pady=8)
            self._tick(rem)
            return

        # PIN card
        c_out, c_in = card(body, pady=20, padx=20)
        c_out.pack(fill="x", padx=20, pady=12)

        tk.Label(c_in, text="Enter your PIN",
                 font=("Segoe UI", 12, "bold"),
                 fg=C["text"], bg=C["white"]).pack()
        tk.Label(c_in, text="Enter the 6-digit PIN to unlock",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(pady=(2, 12))

        self.pin_e = PINEntry(c_in, length=6)
        self.pin_e.pack(pady=(0, 8))
        self.pin_e.focus()

        self.status = tk.Label(c_in, text="", font=FONT_SMALL,
                               fg=C["red"], bg=C["white"])
        self.status.pack(pady=(0, 4))

        flat_btn(c_in, "Unlock →", self._do,
                 bg=C["green"], w=360, h=44).pack(pady=(4, 0))

        # Links
        link_row = tk.Frame(body, bg=C["bg"])
        link_row.pack(pady=(8, 0))
        for txt, cmd in [("Forgot PIN?", self._forgot),
                         ("Change PIN",  self._change)]:
            lbl = tk.Label(link_row, text=txt, font=("Segoe UI", 9, "underline"),
                           fg=C["blue"], bg=C["bg"], cursor="hand2")
            lbl.pack(side="left", padx=14)
            lbl.bind("<Button-1>", lambda e, c=cmd: c())

        self.bind("<Return>", lambda e: self._do())

    def _tick(self, r):
        if r <= 0:
            self.destroy()
            UnlockWindow(self.master, self.fp, self.on_success)
            return
        try:
            self.lock_lbl.config(
                text=f"Try again in {r//60}m {r%60:02d}s")
            self.after(1000, self._tick, r - 1)
        except: pass

    def _do(self):
        lo, _ = is_locked_out(self.fp)
        if lo: return
        pin = self.pin_e.get()
        if len(pin) < 4:
            shake_window(self); return
        if verify_pin(self.fp, pin) != 'wrong':
            if unlock_folder(self.fp, pin):
                if self.on_success: self.on_success()
                self.destroy()
            else:
                self.status.config(text="⚠ Decryption failed.")
        else:
            attempts = record_failed(self.fp)
            self.pin_e.clear()
            left = MAX_ATTEMPTS - attempts
            if left <= 0:
                self.destroy()
                UnlockWindow(self.master, self.fp, self.on_success)
            else:
                self.status.config(
                    text=f"⚠ Wrong PIN — {left} attempt(s) remaining")
                shake_window(self)

    def _forgot(self):
        RecoveryWindow(self, self.fp,
                       on_success=lambda: (
                           self.on_success() if self.on_success else None,
                           self.destroy()))

    def _change(self):
        ChangePINWindow(self, self.fp)


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGE PIN
# ══════════════════════════════════════════════════════════════════════════════

class ChangePINWindow(BaseDialog):
    def __init__(self, master, fp, on_success=None):
        super().__init__(master, "Change PIN", w=400, h=440)
        self.fp         = fp
        self.on_success = on_success
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True)

        c_out, c_in = card(body, pady=20, padx=20)
        c_out.pack(fill="x", padx=20, pady=20)

        tk.Label(c_in, text="Change Your PIN",
                 font=("Segoe UI", 12, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in, text="Enter current PIN to set a new one",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")

        for label, attr, col in [
            ("CURRENT PIN", "old_pin", C["blue"]),
            ("NEW PIN",     "new_pin", C["green"]),
            ("CONFIRM NEW", "confirm", C["green"]),
        ]:
            section_label(c_in, label)
            pe = PINEntry(c_in, color=col)
            pe.pack(anchor="w", pady=(0, 4))
            setattr(self, attr, pe)

        self.old_pin.focus()
        self.err = tk.Label(c_in, text="", font=FONT_SMALL,
                            fg=C["red"], bg=C["white"])
        self.err.pack(pady=(8, 0))
        flat_btn(c_in, "Change PIN ✓", self._do,
                 bg=C["blue"], w=360, h=44).pack(pady=(8, 0))

    def _do(self):
        old = self.old_pin.get()
        new = self.new_pin.get()
        con = self.confirm.get()
        if len(new) < 4:
            self.err.config(text="⚠ New PIN too short"); return
        if new != con:
            self.confirm.clear()
            self.err.config(text="⚠ PINs do not match"); return
        if change_pin(self.fp, old, new):
            if self.on_success: self.on_success()
            messagebox.showinfo("Done", "PIN changed successfully!", parent=self)
            self.destroy()
        else:
            self.old_pin.clear()
            self.err.config(text="⚠ Wrong current PIN")


# ══════════════════════════════════════════════════════════════════════════════
#  RECOVERY
# ══════════════════════════════════════════════════════════════════════════════

class RecoveryWindow(BaseDialog):
    def __init__(self, master, fp, on_success=None):
        super().__init__(master, "Forgot PIN", w=420, h=460)
        self.fp         = fp
        self.on_success = on_success
        try:
            with open(get_lock_file(fp)) as f:
                self.question = json.load(f).get("secret_question", "N/A")
        except: self.question = "N/A"
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True)
        self._step1()

    def _clear(self):
        for w in self.body.winfo_children(): w.destroy()

    def _step1(self):
        self._clear()
        c_out, c_in = card(self.body, pady=20, padx=20)
        c_out.pack(fill="x", padx=20, pady=20)

        tk.Label(c_in, text="Account Recovery",
                 font=("Segoe UI", 12, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in, text="Answer your secret question to reset PIN",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")

        section_label(c_in, "SECRET QUESTION")
        tk.Label(c_in, text=self.question, font=("Segoe UI", 10),
                 fg=C["blue_dark"], bg=C["white"],
                 wraplength=360, justify="left").pack(anchor="w", pady=(0, 8))

        section_label(c_in, "YOUR ANSWER")
        self.ans = tk.Entry(c_in, show="•", bg=C["bg"],
                            fg=C["text"], insertbackground=C["blue"],
                            relief="flat", bd=0, font=("Segoe UI", 11),
                            highlightthickness=1,
                            highlightbackground=C["border2"],
                            highlightcolor=C["blue"])
        self.ans.pack(fill="x", ipady=8, pady=(0, 8))
        self.ans.focus()

        self.err = tk.Label(c_in, text="", font=FONT_SMALL,
                            fg=C["red"], bg=C["white"])
        self.err.pack(pady=(0, 4))

        flat_btn(c_in, "Verify Answer →", self._verify,
                 bg=C["orange"], w=360, h=44).pack()
        self.bind("<Return>", lambda e: self._verify())

    def _verify(self):
        if verify_secret(self.fp, self.ans.get()):
            self._step2()
        else:
            self.ans.delete(0, "end")
            self.err.config(text="⚠ Wrong answer. Try again.")

    def _step2(self):
        self._clear()
        self.unbind("<Return>")
        c_out, c_in = card(self.body, pady=20, padx=20)
        c_out.pack(fill="x", padx=20, pady=20)

        tk.Label(c_in, text="Set New PIN",
                 font=("Segoe UI", 12, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in, text="Your identity is verified",
                 font=FONT_SMALL, fg=C["green"], bg=C["white"]).pack(anchor="w")

        section_label(c_in, "NEW PIN")
        self.np = PINEntry(c_in, color=C["green"]); self.np.pack(anchor="w", pady=(0, 8))
        self.np.focus()

        section_label(c_in, "CONFIRM NEW PIN")
        self.cp = PINEntry(c_in, color=C["green"]); self.cp.pack(anchor="w")

        self.err2 = tk.Label(c_in, text="", font=FONT_SMALL,
                             fg=C["red"], bg=C["white"])
        self.err2.pack(pady=(8, 4))
        flat_btn(c_in, "Reset PIN ✓", self._reset,
                 bg=C["green"], w=360, h=44).pack()

    def _reset(self):
        p1, p2 = self.np.get(), self.cp.get()
        if len(p1) < 4:
            self.err2.config(text="⚠ Too short"); return
        if p1 != p2:
            self.cp.clear()
            self.err2.config(text="⚠ PINs do not match"); return
        if reset_pin_via_secret(self.fp, p1):
            messagebox.showinfo("Done", "PIN reset! Use your new PIN.", parent=self)
            if self.on_success: self.on_success()
            self.destroy()
        else:
            self.err2.config(text="⚠ Reset failed")


# ══════════════════════════════════════════════════════════════════════════════
#  LOG VIEWER
# ══════════════════════════════════════════════════════════════════════════════

class LogViewerWindow(BaseDialog):
    def __init__(self, master, fp):
        super().__init__(master, "Access Log", w=560, h=520)
        self.fp = fp
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text="Access Log",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(anchor="w")
        tk.Label(body, text=f"📁  {Path(self.fp).name}",
                 font=FONT_SMALL, fg=C["text3"],
                 bg=C["bg"]).pack(anchor="w", pady=(2, 12))

        cols = ("Time", "Event", "Detail")
        tv   = ttk.Treeview(body, columns=cols, show="headings", height=18)

        s = ttk.Style()
        s.configure("Log.Treeview",
                    background=C["white"], foreground=C["text"],
                    fieldbackground=C["white"], rowheight=28,
                    font=("Segoe UI", 9))
        s.configure("Log.Treeview.Heading",
                    background=C["bg"], foreground=C["text2"],
                    font=("Segoe UI", 8, "bold"))
        s.map("Log.Treeview",
              background=[("selected", C["blue_light"])],
              foreground=[("selected", C["blue_dark"])])
        tv.configure(style="Log.Treeview")

        for col, w in zip(cols, [165, 150, 210]):
            tv.heading(col, text=col.upper())
            tv.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)

        entries = _load_log(self.fp)
        if not entries:
            tv.insert("", "end", values=("—", "No entries yet", ""))
        else:
            for e in reversed(entries):
                tv.insert("", "end",
                          values=(e["time"], e["event"], e.get("detail", "")))

        flat_btn(body, "Clear Log", self._clear,
                 bg=C["red"], fg=C["white"], w=120, h=34).pack(
                     anchor="e", pady=(10, 0))

    def _clear(self):
        if messagebox.askyesno("Clear Log", "Delete all log entries?",
                               parent=self):
            _save_log(self.fp, [])
            self.destroy()
            LogViewerWindow(self.master, self.fp)


# ══════════════════════════════════════════════════════════════════════════════
#  BATCH UNLOCK
# ══════════════════════════════════════════════════════════════════════════════

class BatchUnlockWindow(BaseDialog):
    def __init__(self, master, folders, on_done=None):
        super().__init__(master, "Batch Unlock", w=460, h=500)
        self.folders = [f for f in folders if is_locked(f)]
        self.on_done = on_done
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text="Batch Unlock",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(anchor="w")
        tk.Label(body, text=f"{len(self.folders)} locked folder(s) selected",
                 font=FONT_SMALL, fg=C["text3"],
                 bg=C["bg"]).pack(anchor="w", pady=(2, 12))

        # Folder list
        lf = tk.Frame(body, bg=C["border"], bd=0)
        lf.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(lf, bg=C["white"])
        inner.pack(fill="both", padx=1, pady=1)
        for fp in self.folders:
            row = tk.Frame(inner, bg=C["white"])
            row.pack(fill="x")
            tk.Label(row, text="🔒", font=("Segoe UI Emoji", 10),
                     bg=C["white"], fg=C["orange"]).pack(side="left", padx=(12, 4), pady=6)
            tk.Label(row, text=Path(fp).name,
                     font=("Segoe UI", 9), fg=C["text"],
                     bg=C["white"]).pack(side="left", pady=6)
            tk.Frame(inner, bg=C["border"], height=1).pack(fill="x")

        c_out, c_in = card(body, pady=16, padx=16)
        c_out.pack(fill="x", pady=(0, 12))
        tk.Label(c_in, text="Enter Master PIN",
                 font=("Segoe UI", 10, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w")
        tk.Label(c_in, text="The master PIN you set when locking",
                 font=FONT_SMALL, fg=C["text3"], bg=C["white"]).pack(anchor="w")
        self.mpin = PINEntry(c_in, color=C["purple"])
        self.mpin.pack(anchor="w", pady=(8, 0))
        self.mpin.focus()

        self.res = tk.Label(body, text="", font=FONT_SMALL,
                            fg=C["text2"], bg=C["bg"], justify="left")
        self.res.pack(anchor="w", pady=(0, 8))

        flat_btn(body, "Unlock All Folders", self._do,
                 bg=C["purple"], w=420, h=46).pack()

    def _do(self):
        pin = self.mpin.get()
        if len(pin) < 4: shake_window(self); return
        ok = fail = 0; lines = []
        for fp in self.folders:
            pt = verify_pin(fp, pin)
            if pt in ('real', 'master'):
                if unlock_folder(fp, pin):
                    ok += 1; lines.append(f"✅  {Path(fp).name}")
                else:
                    fail += 1; lines.append(f"❌  {Path(fp).name}")
            else:
                fail += 1; lines.append(f"⛔  {Path(fp).name}  (wrong PIN)")
        self.res.config(
            text="\n".join(lines),
            fg=C["green"] if fail == 0 else C["orange"])
        if ok > 0 and self.on_done: self.on_done()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

class JHSecureApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JH Secure")
        self.configure(bg=C["bg"])
        self.resizable(True, True)
        self.minsize(580, 660)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"640x740+{(sw-640)//2}+{(sh-740)//2}")

        self.current_folder = None
        self.recent_folders = []
        self.batch_folders  = []

        self._setup_styles()
        self._build_ui()
        self.recent_folders = self._load_recent()
        self._refresh_list()
        self._daemon = AutoLockDaemon(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._daemon.stop(); self.destroy()

    def _setup_styles(self):
        s = ttk.Style(); s.theme_use("clam")
        s.configure("Tab.TNotebook", background=C["bg"], borderwidth=0)
        s.configure("Tab.TNotebook.Tab",
                    background=C["bg"], foreground=C["text2"],
                    padding=[16, 8], font=("Segoe UI", 9, "bold"))
        s.map("Tab.TNotebook.Tab",
              background=[("selected", C["white"])],
              foreground=[("selected", C["blue"])])

    def _build_ui(self):
        # ── Top header ───────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=C["white"], height=70)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        # Blue sidebar accent
        tk.Frame(hdr, bg=C["blue"], width=5).pack(side="left", fill="y")

        # App icon + name
        icon_frame = tk.Frame(hdr, bg=C["blue"], width=70)
        icon_frame.pack(side="left", fill="y")
        icon_frame.pack_propagate(False)
        tk.Label(icon_frame, text="🔒", font=("Segoe UI Emoji", 24),
                 bg=C["blue"], fg=C["white"]).pack(expand=True)

        title_frame = tk.Frame(hdr, bg=C["white"])
        title_frame.pack(side="left", fill="both", expand=True, padx=16)
        tk.Label(title_frame, text="JH Secure",
                 font=("Segoe UI", 18, "bold"),
                 fg=C["text"], bg=C["white"]).pack(anchor="w", pady=(12, 0))
        tk.Label(title_frame, text="Portable Folder Lock  •  v2.0",
                 font=("Segoe UI", 9), fg=C["text3"],
                 bg=C["white"]).pack(anchor="w")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

        # ── Notebook tabs ────────────────────────────────────────────────────
        nb = ttk.Notebook(self, style="Tab.TNotebook")
        nb.pack(fill="both", expand=True, padx=0, pady=0)

        home_page  = tk.Frame(nb, bg=C["bg"])
        batch_page = tk.Frame(nb, bg=C["bg"])
        log_page   = tk.Frame(nb, bg=C["bg"])

        nb.add(home_page,  text="  🏠  Home  ")
        nb.add(batch_page, text="  📁  Batch  ")
        nb.add(log_page,   text="  📋  Log  ")

        nb.bind("<<NotebookTabChanged>>",
                lambda e: self._on_tab_change(nb.index(nb.select())))

        self._build_home(home_page)
        self._build_batch(batch_page)
        self._build_log(log_page)

    def _on_tab_change(self, idx):
        if idx == 2:
            self._refresh_log_tab()

    # ── HOME ─────────────────────────────────────────────────────────────────

    def _build_home(self, page):
        scroll_c = tk.Canvas(page, bg=C["bg"], highlightthickness=0)
        sb = tk.Scrollbar(page, orient="vertical", command=scroll_c.yview)
        scroll_c.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        scroll_c.pack(fill="both", expand=True)
        body = tk.Frame(scroll_c, bg=C["bg"])
        wid  = scroll_c.create_window((0,0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: scroll_c.configure(scrollregion=scroll_c.bbox("all")))
        scroll_c.bind("<Configure>",
                      lambda e: scroll_c.itemconfig(wid, width=e.width))

        P = dict(padx=20)

        # ── Folder picker ────────────────────────────────────────────────────
        c_out, c_in = card(body, pady=14, padx=16)
        c_out.pack(fill="x", **P, pady=(20, 0))

        top = tk.Frame(c_in, bg=C["white"])
        top.pack(fill="x")
        tk.Label(top, text="Select Folder",
                 font=("Segoe UI", 11, "bold"),
                 fg=C["text"], bg=C["white"]).pack(side="left")
        flat_btn(top, "Browse…", self._browse,
                 bg=C["blue"], w=100, h=32).pack(side="right")

        self.folder_label = tk.Label(c_in,
                                     text="No folder selected — click Browse to start",
                                     font=("Segoe UI", 9), fg=C["text3"],
                                     bg=C["white"], anchor="w",
                                     wraplength=500)
        self.folder_label.pack(anchor="w", pady=(8, 0))

        # ── Status card ──────────────────────────────────────────────────────
        self.status_card_out, self.status_card = card(body, pady=16, padx=16)
        self.status_card_out.pack(fill="x", **P, pady=(12, 0))
        self._draw_status(None)

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = tk.Frame(body, bg=C["bg"])
        btn_row.pack(fill="x", **P, pady=(14, 0))

        btns = [
            ("🔒  Lock",    self._lock_action,   C["blue"],   138),
            ("🔓  Unlock",  self._unlock_action, C["green"],  138),
            ("🔑  Change",  self._change_pin,    C["purple"], 120),
            ("📋  Log",     self._view_log,      C["text2"],  100),
        ]
        for txt, cmd, col, w in btns:
            flat_btn(btn_row, txt, cmd, bg=col, w=w, h=44).pack(
                side="left", padx=(0, 8))

        # ── Recent folders ───────────────────────────────────────────────────
        tk.Label(body, text="Recent Folders",
                 font=("Segoe UI", 10, "bold"),
                 fg=C["text2"], bg=C["bg"]).pack(anchor="w",
                                                 padx=20, pady=(20, 6))

        list_out = tk.Frame(body, bg=C["border"])
        list_out.pack(fill="x", **P, pady=(0, 24))
        list_in  = tk.Frame(list_out, bg=C["white"])
        list_in.pack(fill="both", padx=1, pady=1)

        sb2 = tk.Scrollbar(list_in, orient="vertical", bg=C["border"])
        self.folder_list = tk.Listbox(list_in,
                                      bg=C["white"], fg=C["text"],
                                      selectbackground=C["blue_light"],
                                      selectforeground=C["blue_dark"],
                                      font=("Segoe UI", 9),
                                      relief="flat", bd=0,
                                      yscrollcommand=sb2.set,
                                      activestyle="none",
                                      highlightthickness=0,
                                      height=10)
        sb2.config(command=self.folder_list.yview)
        sb2.pack(side="right", fill="y")
        self.folder_list.pack(fill="both", expand=True)
        self.folder_list.bind("<<ListboxSelect>>", self._on_sel)
        self.folder_list.bind("<Double-Button-1>",  self._on_dbl)

    def _draw_status(self, path):
        for w in self.status_card.winfo_children():
            w.destroy()

        if not path:
            tk.Label(self.status_card,
                     text="⬤  No folder selected",
                     font=("Segoe UI", 10), fg=C["text3"],
                     bg=C["white"]).pack(anchor="w")
            return

        locked = is_locked(path)
        if locked:
            bg_color   = C["red_light"]
            dot_color  = C["red"]
            state_text = "Locked"
            icon       = "🔒"
        else:
            bg_color   = C["green_light"]
            dot_color  = C["green"]
            state_text = "Unlocked"
            icon       = "🔓"

        # Colored status banner
        banner = tk.Frame(self.status_card, bg=bg_color)
        banner.pack(fill="x")
        tk.Label(banner, text=f"  {icon}  {state_text}  —  {Path(path).name}",
                 font=("Segoe UI", 10, "bold"),
                 fg=dot_color, bg=bg_color,
                 pady=10, anchor="w").pack(fill="x")

        # Auto-lock info
        lf = get_lock_file(path)
        if not locked and os.path.exists(lf):
            try:
                with open(lf) as f: d = json.load(f)
                als = d.get("auto_lock_seconds", 0)
                uat = d.get("unlocked_at", 0)
                if als and uat:
                    rem = int((uat + als) - time.time())
                    if rem > 0:
                        tk.Label(self.status_card,
                                 text=f"  ⏱  Auto-lock in {rem//60}m {rem%60:02d}s",
                                 font=("Segoe UI", 8), fg=C["orange"],
                                 bg=C["white"]).pack(anchor="w", pady=(6, 0))
            except: pass

        # Lockout warning
        lo, rem2 = is_locked_out(path)
        if lo:
            tk.Label(self.status_card,
                     text=f"  ⛔  Locked out — retry in {rem2//60}m {rem2%60:02d}s",
                     font=("Segoe UI", 8), fg=C["red"],
                     bg=C["white"]).pack(anchor="w", pady=(4, 0))

    # ── BATCH ─────────────────────────────────────────────────────────────────

    def _build_batch(self, page):
        body = tk.Frame(page, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(body, text="Batch Operations",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(anchor="w")
        tk.Label(body, text="Manage multiple folders at once",
                 font=FONT_SMALL, fg=C["text3"],
                 bg=C["bg"]).pack(anchor="w", pady=(2, 14))

        # Folder list
        list_out = tk.Frame(body, bg=C["border"])
        list_out.pack(fill="both", expand=True)
        list_in  = tk.Frame(list_out, bg=C["white"])
        list_in.pack(fill="both", padx=1, pady=1)

        sb = tk.Scrollbar(list_in, orient="vertical")
        self.batch_list = tk.Listbox(list_in, bg=C["white"],
                                     fg=C["text"],
                                     selectbackground=C["blue_light"],
                                     selectforeground=C["blue_dark"],
                                     font=("Segoe UI", 9), relief="flat",
                                     bd=0, yscrollcommand=sb.set,
                                     activestyle="none",
                                     highlightthickness=0,
                                     selectmode="multiple")
        sb.config(command=self.batch_list.yview)
        sb.pack(side="right", fill="y")
        self.batch_list.pack(fill="both", expand=True)

        r1 = tk.Frame(body, bg=C["bg"]); r1.pack(fill="x", pady=(10, 0))
        flat_btn(r1, "+ Add Folder",  self._batch_add,
                 bg=C["blue"],    w=140, h=38).pack(side="left", padx=(0, 8))
        flat_btn(r1, "✕ Remove",     self._batch_remove,
                 bg=C["text3"],   w=110, h=38).pack(side="left")

        tk.Frame(body, bg=C["border"], height=1).pack(fill="x", pady=14)

        r2 = tk.Frame(body, bg=C["bg"]); r2.pack(fill="x")
        flat_btn(r2, "🔒  Lock All",    self._batch_lock,
                 bg=C["blue"],   w=160, h=44).pack(side="left", padx=(0, 10))
        flat_btn(r2, "🗝️  Batch Unlock", self._batch_unlock,
                 bg=C["purple"], w=180, h=44).pack(side="left")

        self.batch_status = tk.Label(body, text="", font=FONT_SMALL,
                                     fg=C["text2"], bg=C["bg"], justify="left")
        self.batch_status.pack(anchor="w", pady=(10, 0))

    def _batch_add(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path and path not in self.batch_folders:
            self.batch_folders.append(path)
            self._refresh_batch()

    def _batch_remove(self):
        for i in reversed(list(self.batch_list.curselection())):
            self.batch_folders.pop(i)
        self._refresh_batch()

    def _refresh_batch(self):
        self.batch_list.delete(0, "end")
        for fp in self.batch_folders:
            icon = "🔒" if is_locked(fp) else "🔓"
            self.batch_list.insert("end", f"  {icon}  {Path(fp).name}  —  {fp}")

    def _batch_lock(self):
        unlocked = [f for f in self.batch_folders if not is_locked(f)]
        if not unlocked:
            self.batch_status.config(text="No unlocked folders to lock."); return
        for fp in unlocked:
            LockSetupWindow(self, fp, on_success=self._refresh_batch)

    def _batch_unlock(self):
        locked = [f for f in self.batch_folders if is_locked(f)]
        if not locked:
            self.batch_status.config(text="No locked folders."); return
        BatchUnlockWindow(self, locked, on_done=self._refresh_batch)

    # ── LOG ───────────────────────────────────────────────────────────────────

    def _build_log(self, page):
        self._log_body = tk.Frame(page, bg=C["bg"])
        self._log_body.pack(fill="both", expand=True, padx=20, pady=20)

    def _refresh_log_tab(self):
        for w in self._log_body.winfo_children(): w.destroy()

        tk.Label(self._log_body, text="Access Log",
                 font=("Segoe UI", 13, "bold"),
                 fg=C["text"], bg=C["bg"]).pack(anchor="w")

        if not self.current_folder:
            tk.Label(self._log_body,
                     text="Select a folder from the Home tab first.",
                     font=FONT_SUB, fg=C["text3"],
                     bg=C["bg"]).pack(anchor="w", pady=16)
            return

        tk.Label(self._log_body,
                 text=f"📁  {self.current_folder}",
                 font=FONT_SMALL, fg=C["text3"],
                 bg=C["bg"]).pack(anchor="w", pady=(2, 14))

        cols = ("Time", "Event", "Detail")
        tv   = ttk.Treeview(self._log_body, columns=cols,
                            show="headings", height=20)
        s    = ttk.Style()
        s.configure("L2.Treeview",
                    background=C["white"], foreground=C["text"],
                    fieldbackground=C["white"], rowheight=28,
                    font=("Segoe UI", 9))
        s.configure("L2.Treeview.Heading",
                    background=C["bg"], foreground=C["text2"],
                    font=("Segoe UI", 8, "bold"))
        s.map("L2.Treeview",
              background=[("selected", C["blue_light"])],
              foreground=[("selected", C["blue_dark"])])
        tv.configure(style="L2.Treeview")

        for col, w in zip(cols, [165, 155, 220]):
            tv.heading(col, text=col.upper())
            tv.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(self._log_body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)

        entries = _load_log(self.current_folder)
        if not entries:
            tv.insert("", "end", values=("—", "No entries yet", ""))
        else:
            for e in reversed(entries):
                tv.insert("", "end",
                          values=(e["time"], e["event"], e.get("detail", "")))

        flat_btn(self._log_body, "Clear Log", self._clear_log,
                 bg=C["red"], w=120, h=34).pack(anchor="e", pady=(10, 0))

    def _clear_log(self):
        if self.current_folder and messagebox.askyesno(
                "Clear Log", "Delete all log entries?"):
            _save_log(self.current_folder, [])
            self._refresh_log_tab()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path: self._select(path)

    def _select(self, path):
        self.current_folder = path
        self.folder_label.config(
            text=f"📁  {path}", fg=C["text"])
        self._draw_status(path)
        self._add_recent(path)
        self._refresh_list()

    def _refresh_view(self):
        self._draw_status(self.current_folder)
        self._refresh_list()

    def _lock_action(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first."); return
        if is_locked(self.current_folder):
            messagebox.showinfo("Already Locked", "This folder is already locked."); return
        LockSetupWindow(self, self.current_folder, on_success=self._refresh_view)

    def _unlock_action(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first."); return
        if not is_locked(self.current_folder):
            messagebox.showinfo("Not Locked", "This folder is not locked."); return
        UnlockWindow(self, self.current_folder, on_success=self._refresh_view)

    def _change_pin(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first."); return
        if not is_locked(self.current_folder):
            messagebox.showinfo("Not Locked", "Lock the folder first."); return
        ChangePINWindow(self, self.current_folder)

    def _view_log(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Please select a folder first."); return
        LogViewerWindow(self, self.current_folder)

    def _on_sel(self, e):
        sel = self.folder_list.curselection()
        if sel: self._select(self.recent_folders[sel[0]])

    def _on_dbl(self, e):
        sel = self.folder_list.curselection()
        if not sel: return
        path = self.recent_folders[sel[0]]
        if is_locked(path):
            UnlockWindow(self, path, on_success=self._refresh_view)
        else:
            LockSetupWindow(self, path, on_success=self._refresh_view)

    def _refresh_list(self):
        self.folder_list.delete(0, "end")
        for fp in self.recent_folders:
            icon = "🔒" if is_locked(fp) else "🔓"
            lo, _ = is_locked_out(fp)
            warn  = "  ⚠" if lo else ""
            self.folder_list.insert(
                "end", f"  {icon}  {Path(fp).name}{warn}  —  {fp}")

    def _add_recent(self, path):
        if path in self.recent_folders:
            self.recent_folders.remove(path)
        self.recent_folders.insert(0, path)
        self.recent_folders = self.recent_folders[:20]
        self._save_recent()

    # ── Auto-lock ──────────────────────────────────────────────────────────

    def check_auto_locks(self):
        for fp in self.recent_folders:
            lf = get_lock_file(fp)
            if not os.path.exists(lf): continue
            try:
                with open(lf) as f: d = json.load(f)
                als = d.get("auto_lock_seconds", 0)
                uat = d.get("unlocked_at", 0)
                if als and uat and not d.get("locked", False):
                    if time.time() > uat + als:
                        create_lock(fp, "__auto__", "", "")
                        write_log(fp, "AUTO_LOCKED", f"After {als}s")
            except: pass
        self._draw_status(self.current_folder)
        self._refresh_list()

    # ── Persistence ────────────────────────────────────────────────────────

    def _cfg_path(self):
        base = (os.path.dirname(sys.executable)
                if getattr(sys, 'frozen', False)
                else os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, ".jhsecure_app.json")

    def _load_recent(self):
        try:
            with open(self._cfg_path()) as f:
                return json.load(f).get("recent", [])
        except: return []

    def _save_recent(self):
        try:
            with open(self._cfg_path(), 'w') as f:
                json.dump({"recent": self.recent_folders}, f)
        except: pass


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = JHSecureApp()
    app.mainloop()
