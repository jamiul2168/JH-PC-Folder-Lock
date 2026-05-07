# 🔒 JH Secure v2.0

**Portable Folder Lock Application for Windows**

[![Build JH Secure](https://github.com/YOUR_USERNAME/jh-secure/actions/workflows/build.yml/badge.svg)](https://github.com/YOUR_USERNAME/jh-secure/actions/workflows/build.yml)

---

## ✨ Features

### 🔐 Security
| Feature | Description |
|---------|-------------|
| 🔒 **Folder Lock** | Lock any folder with 6-digit PIN |
| 👻 **Fake Password** | Decoy PIN reveals fake/empty files |
| 🛡️ **Brute Force Protection** | Lockout after 5 wrong PINs (5 min cooldown) |
| 🔑 **Change PIN** | Change PIN using old PIN |
| 🔐 **Secret Recovery** | Reset PIN via secret question/answer |
| 🗝️ **Master PIN** | One PIN to batch-unlock all folders |

### ⏱️ Auto-Lock
| Timer | Description |
|-------|-------------|
| Never / 1 min / 5 min | Folder auto re-locks after unlock |
| 15 min / 30 min / 1 hr | Timer shown in status bar |

### 📋 Logging
| Log Type | Description |
|----------|-------------|
| **Access Log** | Every lock/unlock with timestamp |
| **Failed Attempt Log** | Wrong PIN attempts with timestamp |
| **Lockout Log** | Brute force lockout events |

### 📁 Batch Operations
| Feature | Description |
|---------|-------------|
| **Multiple Folder Lock** | Manage many folders in Batch tab |
| **Batch Unlock** | One master PIN unlocks selected folders |

### 💻 Portability
- Single `.exe` — no installation
- `.jhsecure` lock file travels **with** the folder
- Lock on PC-A, unlock on PC-B

---

## 📥 Download

Go to [**Releases**](../../releases/latest) → download `JH-Secure-vX.X.X-Windows.zip`

---

## 🚀 How to Use

### Lock a Folder
1. Open `JH Secure.exe`
2. Browse → select folder → **🔒 LOCK**
3. Set 6-digit PIN + secret question
4. Optional: Fake PIN, Master PIN, Auto-Lock timer
5. Done! `.jhsecure` stored inside folder

### Unlock
1. Open `JH Secure.exe` on **any PC**
2. Select folder → **🔓 UNLOCK** → enter PIN

### Fake Password (Decoy PIN)
- Set a separate decoy PIN during lock setup
- Enter decoy PIN when forced → shows empty/fake files
- Real files remain encrypted

### Batch Unlock
1. Go to **BATCH** tab → add folders
2. Click **Batch Unlock** → enter master PIN
3. All matching folders unlock at once

### Brute Force Protection
- 5 wrong PIN attempts → 5-minute lockout
- Countdown shown in unlock window
- All attempts logged with timestamp

---

## 🏗️ Build from Source

```bash
git clone https://github.com/YOUR_USERNAME/jh-secure.git
cd jh-secure
pip install pyinstaller pyinstaller-hooks-contrib
pyinstaller jh_secure.spec --clean --noconfirm
# Output: dist/JH Secure.exe
```

### Create a Release
```bash
git tag v2.0.0
git push origin v2.0.0
# GitHub Actions auto-builds and publishes the release
```

---

## 📁 Project Structure

```
jh-secure/
├── src/
│   └── jh_secure.py          # Main app (1500+ lines)
├── .github/
│   └── workflows/
│       └── build.yml          # CI/CD: lint → build → release
├── jh_secure.spec             # PyInstaller config
├── requirements.txt
└── README.md
```

### Files created per locked folder
```
your-folder/
├── .jhsecure          # Encrypted data + PIN hash + settings
├── .jhsecure_log      # Access + failed attempt log
└── .jhsecure_lockout  # Brute force counter
```

---

## 🔐 Security Notes

- **PIN hashing**: PBKDF2-SHA256, 200,000 iterations, 32-byte random salt
- **Encryption**: XOR cipher with PBKDF2-derived 32-byte key
- **Secret Answer**: SHA-256 hashed (case-insensitive)
- **Brute Force**: Hard lockout after 5 attempts (configurable in source)
- **Portable**: All credentials live in `.jhsecure` — works on any machine

> ⚠️ For highly sensitive data, combine with full-disk encryption (BitLocker/VeraCrypt).

---

## 📜 License

MIT License — Free to use, modify, distribute.

---

**JH Secure v2.0** | Made with ❤️
