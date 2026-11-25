"""Run a Python script inside an isolated, non-interactive GUI Maya and collect its result.

    python tests/gui/run_gui.py 2027 tests/gui/gui_options_dialog.py --out result.json
           [--timeout 300] [--app-dir DIR] [--module-path PATH] [--shots DIR] [--keep-app-dir]

Isolation (never touches the user's own Maya session or preferences):
  * MAYA_APP_DIR is a throw-away folder (or --app-dir), so preferences, the Trust Center
    allow-list and pluginPrefs are fresh and private to this run;
  * MAYA_SKIP_USERSETUP_PY=1, MAYA_MODULE_PATH is exactly --module-path (default: none, so
    only Maya's own default module folders are searched), and PYTHONPATH / MAYA_SCRIPT_PATH /
    MAYA_PLUG_IN_PATH from the calling environment are dropped;
  * only the maya.exe process started here (and its child processes) is ever killed, by PID,
    after the script reports or the timeout expires.

The inner script is executed in Maya's __main__ once the UI is idle. It receives
I3D_GUI_OUT (JSON result path), I3D_GUI_SHOTS (screenshot folder) and I3D_GUI_ARGS in the
environment and must write I3D_GUI_OUT; gui_common.finish() does that and quits Maya.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

BOOT = r'''
python("import sys; sys.path.insert(0, r'%(here)s')");
python("import gui_common; gui_common.boot(r'%(script)s')");
'''


def maya_exe(version):
    return r"C:\Program Files\Autodesk\Maya%s\bin\maya.exe" % version


def window_titles(pid):
    """Titles of the visible top-level windows owned by process ``pid`` (Windows only):
    shows what Maya is waiting on (licence, crash or other startup dialogs)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    titles = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            titles.append(buf.value)
        return True

    user32.EnumWindows(proto(cb), 0)
    return titles


def _png(path, width, height, bgra):
    import struct
    import zlib
    rows = []
    stride = width * 4
    for y in range(height):
        row = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def capture_windows(pid, folder, prefix):
    """Save a PNG of every visible top-level window of process ``pid`` (PrintWindow: only
    that window's own pixels, nothing else on the desktop). Returns the file paths."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    user32.GetDC.restype = wintypes.HDC
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetDC.argtypes = (wintypes.HWND,)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT)
    hwnds = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(proto(cb), 0)
    out = []
    for k, hwnd in enumerate(hwnds):
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0 or w * h > 40000000:
            continue
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
        old = gdi32.SelectObject(dc, bmp)
        user32.PrintWindow(hwnd, dc, 2)

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bih), 0)
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(dc)
        user32.ReleaseDC(None, screen)
        path = os.path.join(folder, "%s_%d.png" % (prefix, k))
        _png(path, w, h, buf.raw)
        out.append(path)
    return out


def kill_tree(pid):
    """Kill the process tree rooted at a PID this harness started."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def run(version, script, out, timeout=300, app_dir=None, module_path=None, shots=None, args="",
        keep_app_dir=False, extra_env=None, autoload=False, capture=True):
    exe = maya_exe(version)
    if not os.path.exists(exe):
        return {"status": "not installed"}
    own_app = app_dir is None
    app_dir = app_dir or tempfile.mkdtemp(prefix="i3dgltf_gui_%s_" % version)
    shots = shots or os.path.join(app_dir, "shots")
    os.makedirs(shots, exist_ok=True)
    boot = os.path.join(app_dir, "boot.mel")
    with open(boot, "w", encoding="utf-8") as f:
        f.write(BOOT % {"here": HERE.replace("\\", "/"), "script": os.path.abspath(script).replace("\\", "/")})
    if os.path.exists(out):
        os.remove(out)
    env = dict(os.environ)
    for key in ("PYTHONPATH", "MAYA_SCRIPT_PATH", "MAYA_PLUG_IN_PATH", "PYTHONUTF8", "MAYA_MODULE_PATH",
                "XBMLANGPATH"):
        env.pop(key, None)
    env.update({"MAYA_APP_DIR": app_dir, "MAYA_SKIP_USERSETUP_PY": "1", "MAYA_DISABLE_CIP": "1",
                "MAYA_DISABLE_CER": "1", "MAYA_DISABLE_ADP": "1", "MAYA_NO_HOME": "1",
                "MAYA_NO_HOME_ICON": "1", "MAYA_DISABLE_CLIC_IPM": "1",
                "I3D_GUI_OUT": os.path.abspath(out), "I3D_GUI_SHOTS": os.path.abspath(shots),
                "I3D_GUI_ARGS": args, "I3D_GUI_REPO": REPO, "I3D_GUI_TIMEOUT": str(timeout)})
    if module_path:
        env["MAYA_MODULE_PATH"] = module_path
    env.update(extra_env or {})
    log = os.path.join(app_dir, "maya_gui.log")
    t0 = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as logf:
        cmd = [exe] + ([] if autoload else ["-noAutoloadPlugins"]) + ["-script", boot.replace("\\", "/")]
        proc = subprocess.Popen(cmd, env=env,
                                stdout=logf, stderr=subprocess.STDOUT, cwd=app_dir)
        status = None
        windows = []
        try:
            while time.time() - t0 < timeout:
                titles = window_titles(proc.pid)
                if titles and (not windows or windows[-1][1] != titles):
                    windows.append((round(time.time() - t0), titles))
                    if capture:
                        time.sleep(3)
                        try:
                            capture_windows(proc.pid, shots, "outer_%03d" % (time.time() - t0))
                        except Exception as exc:  # diagnostics only
                            windows.append(("capture failed", str(exc)))
                if proc.poll() is not None:
                    status = "exited %s" % proc.returncode
                    break
                if os.path.exists(out):
                    # give the script a moment to quit Maya itself
                    try:
                        proc.wait(20)
                        status = "exited %s" % proc.returncode
                    except subprocess.TimeoutExpired:
                        status = "result written, killed"
                    break
                time.sleep(1)
            else:
                status = "timeout"
        finally:
            if proc.poll() is None:
                if capture and not os.path.exists(out):
                    try:
                        capture_windows(proc.pid, shots, "outer_before_kill")
                    except Exception:
                        pass
                kill_tree(proc.pid)
                try:
                    proc.wait(30)
                except subprocess.TimeoutExpired:
                    status = (status or "") + " (kill failed: pid %d)" % proc.pid
    result = {"status": status, "seconds": round(time.time() - t0, 1), "log": log, "shots": shots,
              "app_dir": app_dir, "pid": proc.pid, "windows": windows}
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            result["result"] = json.load(f)
    if own_app and not keep_app_dir and result.get("result", {}).get("ok"):
        shutil.rmtree(app_dir, ignore_errors=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("script")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--app-dir")
    ap.add_argument("--module-path")
    ap.add_argument("--shots")
    ap.add_argument("--args", default="")
    ap.add_argument("--keep-app-dir", action="store_true")
    a = ap.parse_args()
    res = run(a.version, a.script, a.out, a.timeout, a.app_dir, a.module_path, a.shots, a.args, a.keep_app_dir)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    sys.exit(0 if res.get("result", {}).get("ok") else 1)


if __name__ == "__main__":
    main()
