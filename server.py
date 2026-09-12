#!/usr/bin/env python3
"""Local, read-only MiniPRO bridge. Python standard library only."""
import argparse
import hashlib
import json
import os
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
CANCEL_EVENT = None
ACTIVE_PROCESS = None
ACTIVE_PROCESS_LOCK = threading.Lock()
SC88_CHIP = "AM27C4096@DIP40"
BANK_SIZE = 512 * 1024
HSP_PROFILE_NAME = "HSP-08-0 PRG · LH2310 / DIP-28"
DEFAULT_SPEED = "normal"
SPEED_PRESETS = {
    "fast": {"label": "高速", "delay_us": 0},
    "normal": {"label": "標準", "delay_us": 50},
    "slow": {"label": "低速・安定", "delay_us": 250},
    "safe": {"label": "最低速・検証用", "delay_us": 1000},
}


class ReadCancelled(Exception):
    """Raised inside the worker after the user cancels a read."""

# HSP-08-0 is the PRG position on the HVC-SLROM-02 MMC1 board.  This is the
# Nintendo mask-ROM pinout, not the JEDEC pinout of a 27C010.  Keep it as a
# profile so selecting the part shows the wiring before any read is allowed.
HSP_PROFILE = {
    "name": HSP_PROFILE_NAME,
    "kind": "nes_mmc1_prg",
    "reader_chip": "AM27C010@DIP32",
    "direct_reader_chip": HSP_PROFILE_NAME,
    "info": "HSP-08-0 PRG / Sharp LH2310 0S / 128 KiB / Mask ROM / DIP-28\nMMC1 · HVC-SLROM-02 · PRG0\n標準miniproでは直挿し不可。LH2310の専用ピン配置をAM27C010@DIP32へ変換する配線アダプターが必要です。",
    "direct_info": "HSP-08-0 PRG / Sharp LH2310 0S / 128 KiB / Mask ROM / DIP-28\nMMC1 · HVC-SLROM-02 · PRG0\nカスタムPROMビットバン対応miniproで、LH2310のピン配置をTL866CSのZIFから直接制御します（読み出し専用）。",
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
        "27C010のJEDECピン番号とは異なります。AM27C010設定の配線アダプターを使う場合は、信号を必ず照合してください。",
    ],
    "adapter_warning": "配線アダプターはLH2310の28ピン信号をAM27C010@DIP32として提示する必要があります。既製品のピン配置を必ず照合してください。",
    "direct_warning": "カスタムPROMビットバンは読み出し専用です。実チップでの読み出しは未検証のため、ピン番号・向き・5V条件を確認してから実行してください。",
}
PROFILES = {HSP_PROFILE_NAME: HSP_PROFILE}
CUSTOM_MINIPRO = ROOT / ".runtime" / "minipro-hsp" / "bin" / "minipro"
HSP_DIRECT_SUPPORTED = None


def minipro_binary():
    """Return the selected minipro binary without accepting CLI arguments."""
    override = os.environ.get("TL866MAN_MINIPRO", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise RuntimeError(f"指定したminiproがありません: {candidate}")
        return str(candidate)
    if CUSTOM_MINIPRO.is_file():
        return str(CUSTOM_MINIPRO)
    binary = shutil.which("minipro")
    if not binary:
        raise RuntimeError("minipro がありません。brew install minipro を実行してください。")
    return binary


def hsp_direct_supported():
    """Check whether the selected minipro includes the HSP custom PROM profile."""
    global HSP_DIRECT_SUPPORTED
    if HSP_DIRECT_SUPPORTED is None:
        try:
            code, out, _ = run(["-q", "TL866A", "-L", HSP_PROFILE_NAME])
            HSP_DIRECT_SUPPORTED = code == 0 and any(
                line.startswith(HSP_PROFILE_NAME) for line in out.splitlines()
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            HSP_DIRECT_SUPPORTED = False
    return HSP_DIRECT_SUPPORTED


def declared_size(chip):
    """Return the code-memory size reported by minipro, when available."""
    if chip == HSP_PROFILE_NAME:
        return 128 * 1024
    try:
        code, out, err = run(["-q", "TL866A", "-d", chip])
        if code:
            return None
        match = re.search(r"Memory:\s*([0-9][0-9,]*)\s+Bytes", out + err)
        return int(match.group(1).replace(",", "")) if match else None
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError):
        return None


def run(args, timeout=15):
    p = subprocess.run([minipro_binary()] + args, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")


def public_job(job):
    """Remove worker-only state before returning a job over the API."""
    return {key: value for key, value in job.items() if not key.startswith("_")}


def append_job_log(text="", flush=False):
    """Append process output as timestamped, complete log lines."""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    # minipro redraws its progress line with ANSI erase codes. Keep the
    # useful text in the browser log while retaining one timestamp per line.
    normalized = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    with STATE:
        if not JOB:
            return
        pending = JOB.get("_log_pending", "") + normalized
        lines = pending.split("\n")
        if flush:
            pending = ""
        else:
            pending = lines.pop()
        complete = [line for line in lines if line.strip()]
        if complete:
            stamp = time.strftime("%H:%M:%S")
            addition = "".join(f"[{stamp}] {line}\n" for line in complete)
            JOB["log"] = (JOB.get("log", "") + addition)[-40000:]
        JOB["_log_pending"] = pending


def devices():
    global DEVICES
    if DEVICES is None:
        code, out, err = run(["-q", "TL866A", "-l"])
        if code:
            raise RuntimeError(err or out)
        DEVICES = sorted({re.sub(r"\(custom\)$", "", line).rstrip() for line in out.splitlines() if line.strip()}, key=str.casefold)
    return DEVICES


def read_rom(chip, sc88_bank=None, display_chip=None, cancel_event=None, speed=DEFAULT_SPEED):
    global DATA, BANK0, CANCEL_EVENT, ACTIVE_PROCESS
    started = time.monotonic()
    cancel_event = cancel_event or threading.Event()
    speed_config = SPEED_PRESETS.get(speed, SPEED_PRESETS[DEFAULT_SPEED])
    proc = None
    timer = None
    timed_out = threading.Event()
    data_thread = None
    log_thread = None
    try:
        with tempfile.TemporaryDirectory(prefix="tl866man-") as folder:
            dest = Path(folder) / "rom.bin"
            with STATE:
                expected_size = JOB.get("expected_size") if JOB else None
                last_published = 0

            def publish_partial(force=False):
                nonlocal last_published
                global DATA
                try:
                    size = dest.stat().st_size
                    if size <= 0 or (not force and size < last_published + 256):
                        return
                    partial = dest.read_bytes()
                except (FileNotFoundError, OSError):
                    return
                if not partial or len(partial) <= last_published:
                    return
                with STATE:
                    if not JOB:
                        return
                    DATA = partial
                    JOB["partial_size"] = len(partial)
                    JOB["bytes_read"] = len(partial)
                    if expected_size:
                        JOB["progress"] = round(min(99.9, len(partial) * 100 / expected_size), 1)
                last_published = len(partial)

            def ensure_not_cancelled():
                if cancel_event.is_set():
                    raise ReadCancelled()

            # Read to stdout so a custom bit-bang minipro can stream each block.
            # Standard minipro versions may still emit the complete buffer at once.
            command = [minipro_binary(), "-p", chip, "-c", "code", "-r", "-"]
            if sc88_bank is not None:
                # Adapter presents a 27C4096 bus, not its JEDEC ID. Read only.
                command.append("-x")
            ensure_not_cancelled()
            child_env = os.environ.copy()
            child_env["TL866MAN_READ_DELAY_US"] = str(speed_config["delay_us"])
            child_env["TL866MAN_READ_SPEED"] = speed
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    bufsize=0, env=child_env)
            with ACTIVE_PROCESS_LOCK:
                ACTIVE_PROCESS = proc
            append_job_log(f"速度設定: {speed_config['label']} ({speed_config['delay_us']} µs/byte)\n", flush=True)

            def stream_data():
                try:
                    with dest.open("wb") as output:
                        while True:
                            chunk = proc.stdout.read(4096)
                            if not chunk:
                                break
                            output.write(chunk)
                            output.flush()
                            publish_partial()
                except (OSError, ValueError):
                    # The main worker owns process termination and records its result.
                    pass

            def stream_logs():
                while True:
                    chunk = proc.stderr.read(1024)
                    if not chunk:
                        break
                    append_job_log(chunk)
                    log_text = chunk.decode(errors="replace")
                    progress_matches = re.findall(r"(?<!\d)(\d{1,3})%", log_text)
                    if progress_matches:
                        with STATE:
                            if JOB:
                                JOB["progress"] = max(JOB.get("progress", 0),
                                                       min(99.9, float(progress_matches[-1])))

            data_thread = threading.Thread(target=stream_data, name="tl866man-data", daemon=True)
            log_thread = threading.Thread(target=stream_logs, name="tl866man-log", daemon=True)
            data_thread.start()
            log_thread.start()

            def expire():
                timed_out.set()
                try:
                    proc.kill()
                except OSError:
                    pass

            timer = threading.Timer(180, expire)
            timer.start()
            while proc.poll() is None:
                if cancel_event.is_set():
                    try:
                        proc.kill()
                    except OSError:
                        pass
                time.sleep(0.05)
            code = proc.wait()
            if data_thread:
                data_thread.join(timeout=2)
            if log_thread:
                log_thread.join(timeout=2)
            publish_partial(force=True)
            append_job_log("", flush=True)
            if cancel_event.is_set():
                raise ReadCancelled()
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
                        DATA = data
                        BANK0 = data
                        JOB.update(status="waiting_bank", bank=0, bank0_seconds=time.monotonic() - started,
                                   bank0_sha256=hashlib.sha256(data).hexdigest(), progress=100.0,
                                   partial_size=len(data), bytes_read=len(data))
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
                           uniform=len(set(data)) == 1, progress=100.0,
                           partial_size=len(data), bytes_read=len(data))
                BANK0 = b""
            else:
                with STATE:
                    DATA = data
                    JOB.update(status="done", chip=display_chip or JOB.get("chip", chip), size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                           seconds=round(time.monotonic() - started, 2),
                           uniform=len(set(data)) == 1, progress=100.0,
                           partial_size=len(data), bytes_read=len(data))
    except ReadCancelled:
        append_job_log("読み出しをキャンセルしました。取得済みデータを保持します。\n", flush=True)
        with STATE:
            if JOB:
                JOB.update(status="canceled", canceled=True, canceled_at=time.time(),
                           seconds=round(time.monotonic() - started, 2))
    except Exception as exc:
        append_job_log(f"ERROR: {exc}\n", flush=True)
        with STATE:
            if JOB:
                JOB.update(status="error", error=str(exc), seconds=round(time.monotonic() - started, 2))
    finally:
        if timer:
            timer.cancel()
        if proc:
            with ACTIVE_PROCESS_LOCK:
                if ACTIVE_PROCESS is proc:
                    ACTIVE_PROCESS = None
        with STATE:
            if CANCEL_EVENT is cancel_event:
                CANCEL_EVENT = None
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
            matches = sorted({d for d in list(devices()) + list(PROFILES) if term in d.casefold()}, key=str.casefold)
            return self.reply(200, {"devices": matches[:150], "total": len(matches)})
        if url.path == "/api/device":
            chip = query.get("name", [""])[0]
            if chip in PROFILES:
                profile = PROFILES[chip]
                direct = hsp_direct_supported() if chip == HSP_PROFILE_NAME else False
                warnings = list(profile["warnings"])
                if direct:
                    warnings.append(profile["direct_warning"])
                else:
                    warnings.append(profile["adapter_warning"])
                return self.reply(200, {"info": profile["direct_info"] if direct else profile["info"],
                                        "profile": profile["kind"], "name": profile["name"],
                                        "reader_chip": profile["direct_reader_chip"] if direct else profile["reader_chip"],
                                        "pins": profile["pins"], "warnings": warnings,
                                        "adapter_required": not direct, "direct_supported": direct})
            if chip not in devices():
                raise ValueError("候補から型番を選択してください。")
            code, out, err = run(["-q", "TL866A", "-d", chip])
            if code:
                raise RuntimeError(err or out)
            return self.reply(200, {"info": out + err})
        if url.path == "/api/job":
            with STATE:
                return self.reply(200, public_job(JOB) if JOB else {"status": "idle", "log": ""})
        if url.path in ("/api/hex", "/api/download"):
            with STATE:
                if not JOB or not DATA:
                    return self.reply(409, {"error": "表示できる読み出しデータがありません。"})
                if url.path == "/api/download" and JOB["status"] != "done":
                    return self.reply(409, {"error": "読み出し完了後にBINを保存できます。"})
                data, chip, partial = DATA, JOB["chip"], JOB["status"] != "done"
            if url.path == "/api/download":
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", chip) + ".bin"
                return self.reply(200, data, "application/octet-stream", name)
            offset = max(0, min(int(query.get("offset", ["0"])[0]), max(0, len(data) - 1))) // 256 * 256
            lines = []
            for start in range(offset, min(offset + 256, len(data)), 16):
                row = data[start:start + 16]
                lines.append(f"{start:08X}  " + " ".join(f"{b:02X}" for b in row).ljust(47) + "  " + "".join(chr(b) if 32 <= b < 127 else "." for b in row))
            return self.reply(200, {"text": "\n".join(lines), "offset": offset, "size": len(data), "partial": partial})
        files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}
        if url.path in files:
            name, mime = files[url.path]
            return self.reply(200, (ROOT / "static" / name).read_bytes(), mime)
        self.reply(404, {"error": "Not found"})

    def do_POST(self):
        global JOB, DATA, BANK0, CANCEL_EVENT
        if not self.trusted() or not secrets.compare_digest(self.headers.get("X-Session-Token", ""), TOKEN):
            return self.reply(403, {"error": "ページを再読み込みしてください。"})
        if self.path == "/api/cancel":
            with STATE:
                if not JOB or JOB.get("status") not in ("running", "canceling"):
                    return self.reply(409, {"error": "キャンセルできる読み出しがありません。"})
                job_id = JOB.get("id")
                event = CANCEL_EVENT
                JOB["status"] = "canceling"
                JOB["cancel_requested"] = True
            append_job_log("読み出しのキャンセルを要求しました。\n", flush=True)
            if event:
                event.set()
            with ACTIVE_PROCESS_LOCK:
                proc = ACTIVE_PROCESS
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            return self.reply(202, {"status": "canceling", "job_id": job_id})
        if self.path == "/api/clear":
            with STATE:
                if JOB and JOB.get("status") in ("running", "canceling"):
                    return self.reply(409, {"error": "読み出し中はメモリビューをクリアできません。"})
                if JOB and JOB.get("status") == "waiting_bank":
                    JOB = None
                elif JOB:
                    JOB["data_cleared"] = True
                DATA = b""
                BANK0 = b""
            return self.reply(200, {"status": "cleared"})
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
            direct_profile = False
            if profile:
                reader_chip = profile["reader_chip"]
                direct_profile = chip == HSP_PROFILE_NAME and hsp_direct_supported()
                if direct_profile:
                    reader_chip = profile["direct_reader_chip"]
                    if body.get("profile_direct_confirmed") is not True:
                        raise ValueError("LH2310のピン配置・向き・5V条件を確認してください。")
                elif body.get("profile_adapter_confirmed") is not True:
                    raise ValueError("LH2310用の配線アダプターを用意し、ピン配置を確認してください。")
            if not isinstance(chip, str) or (chip not in devices() and not profile) or chip.startswith("-"):
                raise ValueError("候補から型番を選択してください。")
            if body.get("confirmed") is not True:
                raise ValueError("型番・装着位置・向きを確認してください。")
            speed = body.get("speed", DEFAULT_SPEED)
            if not isinstance(speed, str) or speed not in SPEED_PRESETS:
                raise ValueError("読み出し速度の指定が不正です。")
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
                expected_size = BANK_SIZE if sc88 else declared_size(reader_chip)
                cancel_event = threading.Event()
                with STATE:
                    DATA = b""
                    if sc88 and bank == 1:
                        CANCEL_EVENT = cancel_event
                        JOB.update(status="running", bank=1, log=JOB["log"], expected_size=expected_size,
                                   progress=0.0, partial_size=0, bytes_read=0,
                                   speed=speed, cancel_requested=False, data_cleared=False)
                        JOB.pop("_log_pending", None)
                    else:
                        BANK0 = b""
                        CANCEL_EVENT = cancel_event
                        JOB = {"id": secrets.token_hex(8), "status": "running", "chip": "SC-88Pro_PRGROM" if sc88 else chip,
                               "profile": "sc88" if sc88 else (profile["kind"] if profile else "generic"), "bank": bank,
                               "reader_chip": reader_chip,
                               "log": "", "started": time.time(), "expected_size": expected_size,
                               "progress": 0.0, "partial_size": 0, "bytes_read": 0,
                               "speed": speed, "cancel_requested": False, "data_cleared": False}
                    header = ("Bank 0 / adapter read; ID check skipped" if sc88 else
                              (f"{chip} / {'direct custom PROM bit-bang' if direct_profile else f'adapter reader {reader_chip}'}" if profile else chip))
                append_job_log(f"--- {header} ---\n", flush=True)
                if sc88 and bank == 1:
                    append_job_log("--- Bank 1 ---\n", flush=True)
                threading.Thread(target=read_rom, args=(reader_chip, bank, "SC-88Pro_PRGROM" if sc88 else chip, cancel_event, speed), daemon=True).start()
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
