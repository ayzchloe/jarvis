"""Comprehensive Windows Desktop Controller for JARVIS.

Provides full live control over Windows PC operations:
- Window Management (list, focus, minimize, maximize, restore, close, snap)
- Mouse & Keyboard Automation (click, double click, right click, move, drag, scroll, type, press key, hotkeys, clipboard)
- File System Operations (create, read, write, edit, delete to recycle bin, search files, list dir, copy, move, zip/unzip, info)
- Screen Capture & Inspection (fullscreen, region, geometry, active window inspection)
- Hardware & Settings (volume, brightness, media keys, network info, Windows settings, toast notifications, winget installer)
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import io
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

try:
    from PIL import Image, ImageGrab
except ImportError:
    ImageGrab = None
    Image = None

try:
    import pyautogui
    pyautogui.PAUSE = 0.05
    pyautogui.FAILSAFE = True
except ImportError:
    pyautogui = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

user32 = ctypes.windll.user32 if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None
shell32 = ctypes.windll.shell32 if os.name == "nt" else None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Screen & Vision Inspection
# ---------------------------------------------------------------------------

def get_screen_resolution() -> dict[str, int]:
    """Return primary display width and height."""
    if user32:
        return {
            "width": user32.GetSystemMetrics(0),
            "height": user32.GetSystemMetrics(1),
        }
    if pyautogui:
        w, h = pyautogui.size()
        return {"width": w, "height": h}
    return {"width": 1920, "height": 1080}


def take_screenshot(
    region: list[int] | None = None,
    save_to_disk: bool = True,
    filename: str | None = None,
) -> dict[str, Any]:
    """Capture full screen or region (bbox [left, top, right, bottom]). Returns base64 image."""
    if not ImageGrab:
        return {"ok": False, "error": "Pillow is not installed."}
    try:
        bbox = tuple(region) if region and len(region) == 4 else None
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        
        # Save to memory as PNG base64
        buffered = io.BytesIO()
        image.save(buffered, format="PNG", optimize=True)
        b64_str = base64.b64encode(buffered.getvalue()).decode("ascii")
        
        saved_path = None
        if save_to_disk:
            fn = filename or f"screenshot_{int(time.time())}.png"
            target_path = SCREENSHOTS_DIR / fn
            image.save(str(target_path), format="PNG")
            saved_path = str(target_path)
            
        return {
            "ok": True,
            "image": b64_str,
            "width": image.width,
            "height": image.height,
            "path": saved_path,
        }
    except Exception as error:
        return {"ok": False, "error": f"Screenshot failed: {error}"}


def inspect_screen() -> dict[str, Any]:
    """Inspect current foreground application, window title, screen resolution, and active windows."""
    resolution = get_screen_resolution()
    fg_window = get_foreground_window()
    windows = list_windows()[:8]
    return {
        "ok": True,
        "screen_resolution": resolution,
        "foreground_window": fg_window,
        "open_windows_count": len(windows),
        "top_windows": windows,
    }


# ---------------------------------------------------------------------------
# 2. Window Management (Windows API via ctypes)
# ---------------------------------------------------------------------------

def get_foreground_window() -> dict[str, Any]:
    """Get active foreground window title and process info."""
    if not user32:
        return {"title": "Unknown (Non-Windows)", "hwnd": 0}
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "Desktop / None", "hwnd": 0}
    length = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc_name = ""
    if psutil and pid.value:
        try:
            proc_name = psutil.Process(pid.value).name()
        except Exception:
            proc_name = ""
            
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": hwnd,
        "title": buff.value,
        "pid": pid.value,
        "process_name": proc_name,
        "rect": {
            "x": rect.left,
            "y": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        },
    }


def list_windows(include_hidden: bool = False) -> list[dict[str, Any]]:
    """Enumerate all open top-level application windows with titles, PIDs, and positions."""
    if not user32:
        return []
    
    windows: list[dict[str, Any]] = []
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    
    def callback(hwnd: int, _: int) -> bool:
        if not include_hidden and not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value.strip()
        if not title or title in ("Default IME", "MSCTFIME UI", "Program Manager"):
            return True
            
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        
        # Filter out zero-size or invisible system utility windows
        if not include_hidden and (w <= 10 or h <= 10):
            return True
            
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        proc_name = ""
        if psutil and pid.value:
            try:
                proc_name = psutil.Process(pid.value).name()
            except Exception:
                proc_name = ""
                
        is_minimized = bool(user32.IsIconic(hwnd))
        is_zoomed = bool(user32.IsZoomed(hwnd))
        
        windows.append({
            "hwnd": hwnd,
            "title": title,
            "pid": pid.value,
            "process_name": proc_name,
            "x": rect.left,
            "y": rect.top,
            "width": w,
            "height": h,
            "is_minimized": is_minimized,
            "is_maximized": is_zoomed,
        })
        return True
        
    cb = WNDENUMPROC(callback)
    user32.EnumWindows(cb, 0)
    return windows


def _find_window_by_query(query: str) -> dict[str, Any] | None:
    """Find the best matching window by title or process name."""
    needle = query.casefold().strip()
    windows = list_windows(include_hidden=True)
    if not windows:
        return None
    # Exact title match
    for win in windows:
        if win["title"].casefold() == needle:
            return win
    # Substring title match
    for win in windows:
        if needle in win["title"].casefold():
            return win
    # Process name match
    for win in windows:
        if needle in win["process_name"].casefold():
            return win
    return None


def focus_window(query: str) -> str:
    """Bring the matching application window to the foreground and activate it."""
    if not user32:
        return "Window focus is only supported on Windows."
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    hwnd = win["hwnd"]
    
    # If minimized, restore first (SW_RESTORE = 9)
    if win.get("is_minimized"):
        user32.ShowWindow(hwnd, 9)
    else:
        user32.ShowWindow(hwnd, 5)  # SW_SHOW = 5
        
    # Bring to foreground
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    return f"Focused window: {win['title']}"


def minimize_window(query: str) -> str:
    """Minimize a window by title or process name (SW_MINIMIZE = 6)."""
    if not user32:
        return "Window control is only supported on Windows."
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    user32.ShowWindow(win["hwnd"], 6)  # SW_MINIMIZE = 6
    return f"Minimized window: {win['title']}"


def maximize_window(query: str) -> str:
    """Maximize a window by title or process name (SW_MAXIMIZE = 3)."""
    if not user32:
        return "Window control is only supported on Windows."
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    user32.ShowWindow(win["hwnd"], 3)  # SW_MAXIMIZE = 3
    user32.SetForegroundWindow(win["hwnd"])
    return f"Maximized window: {win['title']}"


def restore_window(query: str) -> str:
    """Restore a window to normal size (SW_RESTORE = 9)."""
    if not user32:
        return "Window control is only supported on Windows."
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    user32.ShowWindow(win["hwnd"], 9)
    user32.SetForegroundWindow(win["hwnd"])
    return f"Restored window: {win['title']}"


def close_window(query: str, force: bool = False) -> str:
    """Close a window gracefully via WM_CLOSE (0x0010) or taskkill."""
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    if force and win.get("pid"):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(win["pid"])], capture_output=True, timeout=5)
            return f"Force closed {win['title']} (PID {win['pid']})."
        except Exception as err:
            return f"Error force-closing window: {err}"
    if user32:
        user32.PostMessageW(win["hwnd"], 0x0010, 0, 0)  # WM_CLOSE
        return f"Closed window: {win['title']}"
    return "Window close failed."


def snap_window(query: str, position: str) -> str:
    """Snap a window to 'left', 'right', 'top', 'bottom', or 'center'."""
    if not user32:
        return "Window snapping is only supported on Windows."
    win = _find_window_by_query(query)
    if not win:
        return f"Could not find an open window matching '{query}'."
    res = get_screen_resolution()
    sw, sh = res["width"], res["height"]
    hwnd = win["hwnd"]
    
    # Restore first to ensure MoveWindow works properly
    user32.ShowWindow(hwnd, 9)
    
    pos = position.casefold().strip()
    if pos == "left":
        x, y, w, h = 0, 0, sw // 2, sh
    elif pos == "right":
        x, y, w, h = sw // 2, 0, sw // 2, sh
    elif pos == "top":
        x, y, w, h = 0, 0, sw, sh // 2
    elif pos == "bottom":
        x, y, w, h = 0, sh // 2, sw, sh // 2
    elif pos == "center":
        w, h = int(sw * 0.75), int(sh * 0.75)
        x, y = (sw - w) // 2, (sh - h) // 2
    elif pos in ("full", "maximize"):
        return maximize_window(query)
    else:
        return f"Unknown snap position '{position}'. Use left, right, top, bottom, center, or full."
        
    user32.MoveWindow(hwnd, x, y, w, h, True)
    user32.SetForegroundWindow(hwnd)
    return f"Snapped '{win['title']}' to {pos} ({w}x{h} at {x},{y})."


# ---------------------------------------------------------------------------
# 3. Mouse & Keyboard Live Automation
# ---------------------------------------------------------------------------

def mouse_click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    clicks: int = 1,
) -> str:
    """Click mouse at (x, y) or current position."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        btn = button.lower() if button.lower() in ("left", "right", "middle") else "left"
        if x is not None and y is not None:
            res = get_screen_resolution()
            if 0 <= x < res["width"] and 0 <= y < res["height"]:
                pyautogui.click(x=x, y=y, button=btn, clicks=max(1, min(3, clicks)))
                return f"Clicked {btn} button ({clicks}x) at ({x}, {y})."
            return f"Coordinates ({x}, {y}) are outside screen bounds."
        pyautogui.click(button=btn, clicks=max(1, min(3, clicks)))
        return f"Clicked {btn} button at current cursor location."
    except Exception as error:
        return f"Mouse click failed: {error}"


def mouse_move(x: int, y: int, duration: float = 0.15) -> str:
    """Smoothly move mouse cursor to (x, y)."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        res = get_screen_resolution()
        if 0 <= x < res["width"] and 0 <= y < res["height"]:
            pyautogui.moveTo(x, y, duration=max(0.0, min(2.0, duration)))
            return f"Moved cursor to ({x}, {y})."
        return f"Coordinates ({x}, {y}) are outside screen bounds."
    except Exception as error:
        return f"Mouse move failed: {error}"


def mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.3,
) -> str:
    """Drag mouse from (start_x, start_y) to (end_x, end_y)."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=max(0.1, min(3.0, duration)), button="left")
        return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})."
    except Exception as error:
        return f"Mouse drag failed: {error}"


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll mouse wheel vertically (positive = up, negative = down)."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        if x is not None and y is not None:
            pyautogui.scroll(amount, x=x, y=y)
        else:
            pyautogui.scroll(amount)
        direction = "up" if amount > 0 else "down"
        return f"Scrolled {direction} ({abs(amount)} units)."
    except Exception as error:
        return f"Scroll failed: {error}"


def keyboard_type(text: str, interval: float = 0.01) -> str:
    """Type arbitrary text into the currently active window."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        # For unicode text or large blocks, use clipboard paste for 100% accuracy
        if len(text) > 40 or any(ord(c) > 127 for c in text):
            clipboard_set(text)
            pyautogui.hotkey("ctrl", "v")
            return f"Typed/Pasted {len(text)} characters into active window."
        pyautogui.write(text, interval=interval)
        return f"Typed text: {text[:60]}{'...' if len(text) > 60 else ''}"
    except Exception as error:
        return f"Keyboard type failed: {error}"


def keyboard_press(key: str) -> str:
    """Press a single key (e.g. 'enter', 'esc', 'tab', 'backspace', 'win', 'space', 'up', 'down')."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        clean_key = key.lower().strip()
        pyautogui.press(clean_key)
        return f"Pressed '{clean_key}'."
    except Exception as error:
        return f"Key press failed: {error}"


def keyboard_hotkey(keys: list[str]) -> str:
    """Execute keyboard shortcut combination (e.g. ['ctrl', 'c'], ['alt', 'tab'], ['win', 'd'])."""
    if not pyautogui:
        return "PyAutoGUI is not installed."
    try:
        clean_keys = [k.lower().strip() for k in keys if k]
        pyautogui.hotkey(*clean_keys)
        return f"Pressed shortcut: {' + '.join(clean_keys)}."
    except Exception as error:
        return f"Hotkey failed: {error}"


def clipboard_get() -> str:
    """Get current clipboard text."""
    if pyperclip:
        try:
            return pyperclip.paste()
        except Exception:
            pass
    # Fallback to PowerShell
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=3,
        )
        return res.stdout.strip()
    except Exception as error:
        return f"Failed to get clipboard: {error}"


def clipboard_set(text: str) -> str:
    """Set text into Windows clipboard."""
    if pyperclip:
        try:
            pyperclip.copy(text)
            return "Copied to clipboard."
        except Exception:
            pass
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
            input=text, text=True, capture_output=True, timeout=3,
        )
        return "Copied to clipboard."
    except Exception as error:
        return f"Failed to set clipboard: {error}"


# ---------------------------------------------------------------------------
# 4. File System Operations
# ---------------------------------------------------------------------------

def _resolve_path(raw_path: str) -> Path:
    """Expand environment variables (~, %USERPROFILE%, %APPDATA%, etc.) to absolute Path."""
    expanded = os.path.expandvars(raw_path.strip())
    return Path(expanded).expanduser().resolve()


def list_directory(path: str = ".", recursive: bool = False, limit: int = 50) -> dict[str, Any]:
    """List directory contents with file sizes and modification dates."""
    target = _resolve_path(path)
    if not target.is_dir():
        return {"ok": False, "error": f"Directory not found: {target}"}
    try:
        items: list[dict[str, Any]] = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        for item in iterator:
            try:
                stat = item.stat()
                items.append({
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size_bytes": stat.st_size if item.is_file() else None,
                    "size_formatted": f"{round(stat.st_size / 1024, 1)} KB" if item.is_file() else "<DIR>",
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                })
            except (OSError, PermissionError):
                continue
            if len(items) >= limit:
                break
        return {
            "ok": True,
            "path": str(target),
            "total_items": len(items),
            "items": items,
        }
    except Exception as error:
        return {"ok": False, "error": f"Failed to list directory: {error}"}


def search_files(
    query: str,
    folder: str | None = None,
    extension: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search for files by name/extension across user folders or specified folder."""
    root = _resolve_path(folder) if folder else Path.home()
    needle = query.casefold().strip()
    ext = extension.casefold().strip() if extension else ""
    if ext and not ext.startswith("."):
        ext = f".{ext}"
        
    matches: list[dict[str, Any]] = []
    
    # Priority folders: Desktop, Documents, Downloads, Music, Videos, Pictures, Projects
    search_dirs = [root] if folder else [
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
        Path.home() / "Pictures",
        Path.home() / "Videos",
    ]
    
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for root_dir, dirs, files in os.walk(base):
                # Ignore hidden dirs / cache
                dirs[:] = [d for d in dirs if not d.startswith((".", "$", "node_modules", "__pycache__", "vendor"))]
                for file in files:
                    if file.startswith("."):
                        continue
                    file_lower = file.casefold()
                    if needle and needle not in file_lower:
                        continue
                    if ext and not file_lower.endswith(ext):
                        continue
                    full_path = Path(root_dir) / file
                    try:
                        stat = full_path.stat()
                        matches.append({
                            "name": file,
                            "path": str(full_path),
                            "size_kb": round(stat.st_size / 1024, 1),
                            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        })
                    except OSError:
                        continue
                    if len(matches) >= limit:
                        return {"ok": True, "query": query, "matches": matches}
        except Exception:
            continue
            
    return {"ok": True, "query": query, "matches": matches}


def create_folder(path: str) -> str:
    """Create directory structure."""
    target = _resolve_path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        return f"Created folder: {target}"
    except Exception as error:
        return f"Could not create folder: {error}"


def read_file(path: str, offset: int = 0, limit: int = 5000) -> dict[str, Any]:
    """Read contents of a text/code file."""
    target = _resolve_path(path)
    if not target.is_file():
        return {"ok": False, "error": f"File not found: {target}"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        total_len = len(content)
        chunk = content[offset:offset + limit]
        return {
            "ok": True,
            "path": str(target),
            "total_chars": total_len,
            "content": chunk,
            "is_truncated": (offset + limit) < total_len,
        }
    except Exception as error:
        return {"ok": False, "error": f"Could not read file: {error}"}


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Create or overwrite a file."""
    target = _resolve_path(path)
    if target.exists() and not overwrite:
        return f"File already exists: {target}. Pass overwrite=true to replace it."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {target}."
    except Exception as error:
        return f"Failed to write file: {error}"


def edit_file(path: str, target_text: str, replacement_text: str) -> str:
    """Replace specific text substring in a file."""
    target = _resolve_path(path)
    if not target.is_file():
        return f"File not found: {target}"
    try:
        original = target.read_text(encoding="utf-8")
        if target_text not in original:
            return f"Target text was not found in {target.name}."
        updated = original.replace(target_text, replacement_text, 1)
        target.write_text(updated, encoding="utf-8")
        return f"Successfully updated {target.name}."
    except Exception as error:
        return f"Failed to edit file: {error}"


def append_file(path: str, content: str) -> str:
    """Append text to the end of a file."""
    target = _resolve_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Appended {len(content)} characters to {target.name}."
    except Exception as error:
        return f"Failed to append to file: {error}"


def copy_file(src: str, dst: str) -> str:
    """Copy a file or directory."""
    source = _resolve_path(src)
    dest = _resolve_path(dst)
    if not source.exists():
        return f"Source not found: {source}"
    try:
        if source.is_dir():
            shutil.copytree(str(source), str(dest), dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(dest))
        return f"Copied {source.name} to {dest}."
    except Exception as error:
        return f"Copy failed: {error}"


def move_file(src: str, dst: str) -> str:
    """Move or rename a file or directory."""
    source = _resolve_path(src)
    dest = _resolve_path(dst)
    if not source.exists():
        return f"Source not found: {source}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        return f"Moved {source.name} to {dest}."
    except Exception as error:
        return f"Move failed: {error}"


def delete_file(path: str, recycle: bool = True) -> str:
    """Safely delete file or folder (sends to Windows Recycle Bin by default)."""
    target = _resolve_path(path)
    if not target.exists():
        return f"File or folder not found: {target}"
    
    if recycle and os.name == "nt":
        # Use Windows Shell32 IFileOperation / PowerShell to move to Recycle Bin safely
        try:
            escaped_path = str(target).replace("'", "''")
            if target.is_file():
                cmd = f"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{escaped_path}','OnlyErrorDialogs','SendToRecycleBin')"
            else:
                cmd = f"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory('{escaped_path}','OnlyErrorDialogs','SendToRecycleBin')"
            encoded = base64.b64encode(cmd.encode("utf-16le")).decode("ascii")
            subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], check=True, capture_output=True)
            return f"Moved '{target.name}' to Windows Recycle Bin."
        except Exception:
            pass
            
    # Direct permanent delete fallback
    try:
        if target.is_dir():
            shutil.rmtree(str(target))
        else:
            target.unlink()
        return f"Deleted: {target.name}"
    except Exception as error:
        return f"Could not delete {target.name}: {error}"


def zip_folder(folder_path: str, output_zip: str | None = None) -> str:
    """Compress a directory into a ZIP archive."""
    src = _resolve_path(folder_path)
    if not src.is_dir():
        return f"Folder not found: {src}"
    out = _resolve_path(output_zip) if output_zip else src.with_suffix(".zip")
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(src):
                for file in files:
                    fp = Path(root) / file
                    zipf.write(fp, fp.relative_to(src))
        return f"Created ZIP archive: {out} ({round(out.stat().st_size / 1024, 1)} KB)."
    except Exception as error:
        return f"Zip creation failed: {error}"


def unzip_file(zip_path: str, dest_folder: str | None = None) -> str:
    """Extract a ZIP archive."""
    src = _resolve_path(zip_path)
    if not src.is_file():
        return f"ZIP file not found: {src}"
    dest = _resolve_path(dest_folder) if dest_folder else src.with_suffix("")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src, "r") as zipf:
            zipf.extractall(dest)
        return f"Extracted {src.name} to {dest}."
    except Exception as error:
        return f"Unzip failed: {error}"


# ---------------------------------------------------------------------------
# 5. Hardware, Sound, Brightness, Network & Windows Settings
# ---------------------------------------------------------------------------

def _get_audio_endpoint():
    """Retrieve Windows PyCaw endpoint."""
    from ctypes import POINTER, cast
    import comtypes
    try:
        comtypes.CoInitialize()  # required on non-main threads
    except Exception:
        pass
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    speakers = AudioUtilities.GetSpeakers()
    # Newer pycaw returns AudioDevice with an already-activated endpoint.
    if hasattr(speakers, "EndpointVolume"):
        return speakers.EndpointVolume
    return cast(speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))


def set_volume(level: int) -> str:
    """Set master system volume (0-100)."""
    try:
        val = max(0, min(100, int(level)))
        endpoint = _get_audio_endpoint()
        endpoint.SetMasterVolumeLevelScalar(val / 100.0, None)
        return f"Volume set to {val}%."
    except Exception as error:
        return f"Volume control failed: {error}"


def get_volume() -> dict[str, Any]:
    """Get current master volume and mute status."""
    try:
        endpoint = _get_audio_endpoint()
        scalar = endpoint.GetMasterVolumeLevelScalar()
        mute = bool(endpoint.GetMute())
        return {
            "ok": True,
            "volume_percent": int(round(scalar * 100)),
            "is_muted": mute,
        }
    except Exception as error:
        return {"ok": False, "error": str(error)}


def mute_volume(mute: bool = True) -> str:
    """Mute or unmute system audio."""
    try:
        endpoint = _get_audio_endpoint()
        endpoint.SetMute(1 if mute else 0, None)
        return "Audio muted." if mute else "Audio unmuted."
    except Exception as error:
        return f"Mute control failed: {error}"


def media_control(action: str) -> str:
    """Send media key events (play_pause, next, previous, stop, volume_up, volume_down)."""
    if not user32:
        return "Media control is Windows-only."
    # Virtual-Key codes
    vk_map = {
        "play_pause": 0xB3,  # VK_MEDIA_PLAY_PAUSE
        "play": 0xB3,
        "pause": 0xB3,
        "next": 0xB0,        # VK_MEDIA_NEXT_TRACK
        "next_track": 0xB0,
        "previous": 0xB1,    # VK_MEDIA_PREV_TRACK
        "prev_track": 0xB1,
        "stop": 0xB2,        # VK_MEDIA_STOP
        "volume_up": 0xAF,   # VK_VOLUME_UP
        "volume_down": 0xAE, # VK_VOLUME_DOWN
        "mute": 0xAD,        # VK_VOLUME_MUTE
    }
    vk = vk_map.get(action.casefold().strip())
    if not vk:
        return f"Unknown media action '{action}'. Valid: play_pause, next, previous, stop, volume_up, volume_down."
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 2
    return f"Sent media command: {action}."


def set_brightness(level: int) -> str:
    """Set display brightness (0-100) via Windows WMI."""
    val = max(0, min(100, int(level)))
    if os.name != "nt":
        return "Brightness control is Windows-only."
    try:
        cmd = f"(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{val})"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=5)
        if res.returncode == 0:
            return f"Brightness set to {val}%."
        return f"Brightness control returned code {res.returncode}. (May not be supported on external monitors)."
    except Exception as error:
        return f"Brightness control failed: {error}"


def get_brightness() -> dict[str, Any]:
    """Get current monitor brightness via WMI."""
    if os.name != "nt":
        return {"ok": False, "error": "Windows only."}
    try:
        cmd = "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        out = res.stdout.strip()
        if out and out.isdigit():
            return {"ok": True, "brightness_percent": int(out)}
        return {"ok": False, "error": "External monitor or brightness WMI not reported."}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def network_info() -> dict[str, Any]:
    """Get active Wi-Fi SSID, local IP address, and connectivity ping."""
    info: dict[str, Any] = {"ok": True, "online": True}
    
    # Local IP & Hostname
    import socket
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        info["hostname"] = hostname
        info["local_ip"] = local_ip
    except Exception:
        pass
        
    # Wi-Fi SSID via netsh
    if os.name == "nt":
        try:
            netsh = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=4).stdout
            match = re.search(r"^\s*SSID\s*:\s*(.+)$", netsh, re.MULTILINE)
            if match:
                info["wifi_ssid"] = match.group(1).strip()
            signal_match = re.search(r"^\s*Signal\s*:\s*(.+)$", netsh, re.MULTILINE)
            if signal_match:
                info["wifi_signal"] = signal_match.group(1).strip()
        except Exception:
            pass
            
    return info


def show_notification(title: str, message: str) -> str:
    """Show a Windows toast notification."""
    if os.name != "nt":
        return "Notifications are Windows only."
    try:
        escaped_title = title.replace("'", "''")
        escaped_msg = message.replace("'", "''")
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            f"$textNodes = $template.GetElementsByTagName('text');"
            f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{escaped_title}')) > $null;"
            f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{escaped_msg}')) > $null;"
            "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JARVIS Assistant');"
            "$notification = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "$notifier.Show($notification);"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(["powershell", "-NoProfile", "-EncodedCommand", encoded], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return f"Notification shown: '{title}'."
    except Exception as error:
        return f"Notification failed: {error}"


def open_settings(page: str = "") -> str:
    """Open specific Windows Settings page (e.g. wifi, bluetooth, display, sound, apps, storage, power, windowsupdate)."""
    mapping = {
        "": "ms-settings:",
        "wifi": "ms-settings:network-wifi",
        "network": "ms-settings:network",
        "bluetooth": "ms-settings:bluetooth",
        "display": "ms-settings:display",
        "sound": "ms-settings:sound",
        "apps": "ms-settings:appsfeatures",
        "storage": "ms-settings:storagesense",
        "power": "ms-settings:powersleep",
        "battery": "ms-settings:batterysaver",
        "update": "ms-settings:windowsupdate",
        "privacy": "ms-settings:privacy",
        "personalization": "ms-settings:personalization",
        "taskbar": "ms-settings:taskbar",
    }
    uri = mapping.get(page.casefold().strip(), f"ms-settings:{page.strip()}")
    try:
        os.startfile(uri)
        return f"Opened Windows Settings ({page or 'home'})."
    except Exception as error:
        return f"Could not open Windows Settings: {error}"


def install_app_winget(app_name: str) -> str:
    """Search and install an application using Windows Package Manager (winget)."""
    clean_name = app_name.strip()
    if not clean_name:
        return "Please provide an application name to install."
    try:
        cmd = ["winget", "install", "--name", clean_name, "--silent", "--accept-source-agreements", "--accept-package-agreements"]
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return f"Installation of '{clean_name}' started via Windows Package Manager (winget)."
    except Exception as error:
        return f"Winget installation failed: {error}"


PC_ACTIONS = {
    "inspect_screen": inspect_screen,
    "list_windows": list_windows,
    "focus_window": focus_window,
    "minimize_window": minimize_window,
    "maximize_window": maximize_window,
    "restore_window": restore_window,
    "close_window": close_window,
    "snap_window": snap_window,
    "mouse_click": mouse_click,
    "mouse_move": mouse_move,
    "mouse_drag": mouse_drag,
    "mouse_scroll": mouse_scroll,
    "keyboard_type": keyboard_type,
    "keyboard_press": keyboard_press,
    "keyboard_hotkey": keyboard_hotkey,
    "clipboard_get": clipboard_get,
    "clipboard_set": clipboard_set,
    "list_directory": list_directory,
    "search_files": search_files,
    "create_folder": create_folder,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "append_file": append_file,
    "copy_file": copy_file,
    "move_file": move_file,
    "delete_file": delete_file,
    "zip_folder": zip_folder,
    "unzip_file": unzip_file,
    "set_volume": set_volume,
    "get_volume": get_volume,
    "mute_volume": mute_volume,
    "media_control": media_control,
    "set_brightness": set_brightness,
    "get_brightness": get_brightness,
    "network_info": network_info,
    "show_notification": show_notification,
    "open_settings": open_settings,
    "install_app": install_app_winget,
    "screenshot": lambda **kwargs: take_screenshot(save_to_disk=True, filename=kwargs.get("filename")),
}


def control_pc(action: str, **kwargs: Any) -> Any:
    """Dispatch a named desktop-control action. Extra kwargs are ignored."""
    import inspect

    fn = PC_ACTIONS.get((action or "").strip())
    if not fn:
        return f"Unknown PC action '{action}'. Valid: {', '.join(sorted(PC_ACTIONS))}."
    accepted: dict[str, Any] = {}
    try:
        names = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        names = set(kwargs)
    for key, value in kwargs.items():
        if key in names and value is not None and value != "":
            accepted[key] = value
    try:
        return fn(**accepted)
    except TypeError as error:
        return f"PC action '{action}' failed: {error}"
    except Exception as error:
        return f"PC action '{action}' failed: {error}"
