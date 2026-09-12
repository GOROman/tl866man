#!/usr/bin/env python3
"""Local, read-only MiniPRO bridge. Python standard library only."""
import argparse
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
USB = threading.Lock()
STATE = threading.Lock()
JOB = None
DATA = b""
DEVICES = None
BANK0 = b""
SC88_CHIP = "AM27C4096@DIP40"
BANK_SIZE = 512 * 1024
HSP_PROFILE_NAME = "HSP-08-0 PRG · LH2310 / DIP-28"

# HSP-08-0 is the PRG position on the HVC-SLROM-02 MMC1 board.  This is the
# Nintendo mask-ROM pinout, not the JEDEC pinout of a 27C010.  Keep it as a
# profile so selecting the part shows the wiring before any read is allowed.
HSP_PROFILE = {
    "name": HSP_PROFILE_NAME,
    "kind": "nes_mmc1_prg",
    "reader_chip": "AM27C010@DIP32",
    "info": "HSP-08-0 PRG / Sharp LH2310 0S / 128 KiB / Mask ROM / DIP-28\nMMC1 · HVC-SLROM-02 · PRG0\nTL866CSへ直挿し不可。LH2310の専用ピン配置をAM27C010@DIP32へ変換する配線アダプターが必要です。",
    "pins": [
        {"pin": 1, "signal": "PRG A15", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 2, "signal": "PRG A12", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 3, "signal": "PRG A7", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 4, "signal": "PRG A6", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 5, "signal": "PRG A5", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 6, "signal": "PRG A4", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 7, "signal": "PRG A3", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 8, "signal": "PRG A2", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 9, "signal": "PRG A1", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 10, "signal": "PRG A0", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 11, "signal": "PRG D0", "role": "DATA", "note": "データ出力"},
        {"pin": 12, "signal": "PRG D1", "role": "DATA", "note": "データ出力"},
        {"pin": 13, "signal": "PRG D2", "role": "DATA", "note": "データ出力"},
        {"pin": 14, "signal": "GND", "role": "POWER", "note": "0 V"},
        {"pin": 15, "signal": "PRG D3", "role": "DATA", "note": "データ出力"},
        {"pin": 16, "signal": "PRG D4", "role": "DATA", "note": "データ出力"},
        {"pin": 17, "signal": "PRG D5", "role": "DATA", "note": "データ出力"},
        {"pin": 18, "signal": "PRG D6", "role": "DATA", "note": "データ出力"},
        {"pin": 19, "signal": "PRG D7", "role": "DATA", "note": "データ出力"},
        {"pin": 20, "signal": "PRG /CE", "role": "CTRL", "note": "チップイネーブル、アクティブLow"},
        {"pin": 21, "signal": "PRG A10", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 22, "signal": "PRG A16", "role": "ADDR", "note": "MMC1 PRGバンク上位"},
        {"pin": 23, "signal": "PRG A11", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 24, "signal": "PRG A9", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 25, "signal": "PRG A8", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 26, "signal": "PRG A13", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 27, "signal": "PRG A14", "role": "ADDR", "note": "アドレス入力"},
        {"pin": 28, "signal": "+5V", "role": "POWER", "note": "電源"},
    ],
    "warnings": [
        "切り欠きを上に見たLH2310 DIP-28のピン番号です。",
        "27C010のJEDECピン番号とは異なります。LH2310をTL866CSのZIFへ直挿ししないでください。",
        "配線アダプターはLH2310の28ピン信号をAM27C010@DIP32として提示する必要があります。既製品のピン配置を必ず照合してください。",
    ],
}
PROFILES = {HSP_PROFILE_NAME: HSP_PROFILE}


def run(args, timeout=15):
    binary = shutil.which("minipro")
    if not binary:
        raise RuntimeError("minipro がありません。brew install minipro を実行してください。")
    p = subprocess.run([binary] + args, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")


def devices():
    global DEVICES
    if DEVICES is None:
        code, out, err = run(["-q", "TL866A", "-l"])
        if code:
            raise RuntimeError(err or out)
        DEVICES = sorted(set(out.splitlines()), key=str.casefold)
    return DEVICES


def read_rom(chip, sc88_bank=None, display_chip=None):
    global DATA, BANK0
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="tl866man-") as folder:
            dest = Path(folder) / "rom.bin"
            # Fixed read command: no arbitrary options, ID override or write path.
            command = [shutil.which("minipro"), "-p", chip, "-c", "code", "-r", str(dest)]
            if sc88_bank is not None:
                # Adapter presents a 27C4096 bus, not its JEDEC ID. Read only.
                command.append("-x")
            proc = subprocess.Popen(command,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            timed_out = threading.Event()
            def expire():
                timed_out.set()
                proc.kill()
            timer = threading.Timer(180, expire)
            timer.start()
            try:
                while True:
                    chunk = proc.stdout.read1(1024)
                    if not chunk:
                        break
                    with STATE:
                        JOB["log"] = (JOB["log"] + chunk.decode(errors="replace").replace("\r", "\n"))[-40000:]
                code = proc.wait()
            finally:
                timer.cancel()
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            if timed_out.is_set():
                raise RuntimeError("読み出しが180秒でタイムアウトしました。接続を確認してください。")
            if code != 0:
                raise RuntimeError("読み出しに失敗しました。実行ログを確認してください。")
            if not dest.exists() or not dest.stat().st_size:
                raise RuntimeError("読み出しデータがありません。")
            data = dest.read_bytes()
            if sc88_bank is not None:
                if len(data) != BANK_SIZE:
                    raise RuntimeError(f"バンク容量が不一致です: {len(data)} / {BANK_SIZE} bytes")
                if sc88_bank == 0:
                    with STATE:
                        BANK0 = data
                        JOB.update(status="waiting_bank", bank=0, bank0_seconds=time.monotonic() - started,
                                   bank0_sha256=hashlib.sha256(data).hexdigest())
                    return
                if len(BANK0) != BANK_SIZE:
                    raise RuntimeError("Bank 0がありません。最初から読み出してください。")
                with STATE:
                    JOB["banks_identical"] = BANK0 == data
                data = BANK0 + data
            with STATE:
                DATA = data
                JOB.update(status="done", chip=display_chip or JOB.get("chip", chip), size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                           seconds=round(time.monotonic() - started + JOB.get("bank0_seconds", 0), 2),
                           uniform=len(set(data)) == 1)
                BANK0 = b""
    except Exception as exc:
        with STATE:
            JOB.update(status="error", error=str(exc))
    finally:
        USB.release()


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, data, mime="application/json; charset=utf-8", filename=None):
        body = json.dumps(data, ensure_ascii=False).encode() if mime.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'")
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        hosts = {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}
        return self.headers.get("Host") in hosts and self.headers.get("Origin") in (None, *["http://" + h for h in hosts])

    def do_GET(self):
        if not self.trusted():
            return self.reply(403, {"error": "Local access only"})
        try:
            self.get()
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            self.reply(400, {"error": str(exc)})

    def get(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/session":
            return self.reply(200, {"token": TOKEN})
        if url.path == "/api/status":
            if not USB.acquire(False):
                return self.reply(200, {"busy": True, "connected": None, "log": "読み出し中"})
            try:
                code, out, err = run(["-k"])
                log = re.sub(r"(?m)^(?:Serial code|Device code):.*\n?", "", out + err)
                connected = code == 0 and "TL866CS" in log
                return self.reply(200, {"connected": connected, "busy": False, "log": log})
            finally:
                USB.release()
        if url.path == "/api/devices":
            term = query.get("q", [""])[0].casefold()
            matches = [d for d in list(devices()) + list(PROFILES) if term in d.casefold()]
            return self.reply(200, {"devices": matches[:150], "total": len(matches)})
        if url.path == "/api/device":
            chip = query.get("name", [""])[0]
            if chip in PROFILES:
                profile = PROFILES[chip]
                return self.reply(200, {"info": profile["info"], "profile": profile["kind"], "name": profile["name"],
                                        "reader_chip": profile["reader_chip"], "pins": profile["pins"],
                                        "warnings": profile["warnings"], "adapter_required": True})
            if chip not in devices():
                raise ValueError("候補から型番を選択してください。")
            code, out, err = run(["-q", "TL866A", "-d", chip])
            if code:
                raise RuntimeError(err or out)
            return self.reply(200, {"info": out + err})
        if url.path == "/api/job":
            with STATE:
                return self.reply(200, JOB or {"status": "idle", "log": ""})
        if url.path in ("/api/hex", "/api/download"):
            with STATE:
                if not JOB or JOB["status"] != "done":
                    return self.reply(409, {"error": "完了した読み出しがありません。"})
                data, chip = DATA, JOB["chip"]
            if url.path == "/api/download":
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", chip) + ".bin"
                return self.reply(200, data, "application/octet-stream", name)
            offset = max(0, min(int(query.get("offset", ["0"])[0]), max(0, len(data) - 1))) // 256 * 256
            lines = []
            for start in range(offset, min(offset + 256, len(data)), 16):
                row = data[start:start + 16]
                lines.append(f"{start:08X}  " + " ".join(f"{b:02X}" for b in row).ljust(47) + "  " + "".join(chr(b) if 32 <= b < 127 else "." for b in row))
            return self.reply(200, {"text": "\n".join(lines), "offset": offset, "size": len(data)})
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if url.path in files:
            name, mime = files[url.path]
            return self.reply(200, (ROOT / "static" / name).read_bytes(), mime)
        self.reply(404, {"error": "Not found"})

    def do_POST(self):
        global JOB, DATA, BANK0
        if not self.trusted() or not secrets.compare_digest(self.headers.get("X-Session-Token", ""), TOKEN):
            return self.reply(403, {"error": "ページを再読み込みしてください。"})
        if self.path not in ("/api/read", "/api/sc88/read"):
            return self.reply(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 4096:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Invalid request")
            sc88 = self.path == "/api/sc88/read"
            bank = body.get("bank") if sc88 else None
            if sc88:
                if type(bank) is not int or bank not in (0, 1):
                    raise ValueError("Invalid bank")
                if body.get("adapter_confirmed") is not True:
                    raise ValueError("ROMと変換アダプターの適合・設定を確認してください。")
            chip = SC88_CHIP if sc88 else body.get("chip")
            profile = None if sc88 else PROFILES.get(chip)
            reader_chip = chip
            if profile:
                reader_chip = profile["reader_chip"]
                if body.get("profile_adapter_confirmed") is not True:
                    raise ValueError("LH2310用の配線アダプターを用意し、ピン配置を確認してください。")
            if not isinstance(chip, str) or (chip not in devices() and not profile) or chip.startswith("-"):
                raise ValueError("候補から型番を選択してください。")
            if body.get("confirmed") is not True:
                raise ValueError("型番・装着位置・向きを確認してください。")
            if not USB.acquire(False):
                return self.reply(409, {"error": "機器を使用中です。"})
            try:
                with STATE:
                    if sc88 and bank == 1:
                        if not JOB or JOB["status"] != "waiting_bank" or body.get("job_id") != JOB.get("id"):
                            raise ValueError("対応するBank 0がありません。ページを更新してください。")
                    elif JOB and JOB["status"] == "waiting_bank" and not sc88:
                        raise ValueError("SC-88ProのBank 1を読み出してから操作してください。")
                code, out, err = run(["-k"])
                if code or "TL866CS" not in out + err:
                    raise RuntimeError("TL866CSを接続してください。")
                with STATE:
                    DATA = b""
                    if sc88 and bank == 1:
                        JOB.update(status="running", bank=1, log=JOB["log"] + "\n--- Bank 1 ---\n")
                    else:
                        BANK0 = b""
                        JOB = {"id": secrets.token_hex(8), "status": "running", "chip": "SC-88Pro_PRGROM" if sc88 else chip,
                               "profile": "sc88" if sc88 else (profile["kind"] if profile else "generic"), "bank": bank,
                               "reader_chip": reader_chip,
                               "log": "--- Bank 0 / adapter read; ID check skipped ---\n" if sc88 else
                                      (f"--- {chip} / reader profile {reader_chip} ---\n" if profile else ""), "started": time.time()}
                threading.Thread(target=read_rom, args=(reader_chip, bank, "SC-88Pro_PRGROM" if sc88 else chip), daemon=True).start()
            except Exception:
                USB.release()
                raise
            self.reply(202, {"status": "running"})
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            self.reply(400, {"error": str(exc)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8660)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"TL866man: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
