"""
JH Secure - Portable Folder Lock Application
Version: 2.0.0

New in v2.0:
  - Fake Password (Decoy PIN) with dummy files
  - Auto-Lock Timer
  - Brute Force Protection (lockout after X failed attempts)
  - Change PIN (old PIN required)
  - Access Log (unlock history)
  - Failed Attempt Log
  - Multiple Folder Lock (batch select)
  - Batch Unlock (master PIN)
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
#  CRYPTO UTILITIES
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

COLORS = {
    "bg":      "#0A0A0F",
    "surface": "#111118",
    "card":    "#16161F",
    "border":  "#252535",
    "accent":  "#00C8FF",
    "accent2": "#0088BB",
    "success": "#00FF9C",
    "danger":  "#FF3366",
    "warning": "#FFB800",
    "purple":  "#A855F7",
    "text":    "#EEEEF5",
    "subtext": "#666680",
    "input_bg":"#0E0E16",
}


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def _log_path(folder_path):
    return os.path.join(folder_path, LOG_FILENAME)

def _load_log(folder_path):
    try:
        with open(_log_path(folder_path), 'r') as f:
            return json.load(f)
    except Exception:
        return []

def _save_log(folder_path, entries):
    try:
        with open(_log_path(folder_path), 'w') as f:
            json.dump(entries[-200:], f, indent=2)
    except Exception:
        pass

def write_log(folder_path, event, detail=""):
    entries = _load_log(folder_path)
    entries.append({
        "time":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event":  event,
        "detail": detail,
    })
    _save_log(folder_path, entries)


# ══════════════════════════════════════════════════════════════════════════════
#  BRUTE FORCE PROTECTION
# ══════════════════════════════════════════════════════════════════════════════

def _lockout_path(folder_path):
    return os.path.join(folder_path, LOCKOUT_FILENAME)

def get_lockout_info(folder_path):
    try:
        with open(_lockout_path(folder_path), 'r') as f:
            return json.load(f)
    except Exception:
        return {"attempts": 0, "locked_until": 0}

def save_lockout_info(folder_path, info):
    try:
        with open(_lockout_path(folder_path), 'w') as f:
            json.dump(info, f)
    except Exception:
        pass

def is_locked_out(folder_path):
    """Returns (bool, seconds_remaining)."""
    info      = get_lockout_info(folder_path)
    remaining = int(info.get("locked_until", 0) - time.time())
    return (True, remaining) if remaining > 0 else (False, 0)

def record_failed_attempt(folder_path):
    info = get_lockout_info(folder_path)
    if time.time() > info.get("locked_until", 0):
        info["attempts"] = 0
    info["attempts"] = info.get("attempts", 0) + 1
    if info["attempts"] >= MAX_ATTEMPTS:
        info["locked_until"] = time.time() + LOCKOUT_SECONDS
        write_log(folder_path, "LOCKOUT",
                  f"Locked {LOCKOUT_SECONDS}s after {MAX_ATTEMPTS} fails")
    save_lockout_info(folder_path, info)
    write_log(folder_path, "FAILED_ATTEMPT", f"Attempt #{info['attempts']}")
    return info["attempts"]

def reset_lockout(folder_path):
    save_lockout_info(folder_path, {"attempts": 0, "locked_until": 0})


# ══════════════════════════════════════════════════════════════════════════════
#  LOCK / UNLOCK CORE
# ══════════════════════════════════════════════════════════════════════════════

def get_lock_file(folder_path):
    return os.path.join(folder_path, LOCK_FILENAME)

def is_locked(folder_path):
    lf = get_lock_file(folder_path)
    if not os.path.exists(lf):
        return False
    try:
        with open(lf, 'r') as f:
            return json.load(f).get("locked", False)
    except Exception:
        return False

def _pack_folder(folder_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs
                       if not d.startswith('.jhsecure') and d != "__decoy__"]
            for f in files:
                if f.startswith('.jhsecure'):
                    continue
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, folder_path))
    return buf.getvalue()

def _remove_originals(folder_path):
    for root, dirs, files in os.walk(folder_path, topdown=False):
        dirs[:] = [d for d in dirs if not d.startswith('.jhsecure')]
        for f in files:
            if f.startswith('.jhsecure'):
                continue
            try:
                os.remove(os.path.join(root, f))
            except Exception:
                pass
        if root != folder_path:
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except Exception:
                pass


def create_lock(folder_path, pin, secret_q, secret_a,
                decoy_pin="", auto_lock_seconds=0, master_pin=""):
    try:
        salt     = os.urandom(32)
        key      = derive_key(pin, salt)
        raw_zip  = _pack_folder(folder_path)
        encrypted = xor_encrypt(raw_zip, key)

        lock_data = {
            "locked":             True,
            "version":            "2.0",
            "salt":               base64.b64encode(salt).decode(),
            "pin_hash":           hash_pin(pin, salt),
            "secret_question":    secret_q,
            "secret_answer_hash": hash_answer(secret_a) if secret_a else "",
            "encrypted_data":     base64.b64encode(encrypted).decode(),
            "auto_lock_seconds":  auto_lock_seconds,
            "unlocked_at":        0,
        }

        # ── Decoy PIN ──────────────────────────────────────────────────
        if decoy_pin:
            decoy_folder = os.path.join(folder_path, "__decoy__")
            os.makedirs(decoy_folder, exist_ok=True)
            sample = os.path.join(decoy_folder, "readme.txt")
            if not os.path.exists(sample):
                with open(sample, 'w') as f:
                    f.write("This folder is empty.\n")
            dbuf = io.BytesIO()
            with zipfile.ZipFile(dbuf, 'w') as zf:
                for df in os.listdir(decoy_folder):
                    dfp = os.path.join(decoy_folder, df)
                    if os.path.isfile(dfp):
                        zf.write(dfp, df)
            dsalt = os.urandom(32)
            dkey  = derive_key(decoy_pin, dsalt)
            lock_data["decoy_salt"]     = base64.b64encode(dsalt).decode()
            lock_data["decoy_pin_hash"] = hash_pin(decoy_pin, dsalt)
            lock_data["decoy_data"]     = base64.b64encode(
                xor_encrypt(dbuf.getvalue(), dkey)).decode()

        # ── Master PIN ─────────────────────────────────────────────────
        if master_pin:
            msalt = os.urandom(32)
            mkey  = derive_key(master_pin, msalt)
            lock_data["master_salt"]     = base64.b64encode(msalt).decode()
            lock_data["master_pin_hash"] = hash_pin(master_pin, msalt)
            lock_data["master_data"]     = base64.b64encode(
                xor_encrypt(raw_zip, mkey)).decode()

        with open(get_lock_file(folder_path), 'w') as f:
            json.dump(lock_data, f, indent=2)

        _remove_originals(folder_path)
        write_log(folder_path, "LOCKED", "Folder locked")
        return True
    except Exception as e:
        print(f"Lock error: {e}")
        return False


def verify_pin(folder_path, pin):
    """Returns 'real' | 'decoy' | 'master' | 'wrong'."""
    try:
        with open(get_lock_file(folder_path), 'r') as f:
            data = json.load(f)
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
    except Exception:
        return 'wrong'


def unlock_folder(folder_path, pin):
    try:
        with open(get_lock_file(folder_path), 'r') as f:
            data = json.load(f)

        pin_type = verify_pin(folder_path, pin)
        if pin_type == 'wrong':
            return False

        if pin_type == 'real':
            key = derive_key(pin, base64.b64decode(data["salt"]))
            raw = xor_encrypt(base64.b64decode(data["encrypted_data"]), key)
        elif pin_type == 'decoy':
            key = derive_key(pin, base64.b64decode(data["decoy_salt"]))
            raw = xor_encrypt(base64.b64decode(data["decoy_data"]), key)
        else:
            key = derive_key(pin, base64.b64decode(data["master_salt"]))
            raw = xor_encrypt(base64.b64decode(data["master_data"]), key)

        with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
            zf.extractall(folder_path)

        os.remove(get_lock_file(folder_path))
        reset_lockout(folder_path)
        write_log(folder_path, "UNLOCKED", f"via {pin_type} PIN")
        return True
    except Exception as e:
        print(f"Unlock error: {e}")
        return False


def change_pin(folder_path, old_pin, new_pin):
    try:
        with open(get_lock_file(folder_path), 'r') as f:
            data = json.load(f)
        old_salt = base64.b64decode(data["salt"])
        if not hmac.compare_digest(hash_pin(old_pin, old_salt), data["pin_hash"]):
            return False
        old_key  = derive_key(old_pin, old_salt)
        raw      = xor_encrypt(base64.b64decode(data["encrypted_data"]), old_key)
        new_salt = os.urandom(32)
        new_key  = derive_key(new_pin, new_salt)
        data["salt"]           = base64.b64encode(new_salt).decode()
        data["pin_hash"]       = hash_pin(new_pin, new_salt)
        data["encrypted_data"] = base64.b64encode(xor_encrypt(raw, new_key)).decode()
        with open(get_lock_file(folder_path), 'w') as f:
            json.dump(data, f, indent=2)
        write_log(folder_path, "PIN_CHANGED", "Real PIN changed")
        return True
    except Exception as e:
        print(f"Change PIN error: {e}")
        return False


def verify_secret(folder_path, answer):
    try:
        with open(get_lock_file(folder_path), 'r') as f:
            data = json.load(f)
        return hmac.compare_digest(hash_answer(answer), data["secret_answer_hash"])
    except Exception:
        return False


def reset_pin_via_secret(folder_path, new_pin):
    """Reset PIN – call only after verify_secret passes."""
    try:
        with open(get_lock_file(folder_path), 'r') as f:
            data = json.load(f)
        old_salt = base64.b64decode(data["salt"])
        old_key  = derive_key(base64.b64encode(old_salt).decode(), old_salt)
        raw      = xor_encrypt(base64.b64decode(data["encrypted_data"]), old_key)
        new_salt = os.urandom(32)
        new_key  = derive_key(new_pin, new_salt)
        data["salt"]           = base64.b64encode(new_salt).decode()
        data["pin_hash"]       = hash_pin(new_pin, new_salt)
        data["encrypted_data"] = base64.b64encode(xor_encrypt(raw, new_key)).decode()
        with open(get_lock_file(folder_path), 'w') as f:
            json.dump(data, f, indent=2)
        write_log(folder_path, "PIN_RESET", "via secret question")
        return True
    except Exception as e:
        print(f"Reset error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-LOCK DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class AutoLockDaemon:
    def __init__(self, app):
        self.app  = app
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            time.sleep(15)
            try:
                self.app.after(0, self.app.check_auto_locks)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  CUSTOM WIDGETS
# ══════════════════════════════════════════════════════════════════════════════

class PINEntry(tk.Frame):
    def __init__(self, master, length=6, **kwargs):
        super().__init__(master, bg=COLORS["bg"], **kwargs)
        self.length = length
        self._var   = tk.StringVar()
        self._dots  = []

        self._entry = tk.Entry(self, textvariable=self._var, show="",
                               width=0, bg=COLORS["bg"], fg=COLORS["bg"],
                               insertbackground=COLORS["bg"],
                               relief="flat", bd=0, highlightthickness=0)
        self._entry.pack()
        self._var.trace_add("write", self._on_change)

        row = tk.Frame(self, bg=COLORS["bg"])
        row.pack(pady=6)
        for _ in range(length):
            c = tk.Canvas(row, width=20, height=20,
                          bg=COLORS["bg"], highlightthickness=0)
            c.grid(row=0, column=_, padx=5)
            c.create_oval(2, 2, 18, 18, fill=COLORS["border"],
                          outline=COLORS["border"], tags="dot")
            self._dots.append(c)

        for w in (self, row, *self._dots):
            w.bind("<Button-1>", lambda e: self._entry.focus_set())

    def _on_change(self, *_):
        val   = self._var.get()
        clean = ''.join(c for c in val if c.isdigit())[:self.length]
        if clean != val:
            self._var.set(clean)
            self._entry.icursor(len(clean))
            return
        for i, dot in enumerate(self._dots):
            dot.delete("dot")
            col = COLORS["accent"] if i < len(clean) else COLORS["border"]
            dot.create_oval(2, 2, 18, 18, fill=col, outline=col, tags="dot")

    def get(self):
        return self._var.get()

    def clear(self):
        self._var.set("")

    def focus(self):
        self._entry.focus_set()


def make_btn(parent, text, cmd, color=None, w=180, h=42):
    color = color or COLORS["accent"]
    c = tk.Canvas(parent, width=w, height=h,
                  bg=COLORS["card"], highlightthickness=0, cursor="hand2")
    def draw(fill):
        c.delete("all")
        c.create_rectangle(0, 0, w, h, fill=fill, outline=color, width=1)
        c.create_text(w//2, h//2, text=text,
                      fill=COLORS["text"], font=("Consolas", 9, "bold"))
    draw(COLORS["card"])
    c.bind("<Enter>",    lambda e: draw(color))
    c.bind("<Leave>",    lambda e: draw(COLORS["card"]))
    c.bind("<Button-1>", lambda e: cmd())
    return c


def shake_window(win):
    x, y = win.winfo_x(), win.winfo_y()
    for dx in [10, -10, 8, -8, 5, -5, 0]:
        win.geometry(f"+{x+dx}+{y}")
        win.update()
        time.sleep(0.025)


# ══════════════════════════════════════════════════════════════════════════════
#  BASE WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class BaseWin(tk.Toplevel):
    def __init__(self, master, title, w=420, h=500):
        super().__init__(master)
        self.title(title)
        self.configure(bg=COLORS["bg"])
        self.resizable(False, False)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self._draw_header(title)
        self.lift()
        self.focus_force()

    def _draw_header(self, subtitle):
        hdr = tk.Frame(self, bg=COLORS["surface"], height=58)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        c = tk.Canvas(hdr, width=32, height=32,
                      bg=COLORS["surface"], highlightthickness=0)
        c.place(x=16, y=13)
        c.create_oval(0, 0, 32, 32, fill=COLORS["accent"], outline="")
        c.create_text(16, 16, text="🔒", font=("Segoe UI Emoji", 12))
        tk.Label(hdr, text="JH SECURE", font=("Consolas", 12, "bold"),
                 fg=COLORS["accent"], bg=COLORS["surface"]).place(x=58, y=12)
        tk.Label(hdr, text=subtitle, font=("Consolas", 7),
                 fg=COLORS["subtext"], bg=COLORS["surface"]).place(x=59, y=34)
        tk.Frame(self, bg=COLORS["accent"], height=2).pack(fill="x")


# ══════════════════════════════════════════════════════════════════════════════
#  LOCK SETUP  (v2)
# ══════════════════════════════════════════════════════════════════════════════

class LockSetupWindow(BaseWin):
    def __init__(self, master, folder_path, on_success=None):
        super().__init__(master, "Set Lock", w=460, h=680)
        self.folder_path = folder_path
        self.on_success  = on_success
        self._build()

    def _build(self):
        outer = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        sb    = tk.Scrollbar(self, orient="vertical", command=outer.yview)
        outer.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        outer.pack(fill="both", expand=True)

        body   = tk.Frame(outer, bg=COLORS["bg"])
        wid    = outer.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: outer.configure(scrollregion=outer.bbox("all")))
        outer.bind("<Configure>",
                   lambda e: outer.itemconfig(wid, width=e.width))

        P = dict(padx=28)

        def section(txt):
            tk.Label(body, text=txt, font=("Consolas", 8, "bold"),
                     fg=COLORS["accent"], bg=COLORS["bg"],
                     **P).pack(anchor="w", pady=(8, 2))

        def pin_row(color=None):
            fr = tk.Frame(body, bg=COLORS["bg"]); fr.pack(anchor="w", **P)
            pe = PINEntry(fr, length=6); pe.pack()
            return pe

        def separator():
            tk.Frame(body, bg=COLORS["border"], height=1,
                     **P).pack(fill="x", pady=14)

        tk.Frame(body, bg=COLORS["bg"], height=14).pack()
        tk.Label(body, text="LOCK FOLDER", font=("Consolas", 15, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"], **P).pack(anchor="w")
        tk.Label(body, text=f"📁  {Path(self.folder_path).name}",
                 font=("Consolas", 9), fg=COLORS["subtext"],
                 bg=COLORS["bg"], **P).pack(anchor="w", pady=(2, 14))

        # Real PIN
        section("🔑  REAL PIN  (6 digits)")
        self.pin1 = pin_row()
        self.pin1.focus()
        section("CONFIRM PIN")
        self.pin2 = pin_row()

        separator()
        section("🔐  SECURITY RECOVERY")
        self.q_var = tk.StringVar(value=SECRET_QUESTIONS[0])
        style = ttk.Style(); style.theme_use("clam")
        style.configure("D.TCombobox",
                        fieldbackground=COLORS["input_bg"],
                        background=COLORS["input_bg"],
                        foreground=COLORS["text"],
                        bordercolor=COLORS["border"],
                        arrowcolor=COLORS["accent"])
        cb = ttk.Combobox(body, textvariable=self.q_var,
                          values=SECRET_QUESTIONS, state="readonly",
                          style="D.TCombobox", width=48)
        cb.pack(anchor="w", padx=28, pady=(4, 8))

        fr_ans = tk.Frame(body, bg=COLORS["bg"])
        fr_ans.pack(fill="x", padx=28)
        tk.Label(fr_ans, text="SECRET ANSWER", font=("Consolas", 7),
                 fg=COLORS["subtext"], bg=COLORS["bg"]).pack(anchor="w")
        self.ans_e = tk.Entry(fr_ans, show="*", bg=COLORS["input_bg"],
                              fg=COLORS["text"],
                              insertbackground=COLORS["accent"],
                              relief="flat", bd=8, font=("Consolas", 10))
        self.ans_e.pack(fill="x", pady=(3, 4))

        separator()
        section("👻  FAKE PASSWORD  (optional)")
        tk.Label(body,
                 text="Give this PIN under pressure → shows empty/fake files.",
                 font=("Consolas", 7), fg=COLORS["subtext"],
                 bg=COLORS["bg"], **P).pack(anchor="w", pady=(0, 4))
        self.decoy_pin = pin_row(color=COLORS["warning"])

        separator()
        section("🗝️  MASTER PIN  (optional)")
        tk.Label(body,
                 text="One PIN to batch-unlock all your JH Secure folders.",
                 font=("Consolas", 7), fg=COLORS["subtext"],
                 bg=COLORS["bg"], **P).pack(anchor="w", pady=(0, 4))
        self.master_pin = pin_row(color=COLORS["purple"])

        separator()
        section("⏱️  AUTO-LOCK TIMER")
        self.auto_var = tk.StringVar(value="Never")
        row = tk.Frame(body, bg=COLORS["bg"])
        row.pack(anchor="w", padx=28, pady=(4, 14))
        for opt in AUTO_LOCK_OPTIONS:
            tk.Radiobutton(row, text=opt, variable=self.auto_var, value=opt,
                           font=("Consolas", 8), fg=COLORS["text"],
                           bg=COLORS["bg"], selectcolor=COLORS["card"],
                           activeforeground=COLORS["accent"],
                           activebackground=COLORS["bg"],
                           relief="flat").pack(side="left", padx=(0, 10))

        self.err_lbl = tk.Label(body, text="", font=("Consolas", 8),
                                fg=COLORS["danger"], bg=COLORS["bg"])
        self.err_lbl.pack(pady=(4, 6))
        make_btn(body, "🔒  LOCK FOLDER", self._do_lock,
                 color=COLORS["danger"], w=380, h=48).pack(pady=(0, 24))

    def _do_lock(self):
        p1     = self.pin1.get()
        p2     = self.pin2.get()
        ans    = self.ans_e.get().strip()
        decoy  = self.decoy_pin.get()
        master = self.master_pin.get()
        auto_s = AUTO_LOCK_OPTIONS[self.auto_var.get()]

        if len(p1) < 4:
            self.err_lbl.config(text="PIN must be ≥ 4 digits"); return
        if p1 != p2:
            self.pin2.clear()
            self.err_lbl.config(text="PINs do not match"); return
        if not ans:
            self.err_lbl.config(text="Secret answer is required"); return
        if decoy and decoy == p1:
            self.err_lbl.config(text="Decoy PIN must differ from real PIN"); return
        if master and master == p1:
            self.err_lbl.config(text="Master PIN must differ from real PIN"); return

        self.err_lbl.config(text="Locking…", fg=COLORS["warning"])
        self.update()

        ok = create_lock(self.folder_path, p1, self.q_var.get(), ans,
                         decoy_pin=decoy, auto_lock_seconds=auto_s,
                         master_pin=master)
        if ok:
            if self.on_success:
                self.on_success()
            self.destroy()
        else:
            self.err_lbl.config(text="Lock failed.", fg=COLORS["danger"])


# ══════════════════════════════════════════════════════════════════════════════
#  UNLOCK WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class UnlockWindow(BaseWin):
    def __init__(self, master, folder_path, on_success=None):
        super().__init__(master, "Unlock", w=400, h=440)
        self.folder_path = folder_path
        self.on_success  = on_success
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()

    def _build(self):
        for w in self.winfo_children():
            if isinstance(w, (tk.Frame, tk.Canvas)):
                w.destroy()

        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=30, pady=20)

        tk.Label(body, text="ENTER PIN",
                 font=("Consolas", 18, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(pady=(10, 4))
        tk.Label(body, text=f"📁  {Path(self.folder_path).name}",
                 font=("Consolas", 9), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(pady=(0, 20))

        lo, remaining = is_locked_out(self.folder_path)
        if lo:
            tk.Label(body,
                     text=f"⛔  Too many failed attempts!\n"
                          f"Try again in {remaining//60}m {remaining%60:02d}s",
                     font=("Consolas", 10, "bold"),
                     fg=COLORS["danger"], bg=COLORS["bg"],
                     justify="center").pack(pady=20)
            self._lockout_tick(body, remaining)
            return

        self.pin_e  = PINEntry(body, length=6)
        self.pin_e.pack(pady=(0, 8))
        self.pin_e.focus()

        self.status = tk.Label(body, text="", font=("Consolas", 8),
                               fg=COLORS["danger"], bg=COLORS["bg"])
        self.status.pack(pady=(0, 14))

        make_btn(body, "UNLOCK  →", self._do,
                 color=COLORS["success"], w=200, h=44).pack(pady=(0, 16))

        btn_row = tk.Frame(body, bg=COLORS["bg"])
        btn_row.pack()
        for txt, cmd in [("Forgot PIN?", self._forgot),
                         ("Change PIN",  self._change)]:
            lbl = tk.Label(btn_row, text=txt,
                           font=("Consolas", 8, "underline"),
                           fg=COLORS["subtext"], bg=COLORS["bg"],
                           cursor="hand2")
            lbl.pack(side="left", padx=14)
            lbl.bind("<Button-1>", lambda e, c=cmd: c())

        self.bind("<Return>", lambda e: self._do())

    def _lockout_tick(self, parent, remaining):
        lbl = tk.Label(parent, text="", font=("Consolas", 9),
                       fg=COLORS["warning"], bg=COLORS["bg"])
        lbl.pack()
        def tick(r):
            if r <= 0:
                self._build(); return
            lbl.config(text=f"Retry in {r//60}m {r%60:02d}s")
            self.after(1000, tick, r - 1)
        tick(remaining)

    def _do(self):
        lo, _ = is_locked_out(self.folder_path)
        if lo:
            return
        pin = self.pin_e.get()
        if len(pin) < 4:
            shake_window(self); return

        if verify_pin(self.folder_path, pin) != 'wrong':
            if unlock_folder(self.folder_path, pin):
                if self.on_success:
                    self.on_success()
                self.destroy()
            else:
                self.status.config(text="Decryption error.")
        else:
            attempts = record_failed_attempt(self.folder_path)
            self.pin_e.clear()
            left = MAX_ATTEMPTS - attempts
            if left <= 0:
                self._build()
            else:
                self.status.config(text=f"Wrong PIN — {left} attempt(s) left")
                shake_window(self)

    def _forgot(self):
        RecoveryWindow(self, self.folder_path,
                       on_success=lambda: (
                           self.on_success() if self.on_success else None,
                           self.destroy()))

    def _change(self):
        ChangePINWindow(self, self.folder_path,
                        on_success=lambda: self.status.config(
                            text="PIN changed! Use new PIN.",
                            fg=COLORS["success"]))


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGE PIN
# ══════════════════════════════════════════════════════════════════════════════

class ChangePINWindow(BaseWin):
    def __init__(self, master, folder_path, on_success=None):
        super().__init__(master, "Change PIN", w=400, h=420)
        self.folder_path = folder_path
        self.on_success  = on_success
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=30, pady=20)

        tk.Label(body, text="CHANGE PIN",
                 font=("Consolas", 16, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(pady=(8, 20))

        for label, attr in [("CURRENT PIN", "old_pin"),
                             ("NEW PIN",     "new_pin"),
                             ("CONFIRM",     "confirm")]:
            tk.Label(body, text=label, font=("Consolas", 7),
                     fg=COLORS["subtext"], bg=COLORS["bg"]).pack(anchor="w")
            pe = PINEntry(body); pe.pack(pady=(4, 12))
            setattr(self, attr, pe)

        self.old_pin.focus()
        self.err = tk.Label(body, text="", font=("Consolas", 8),
                            fg=COLORS["danger"], bg=COLORS["bg"])
        self.err.pack(pady=(0, 10))
        make_btn(body, "CHANGE PIN  ✓", self._do,
                 color=COLORS["accent"], w=280, h=44).pack()

    def _do(self):
        old = self.old_pin.get()
        new = self.new_pin.get()
        conf = self.confirm.get()
        if len(new) < 4:
            self.err.config(text="New PIN too short"); return
        if new != conf:
            self.confirm.clear()
            self.err.config(text="PINs do not match"); return
        if change_pin(self.folder_path, old, new):
            if self.on_success:
                self.on_success()
            self.destroy()
        else:
            self.old_pin.clear()
            self.err.config(text="Wrong current PIN")


# ══════════════════════════════════════════════════════════════════════════════
#  RECOVERY WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class RecoveryWindow(BaseWin):
    def __init__(self, master, folder_path, on_success=None):
        super().__init__(master, "Recovery", w=400, h=440)
        self.folder_path = folder_path
        self.on_success  = on_success
        try:
            with open(get_lock_file(folder_path)) as f:
                self.question = json.load(f).get("secret_question", "N/A")
        except Exception:
            self.question = "N/A"
        self._verified_answer = ""
        self.body = tk.Frame(self, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True, padx=28, pady=20)
        self._step1()

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _step1(self):
        self._clear()
        tk.Label(self.body, text="FORGOT PIN",
                 font=("Consolas", 16, "bold"),
                 fg=COLORS["warning"], bg=COLORS["bg"]).pack(pady=(8, 18))
        tk.Label(self.body, text="SECRET QUESTION:",
                 font=("Consolas", 7), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(anchor="w")
        tk.Label(self.body, text=self.question,
                 font=("Consolas", 10), fg=COLORS["text"],
                 bg=COLORS["bg"], wraplength=340).pack(anchor="w", pady=(4, 18))
        tk.Label(self.body, text="YOUR ANSWER:",
                 font=("Consolas", 7), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(anchor="w")
        self.ans = tk.Entry(self.body, show="*", bg=COLORS["input_bg"],
                            fg=COLORS["text"],
                            insertbackground=COLORS["accent"],
                            relief="flat", bd=8, font=("Consolas", 11))
        self.ans.pack(fill="x", pady=(4, 20))
        self.ans.focus()
        self.err = tk.Label(self.body, text="", font=("Consolas", 8),
                            fg=COLORS["danger"], bg=COLORS["bg"])
        self.err.pack(pady=(0, 10))
        make_btn(self.body, "VERIFY  →", self._verify,
                 color=COLORS["warning"], w=260, h=44).pack()
        self.bind("<Return>", lambda e: self._verify())

    def _verify(self):
        answer = self.ans.get()
        if verify_secret(self.folder_path, answer):
            self._verified_answer = answer
            self._step2()
        else:
            self.ans.delete(0, "end")
            self.err.config(text="Wrong answer. Try again.")

    def _step2(self):
        self._clear()
        self.unbind("<Return>")
        tk.Label(self.body, text="SET NEW PIN",
                 font=("Consolas", 16, "bold"),
                 fg=COLORS["success"], bg=COLORS["bg"]).pack(pady=(8, 20))
        for label, attr in [("NEW PIN:", "np"), ("CONFIRM:", "cp")]:
            tk.Label(self.body, text=label, font=("Consolas", 7),
                     fg=COLORS["subtext"], bg=COLORS["bg"]).pack(anchor="w")
            pe = PINEntry(self.body); pe.pack(pady=(4, 12))
            setattr(self, attr, pe)
        self.np.focus()
        self.err2 = tk.Label(self.body, text="", font=("Consolas", 8),
                             fg=COLORS["danger"], bg=COLORS["bg"])
        self.err2.pack(pady=(0, 10))
        make_btn(self.body, "RESET PIN  ✓", self._reset,
                 color=COLORS["success"], w=260, h=44).pack()

    def _reset(self):
        p1, p2 = self.np.get(), self.cp.get()
        if len(p1) < 4:
            self.err2.config(text="Too short"); return
        if p1 != p2:
            self.cp.clear()
            self.err2.config(text="Mismatch"); return
        if reset_pin_via_secret(self.folder_path, p1):
            messagebox.showinfo("Done", "PIN reset! Unlock with new PIN.",
                                parent=self)
            if self.on_success:
                self.on_success()
            self.destroy()
        else:
            self.err2.config(text="Reset failed")


# ══════════════════════════════════════════════════════════════════════════════
#  LOG VIEWER
# ══════════════════════════════════════════════════════════════════════════════

class LogViewerWindow(BaseWin):
    def __init__(self, master, folder_path):
        super().__init__(master, "Access Log", w=540, h=500)
        self.folder_path = folder_path
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(body, text="ACCESS LOG",
                 font=("Consolas", 14, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", pady=(0, 4))
        tk.Label(body, text=f"📁  {Path(self.folder_path).name}",
                 font=("Consolas", 8), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(anchor="w", pady=(0, 12))

        cols = ("Time", "Event", "Detail")
        tv   = ttk.Treeview(body, columns=cols, show="headings", height=18)

        s = ttk.Style()
        s.configure("Log.Treeview",
                    background=COLORS["card"], foreground=COLORS["text"],
                    fieldbackground=COLORS["card"])
        s.configure("Log.Treeview.Heading",
                    background=COLORS["surface"], foreground=COLORS["accent"],
                    font=("Consolas", 8, "bold"))
        s.map("Log.Treeview",
              background=[("selected", COLORS["accent2"])],
              foreground=[("selected", COLORS["bg"])])
        tv.configure(style="Log.Treeview")

        for col, w in zip(cols, [160, 150, 190]):
            tv.heading(col, text=col.upper())
            tv.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)

        entries = _load_log(self.folder_path)
        if not entries:
            tv.insert("", "end", values=("—", "No entries yet", ""))
        else:
            for e in reversed(entries):
                tv.insert("", "end",
                          values=(e["time"], e["event"], e.get("detail", "")))

        make_btn(body, "CLEAR LOG", self._clear,
                 color=COLORS["danger"], w=140, h=36).pack(
                     anchor="e", pady=(10, 0))

    def _clear(self):
        if messagebox.askyesno("Clear", "Delete all log entries?",
                               parent=self):
            _save_log(self.folder_path, [])
            self.destroy()
            LogViewerWindow(self.master, self.folder_path)


# ══════════════════════════════════════════════════════════════════════════════
#  BATCH UNLOCK WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class BatchUnlockWindow(BaseWin):
    def __init__(self, master, folders, on_done=None):
        super().__init__(master, "Batch Unlock", w=480, h=520)
        self.folders = [f for f in folders if is_locked(f)]
        self.on_done = on_done
        self._build()

    def _build(self):
        body = tk.Frame(self, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=20)

        tk.Label(body, text="BATCH UNLOCK",
                 font=("Consolas", 15, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(pady=(4, 4))
        tk.Label(body, text=f"{len(self.folders)} locked folder(s)",
                 font=("Consolas", 8), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(pady=(0, 14))

        lf = tk.Frame(body, bg=COLORS["card"])
        lf.pack(fill="x", pady=(0, 14))
        for fp in self.folders:
            tk.Label(lf, text=f"  🔒  {Path(fp).name}",
                     font=("Consolas", 9), fg=COLORS["text"],
                     bg=COLORS["card"], anchor="w",
                     pady=3).pack(fill="x")

        tk.Frame(body, bg=COLORS["border"], height=1).pack(fill="x", pady=(0, 14))
        tk.Label(body, text="ENTER MASTER PIN",
                 font=("Consolas", 8, "bold"),
                 fg=COLORS["purple"], bg=COLORS["bg"]).pack(anchor="w", pady=(0, 6))
        self.mpin = PINEntry(body); self.mpin.pack(pady=(0, 8))
        self.mpin.focus()

        self.res = tk.Label(body, text="", font=("Consolas", 8),
                            fg=COLORS["text"], bg=COLORS["bg"], justify="left")
        self.res.pack(pady=(0, 12))
        make_btn(body, "🗝️  UNLOCK ALL", self._do,
                 color=COLORS["purple"], w=300, h=46).pack()

    def _do(self):
        pin = self.mpin.get()
        if len(pin) < 4:
            shake_window(self); return
        ok, fail = 0, 0
        lines = []
        for fp in self.folders:
            pt = verify_pin(fp, pin)
            if pt in ('real', 'master'):
                if unlock_folder(fp, pin):
                    ok += 1; lines.append(f"✅  {Path(fp).name}")
                else:
                    fail += 1; lines.append(f"❌  {Path(fp).name}  (error)")
            else:
                fail += 1; lines.append(f"⛔  {Path(fp).name}  (wrong PIN)")
        self.res.config(text="\n".join(lines[-8:]),
                        fg=COLORS["success"] if fail == 0 else COLORS["warning"])
        if ok > 0 and self.on_done:
            self.on_done()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

class JHSecureApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JH Secure  v2.0")
        self.configure(bg=COLORS["bg"])
        self.resizable(True, True)
        self.minsize(560, 640)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"620x720+{(sw-620)//2}+{(sh-720)//2}")

        self.current_folder = None
        self.recent_folders = []
        self.batch_folders  = []

        self._build_ui()
        self.recent_folders = self._load_recent()
        self._refresh_list()
        self._daemon = AutoLockDaemon(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._daemon.stop()
        self.destroy()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=COLORS["surface"], height=70)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        logo = tk.Canvas(hdr, width=46, height=46,
                         bg=COLORS["surface"], highlightthickness=0)
        logo.place(x=18, y=12)
        logo.create_oval(0, 0, 46, 46, fill=COLORS["accent"], outline="")
        logo.create_text(23, 23, text="🔒", font=("Segoe UI Emoji", 18))
        tk.Label(hdr, text="JH SECURE",
                 font=("Consolas", 20, "bold"),
                 fg=COLORS["accent"], bg=COLORS["surface"]).place(x=74, y=12)
        tk.Label(hdr, text="Portable Folder Lock  v2.0",
                 font=("Consolas", 8), fg=COLORS["subtext"],
                 bg=COLORS["surface"]).place(x=76, y=44)
        tk.Frame(self, bg=COLORS["accent"], height=2).pack(fill="x")

        # Tab bar
        tab_bar = tk.Frame(self, bg=COLORS["surface"])
        tab_bar.pack(fill="x")
        self._tab_btns = {}
        for name, label in [("home","HOME"), ("batch","BATCH LOCK"),
                             ("log","LOG")]:
            b = tk.Label(tab_bar, text=label, font=("Consolas", 8, "bold"),
                         fg=COLORS["subtext"], bg=COLORS["surface"],
                         padx=20, pady=10, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, n=name: self._switch_tab(n))
            self._tab_btns[name] = b
        tk.Frame(self, bg=COLORS["border"], height=1).pack(fill="x")

        # Pages
        self.pages = {}
        for name in ("home", "batch", "log"):
            f = tk.Frame(self, bg=COLORS["bg"])
            self.pages[name] = f

        self._build_home(self.pages["home"])
        self._build_batch(self.pages["batch"])
        self._build_log_page(self.pages["log"])
        self._switch_tab("home")

    def _switch_tab(self, name):
        for n, f in self.pages.items():
            f.pack_forget()
        for n, b in self._tab_btns.items():
            b.config(fg=COLORS["accent"] if n == name else COLORS["subtext"])
        self.pages[name].pack(fill="both", expand=True)
        if name == "log":
            self._refresh_log_page()

    # ── HOME TAB ─────────────────────────────────────────────────────────────

    def _build_home(self, page):
        body = tk.Frame(page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=18)

        # Folder picker row
        tk.Label(body, text="SELECTED FOLDER", font=("Consolas", 7),
                 fg=COLORS["subtext"], bg=COLORS["bg"]).pack(anchor="w")
        ff = tk.Frame(body, bg=COLORS["card"])
        ff.pack(fill="x", pady=(5, 0))
        self.folder_label = tk.Label(ff, text="No folder selected",
                                     font=("Consolas", 9),
                                     fg=COLORS["subtext"], bg=COLORS["card"],
                                     anchor="w", padx=12, pady=10,
                                     wraplength=420)
        self.folder_label.pack(side="left", fill="x", expand=True)
        browse = tk.Label(ff, text=" Browse ", font=("Consolas", 8, "bold"),
                          fg=COLORS["bg"], bg=COLORS["accent"],
                          cursor="hand2", padx=10, pady=10)
        browse.pack(side="right")
        browse.bind("<Button-1>", lambda e: self._browse())

        # Status card
        sf = tk.Frame(body, bg=COLORS["card"])
        sf.pack(fill="x", pady=(12, 0))
        self.status_c = tk.Canvas(sf, bg=COLORS["card"],
                                  highlightthickness=0, height=56)
        self.status_c.pack(fill="x", padx=16, pady=10)
        self._draw_status(None)

        # Action buttons — 4 across
        row = tk.Frame(body, bg=COLORS["bg"])
        row.pack(fill="x", pady=(16, 0))
        btns = [
            ("🔒  LOCK",    self._lock_action,    COLORS["danger"],  130),
            ("🔓  UNLOCK",  self._unlock_action,  COLORS["success"], 130),
            ("🔑  CHANGE",  self._change_pin,     COLORS["purple"],  120),
            ("📋  LOG",     self._view_log,       COLORS["accent"],  100),
        ]
        for i, (txt, cmd, col, w) in enumerate(btns):
            make_btn(row, txt, cmd, color=col, w=w, h=48).grid(
                row=0, column=i, padx=(0, 6))

        # Recent list
        tk.Label(body, text="RECENT FOLDERS", font=("Consolas", 7),
                 fg=COLORS["subtext"], bg=COLORS["bg"]).pack(anchor="w",
                                                             pady=(20, 5))
        lf = tk.Frame(body, bg=COLORS["card"])
        lf.pack(fill="both", expand=True)
        sb = tk.Scrollbar(lf, orient="vertical", bg=COLORS["border"])
        self.folder_list = tk.Listbox(lf, bg=COLORS["card"],
                                      fg=COLORS["text"],
                                      selectbackground=COLORS["accent"],
                                      selectforeground=COLORS["bg"],
                                      font=("Consolas", 9), relief="flat",
                                      bd=0, yscrollcommand=sb.set,
                                      activestyle="none",
                                      highlightthickness=0)
        sb.config(command=self.folder_list.yview)
        sb.pack(side="right", fill="y")
        self.folder_list.pack(fill="both", expand=True, padx=2)
        self.folder_list.bind("<<ListboxSelect>>", self._on_list_sel)
        self.folder_list.bind("<Double-Button-1>", self._on_list_dbl)

        footer = tk.Frame(page, bg=COLORS["surface"], height=30)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, text="JH Secure v2.0  •  Portable  •  Encrypted  •  Brute-Force Protected",
                 font=("Consolas", 7), fg=COLORS["subtext"],
                 bg=COLORS["surface"]).pack(expand=True)

    # ── BATCH TAB ────────────────────────────────────────────────────────────

    def _build_batch(self, page):
        body = tk.Frame(page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=20)

        tk.Label(body, text="BATCH OPERATIONS",
                 font=("Consolas", 14, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", pady=(0, 4))
        tk.Label(body, text="Select multiple folders to lock or batch-unlock with master PIN.",
                 font=("Consolas", 8), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(anchor="w", pady=(0, 14))

        lf = tk.Frame(body, bg=COLORS["card"])
        lf.pack(fill="both", expand=True)
        sb = tk.Scrollbar(lf, orient="vertical")
        self.batch_list = tk.Listbox(lf, bg=COLORS["card"],
                                     fg=COLORS["text"],
                                     selectbackground=COLORS["accent"],
                                     selectforeground=COLORS["bg"],
                                     font=("Consolas", 9), relief="flat",
                                     bd=0, yscrollcommand=sb.set,
                                     activestyle="none",
                                     highlightthickness=0,
                                     selectmode="multiple")
        sb.config(command=self.batch_list.yview)
        sb.pack(side="right", fill="y")
        self.batch_list.pack(fill="both", expand=True, padx=2)

        r1 = tk.Frame(body, bg=COLORS["bg"])
        r1.pack(fill="x", pady=(12, 0))
        make_btn(r1, "+ ADD FOLDER",  self._batch_add,
                 color=COLORS["accent"],  w=155, h=40).pack(side="left", padx=(0, 8))
        make_btn(r1, "✕ REMOVE",      self._batch_remove,
                 color=COLORS["subtext"], w=120, h=40).pack(side="left")

        tk.Frame(body, bg=COLORS["border"], height=1).pack(fill="x", pady=14)

        r2 = tk.Frame(body, bg=COLORS["bg"])
        r2.pack(fill="x")
        make_btn(r2, "🔒  LOCK ALL",     self._batch_lock,
                 color=COLORS["danger"],  w=170, h=46).pack(side="left", padx=(0, 12))
        make_btn(r2, "🗝️  BATCH UNLOCK", self._batch_unlock,
                 color=COLORS["purple"],  w=200, h=46).pack(side="left")

        self.batch_status = tk.Label(body, text="",
                                     font=("Consolas", 8),
                                     fg=COLORS["subtext"], bg=COLORS["bg"],
                                     justify="left")
        self.batch_status.pack(anchor="w", pady=(12, 0))

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
            LockSetupWindow(self, fp,
                            on_success=self._refresh_batch)

    def _batch_unlock(self):
        locked = [f for f in self.batch_folders if is_locked(f)]
        if not locked:
            self.batch_status.config(text="No locked folders."); return
        BatchUnlockWindow(self, locked, on_done=self._refresh_batch)

    # ── LOG TAB ──────────────────────────────────────────────────────────────

    def _build_log_page(self, page):
        self._log_page      = page
        self._log_page_body = tk.Frame(page, bg=COLORS["bg"])
        self._log_page_body.pack(fill="both", expand=True, padx=24, pady=20)

    def _refresh_log_page(self):
        for w in self._log_page_body.winfo_children():
            w.destroy()
        body = self._log_page_body

        tk.Label(body, text="ACCESS LOGS",
                 font=("Consolas", 14, "bold"),
                 fg=COLORS["text"], bg=COLORS["bg"]).pack(anchor="w", pady=(0, 4))

        if not self.current_folder:
            tk.Label(body,
                     text="Select a folder from the HOME tab first.",
                     font=("Consolas", 9), fg=COLORS["subtext"],
                     bg=COLORS["bg"]).pack(anchor="w", pady=20)
            return

        tk.Label(body, text=f"📁  {self.current_folder}",
                 font=("Consolas", 8), fg=COLORS["subtext"],
                 bg=COLORS["bg"]).pack(anchor="w", pady=(0, 12))

        cols = ("Time", "Event", "Detail")
        tv   = ttk.Treeview(body, columns=cols, show="headings", height=20)
        s    = ttk.Style()
        s.configure("L.Treeview",
                    background=COLORS["card"], foreground=COLORS["text"],
                    fieldbackground=COLORS["card"])
        s.configure("L.Treeview.Heading",
                    background=COLORS["surface"], foreground=COLORS["accent"],
                    font=("Consolas", 8, "bold"))
        s.map("L.Treeview",
              background=[("selected", COLORS["accent2"])],
              foreground=[("selected", COLORS["bg"])])
        tv.configure(style="L.Treeview")
        for col, w in zip(cols, [165, 155, 210]):
            tv.heading(col, text=col.upper())
            tv.column(col, width=w, anchor="w")

        sb = tk.Scrollbar(body, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)

        entries = _load_log(self.current_folder)
        if not entries:
            tv.insert("", "end", values=("—", "No entries", ""))
        else:
            for e in reversed(entries):
                tv.insert("", "end",
                          values=(e["time"], e["event"], e.get("detail","")))

        make_btn(body, "CLEAR LOG", self._clear_log,
                 color=COLORS["danger"], w=140, h=36).pack(
                     anchor="e", pady=(10, 0))

    def _clear_log(self):
        if self.current_folder and messagebox.askyesno(
                "Clear Log", "Delete all log entries?"):
            _save_log(self.current_folder, [])
            self._refresh_log_page()

    # ── Actions ──────────────────────────────────────────────────────────────

    def _browse(self):
        path = filedialog.askdirectory(title="Select Folder")
        if path:
            self._select(path)

    def _select(self, path):
        self.current_folder = path
        self.folder_label.config(text=f"📁  {path}", fg=COLORS["text"])
        self._draw_status(path)
        self._add_recent(path)
        self._refresh_list()

    def _draw_status(self, path):
        self.status_c.delete("all")
        if not path:
            self.status_c.create_text(
                20, 14, text="●  No folder selected",
                fill=COLORS["subtext"], font=("Consolas", 10), anchor="w")
            return
        locked = is_locked(path)
        color  = COLORS["danger"] if locked else COLORS["success"]
        state  = "LOCKED" if locked else "UNLOCKED"
        self.status_c.create_oval(0, 5, 16, 21, fill=color, outline="")
        self.status_c.create_text(
            24, 13, text=f"{state}  —  {Path(path).name}",
            fill=color, font=("Consolas", 10, "bold"), anchor="w")
        # Auto-lock info
        if not locked:
            lf = get_lock_file(path)
            if os.path.exists(lf):
                try:
                    with open(lf) as f:
                        d = json.load(f)
                    als = d.get("auto_lock_seconds", 0)
                    uat = d.get("unlocked_at", 0)
                    if als and uat:
                        rem = int((uat + als) - time.time())
                        if rem > 0:
                            self.status_c.create_text(
                                24, 32,
                                text=f"⏱  Auto-lock in {rem//60}m {rem%60:02d}s",
                                fill=COLORS["warning"],
                                font=("Consolas", 8), anchor="w")
                except Exception:
                    pass

    def _lock_action(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Select a folder first."); return
        if is_locked(self.current_folder):
            messagebox.showinfo("Locked", "Already locked."); return
        LockSetupWindow(self, self.current_folder,
                        on_success=self._refresh_view)

    def _unlock_action(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Select a folder first."); return
        if not is_locked(self.current_folder):
            messagebox.showinfo("Unlocked", "Not locked."); return
        UnlockWindow(self, self.current_folder,
                     on_success=self._refresh_view)

    def _change_pin(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Select a folder first."); return
        if not is_locked(self.current_folder):
            messagebox.showinfo("Not Locked", "Lock it first."); return
        ChangePINWindow(self, self.current_folder)

    def _view_log(self):
        if not self.current_folder:
            messagebox.showwarning("No Folder", "Select a folder first."); return
        LogViewerWindow(self, self.current_folder)

    def _refresh_view(self):
        self._draw_status(self.current_folder)
        self._refresh_list()

    # ── List ─────────────────────────────────────────────────────────────────

    def _on_list_sel(self, e):
        sel = self.folder_list.curselection()
        if sel:
            self._select(self.recent_folders[sel[0]])

    def _on_list_dbl(self, e):
        sel = self.folder_list.curselection()
        if not sel:
            return
        path = self.recent_folders[sel[0]]
        if is_locked(path):
            UnlockWindow(self, path, on_success=self._refresh_view)
        else:
            LockSetupWindow(self, path, on_success=self._refresh_view)

    def _refresh_list(self):
        self.folder_list.delete(0, "end")
        for fp in self.recent_folders:
            lo, _ = is_locked_out(fp)
            lock_icon = "🔒" if is_locked(fp) else "🔓"
            warn = "  ⚠" if lo else ""
            self.folder_list.insert(
                "end", f"  {lock_icon}  {Path(fp).name}{warn}  —  {fp}")

    def _add_recent(self, path):
        if path in self.recent_folders:
            self.recent_folders.remove(path)
        self.recent_folders.insert(0, path)
        self.recent_folders = self.recent_folders[:20]
        self._save_recent()

    # ── Auto-Lock ────────────────────────────────────────────────────────────

    def check_auto_locks(self):
        for fp in self.recent_folders:
            lf = get_lock_file(fp)
            if not os.path.exists(lf):
                continue
            try:
                with open(lf) as f:
                    d = json.load(f)
                als = d.get("auto_lock_seconds", 0)
                uat = d.get("unlocked_at", 0)
                if als and uat and not d.get("locked", False):
                    if time.time() > uat + als:
                        create_lock(fp, "__auto__", "", "")
                        write_log(fp, "AUTO_LOCKED", f"After {als}s")
            except Exception:
                pass
        self._draw_status(self.current_folder)
        self._refresh_list()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _cfg_path(self):
        base = (os.path.dirname(sys.executable)
                if getattr(sys, 'frozen', False)
                else os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, ".jhsecure_app.json")

    def _load_recent(self):
        try:
            with open(self._cfg_path()) as f:
                return json.load(f).get("recent", [])
        except Exception:
            return []

    def _save_recent(self):
        try:
            with open(self._cfg_path(), 'w') as f:
                json.dump({"recent": self.recent_folders}, f)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = JHSecureApp()
    app.mainloop()
