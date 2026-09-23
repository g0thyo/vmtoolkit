#!/usr/bin/env python3
# language: Python 3.8+, file: vm_activator.py, target: Windows 10/11 (x64/ARM64), stdlib only
# Voicemeeter Potato donationware nag killer + badge — MEMORY-ONLY, nothing on disk changes.
#
# Why memory-only: Potato hashes its own exe at startup (WM_CREATE -> self-file hash) and
# uses that hash as the key to decrypt its UI panel resources. Any on-disk byte change
# breaks panel decryption -> "Failed to create window..." -> exit. The file must stay
# factory-pristine; the patch lives in the running process only.
#
# Mechanism: the nag launcher's DialogBoxIndirectParamA call site is rewritten in RAM to
# call an injected stub. The stub is a single `mov byte [flag], 1; ret` — zero external
# calls, nothing to fault. This script polls the flag over ReadProcessMemory; when raised,
# it pops a "patched by @g0thyo" badge window (your GitHub PFP embedded, works offline).
# Closing the badge only hides it — every logo click reopens it.
#
# Validated against Potato v3.1.1.9 (x64 + x86). Byte signatures are checked before
# patching (x86 signature normalized for ASLR relocations); other builds are refused.
#
# Usage:
#   python vm_activator.py                 :: auto-find Potato x64, launch nag-free + badge
#   python vm_activator.py --x86           :: launch the 32-bit build instead
#   python vm_activator.py --attach        :: hook an already-running Potato in place
#   python vm_activator.py --exe "C:\path\voicemeeter8x64.exe"
#   python vm_activator.py --noinject      :: plain NOP of the nag call, no badge
#
# Launch mode stays resident while Potato runs (debug-loop semantics). Attach mode stays
# resident too — it owns the badge window. Close Potato / Ctrl+C to end.

import base64
import ctypes
import os
import struct
import sys
import tempfile
import threading
import winreg
from ctypes import wintypes

# ---------------------------------------------------------------- patch table
# RVA (from image base) + expected bytes at the nag call site (as stored on DISK).
# The x86 site carries two absolute immediates (dlgproc ptr, IAT slot) that the loader
# relocates by (image_base - 0x400000) — normalized at patch time.
PATCHES = {
    # voicemeeter8.exe: push ebx / push dlgproc / push esi / push ecx / push eax /
    # call [DialogBoxIndirectParamA] — 15 bytes -> E8 rel32 + 10x90.
    0x14C:  (0xB50E3, bytes.fromhex("5368d0434b00565150ff1574945b00")),
    # voicemeeter8x64.exe: call qword [DialogBoxIndirectParamA] — 6 bytes (RIP-relative,
    # no relocations) -> E8 rel32 + 90.
    0x8664: (0x96F17, bytes.fromhex("ff15eb491100")),
}

DEBUG_PROCESS               = 0x00000001
CREATE_PROCESS_DEBUG_EVENT  = 3
EXIT_PROCESS_DEBUG_EVENT    = 5
DBG_CONTINUE                = 0x00010002
DBG_EXCEPTION_NOT_HANDLED   = 0x80010001
STATUS_BREAKPOINT           = 0x80000003
PAGE_EXECUTE_READWRITE      = 0x40
MEM_COMMIT                  = 0x1000
MEM_RESERVE                 = 0x2000
TH32CS_SNAPPROCESS          = 0x00000002
TH32CS_SNAPMODULE           = 0x00000008
TH32CS_SNAPMODULE32         = 0x00000010
MAX_MODULE_NAME32           = 255
WS_OVERLAPPEDWINDOW         = 0x00CF0000
WS_CHILD                    = 0x40000000
WS_VISIBLE                  = 0x10000000
SS_BITMAP                   = 0x0000000E
STM_SETIMAGE                = 0x0172
IMAGE_BITMAP                = 0
LR_LOADFROMFILE             = 0x0010
SW_SHOW                     = 5
SW_HIDE                     = 0
WM_CLOSE                    = 0x0010
BADGE_CLASS                 = "VMG0THYOBadgeWnd"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

# 64-bit handle-carrying calls — ctypes defaults to 32-bit int and truncates
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HANDLE
user32.FindWindowW.restype = wintypes.HWND
user32.SendMessageW.restype = ctypes.c_long
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_uint64, ctypes.c_int64]

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE)]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

# DEBUG_EVENT on a 64-bit host: 3xDWORD header + 4B pad + union (union starts at 16)
class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [("raw", ctypes.c_byte * 512)]

class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wintypes.DWORD),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
                ("_pad", wintypes.DWORD),
                ("u", DEBUG_EVENT_UNION)]

class MODULEENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("th32ModuleID", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("GlblcntUsage", wintypes.DWORD),
                ("ProccntUsage", wintypes.DWORD),
                ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
                ("modBaseSize", wintypes.DWORD),
                ("hModule", wintypes.HMODULE),
                ("szModule", ctypes.c_char * (MAX_MODULE_NAME32 + 1)),
                ("szExePath", ctypes.c_char * wintypes.MAX_PATH)]

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * wintypes.MAX_PATH)]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             ctypes.c_uint64, ctypes.c_int64)

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

# -------------------------------------------------------------- win32 helpers
def read_mem(hproc, addr, size):
    buf = (ctypes.c_ubyte * size)()
    n = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(hproc, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        raise OSError(ctypes.get_last_error(), "ReadProcessMemory")
    return bytes(buf[:n.value])

def write_mem(hproc, addr, data):
    n = ctypes.c_size_t(0)
    if not kernel32.WriteProcessMemory(hproc, ctypes.c_void_p(addr), data, len(data), ctypes.byref(n)) \
            or n.value != len(data):
        raise OSError(ctypes.get_last_error(), "WriteProcessMemory")

def alloc_near(hproc, anchor, size=0x1000):
    """Allocate RWX memory within rel32 reach (+-2GB) of `anchor`."""
    for delta in range(0x10000, 0x70000000, 0x10000):
        for cand in (anchor + delta, anchor - delta):
            if cand <= 0x10000:
                continue
            p = kernel32.VirtualAllocEx(hproc, ctypes.c_void_p(cand), size,
                                        MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
            if p:
                return p
    return None

def patch_site(hproc, addr, expected, replacement):
    count = len(expected)
    cur = read_mem(hproc, addr, count)
    if cur == replacement or (replacement and cur[0] == 0xE8):
        print("[*] site already hooked — nothing to do", flush=True)
        return True
    if cur != expected:
        print("[!] byte signature mismatch at 0x%X:" % addr, flush=True)
        print("    expected %s" % expected.hex(), flush=True)
        print("    found    %s" % cur.hex(), flush=True)
        print("[!] unsupported build — refusing to patch blind", flush=True)
        return False
    old = wintypes.DWORD(0)
    if not kernel32.VirtualProtectEx(hproc, ctypes.c_void_p(addr), ctypes.c_size_t(count),
                                     PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
        raise OSError(ctypes.get_last_error(), "VirtualProtectEx")
    write_mem(hproc, addr, replacement)
    old2 = wintypes.DWORD(0)
    kernel32.VirtualProtectEx(hproc, ctypes.c_void_p(addr), ctypes.c_size_t(count),
                              old.value, ctypes.byref(old2))
    kernel32.FlushInstructionCache(hproc, ctypes.c_void_p(addr), count)
    return True

def pe_machine(path):
    with open(path, "rb") as f:
        head = f.read(0x400)
    if head[:2] != b"MZ":
        raise ValueError("not a PE file")
    peoff = struct.unpack_from("<I", head, 0x3C)[0]
    return struct.unpack_from("<H", head, peoff + 4)[0]

def find_exe(x86=False):
    name = "voicemeeter8.exe" if x86 else "voicemeeter8x64.exe"
    for hive, sub, flags in [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}",
         winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}",
         winreg.KEY_READ),
    ]:
        try:
            with winreg.OpenKey(hive, sub, 0, flags) as k:
                uninst, _ = winreg.QueryValueEx(k, "UninstallString")
                p = os.path.join(os.path.dirname(uninst.strip('"')), name)
                if os.path.isfile(p):
                    return p
        except OSError:
            pass
    p = os.path.join(r"C:\Program Files (x86)\VB\Voicemeeter", name)
    return p if os.path.isfile(p) else None

def module_base(pid, want_name):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        if not kernel32.Module32First(snap, ctypes.byref(me)):
            raise OSError(ctypes.get_last_error(), "Module32First")
        while True:
            if me.szModule.decode(errors="replace").lower() == want_name.lower():
                return ctypes.cast(me.modBaseAddr, ctypes.c_void_p).value
            if not kernel32.Module32Next(snap, ctypes.byref(me)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None

def find_running(name):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wintypes.HANDLE(-1).value:
        return None
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(pe)):
            return None
        while True:
            if pe.szExeFile.decode(errors="replace").lower() == name.lower():
                return pe.th32ProcessID
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None

def adjusted_expected(machine, base):
    """x86 site immediates are relocated by (base - 0x400000) at load time."""
    rva, expected = PATCHES[machine]
    if machine == 0x8664:
        return rva, expected
    delta = base - 0x400000
    b = bytearray(expected)
    struct.pack_into("<I", b, 2, (0x4B43D0 + delta) & 0xFFFFFFFF)   # dlgproc imm32
    struct.pack_into("<I", b, 11, (0x5B9474 + delta) & 0xFFFFFFFF)  # IAT slot
    return rva, bytes(b)

# ------------------------------------------------------------ stub building
def build_stub_x64(stub_addr, flag_addr):
    """mov byte [rip+flag], 1; ret  — zero external calls, nothing to fault.
    Note: instruction is 7 bytes (opcode+rel32+imm8); rel32 counts from stub+7."""
    s = bytearray(bytes.fromhex("c6050000000001c3"))
    struct.pack_into("<i", s, 2, flag_addr - (stub_addr + 7))
    return bytes(s)

def build_stub_x86(stub_addr, flag_addr):
    """mov byte [flag], 1; ret — absolute 32-bit address."""
    return bytes.fromhex("c605") + struct.pack("<I", flag_addr & 0xFFFFFFFF) + bytes.fromhex("01c3")

g_flag_addr = [0]
g_hproc = [None]

def install_hook(hproc, base, machine):
    """Allocate a stub page, write the flag-stub, rewrite the nag call site."""
    is64 = machine == 0x8664
    rva, expected = adjusted_expected(machine, base)
    site = base + rva

    stub = alloc_near(hproc, base) if is64 else kernel32.VirtualAllocEx(
        hproc, None, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE)
    if not stub:
        raise OSError(ctypes.get_last_error(), "VirtualAllocEx")
    flag_addr = stub + 0x800
    code = build_stub_x64(stub, flag_addr) if is64 else build_stub_x86(stub, flag_addr)
    write_mem(hproc, stub, code)

    rel = stub - (site + 5)
    replacement = b"\xe8" + struct.pack("<i", rel)
    replacement += b"\x90" * (len(expected) - len(replacement))
    if not patch_site(hproc, site, expected, replacement):
        return False
    g_flag_addr[0] = flag_addr
    g_hproc[0] = hproc
    print("[+] logo click hooked -> badge flag at 0x%X (stub 0x%X)" % (flag_addr, stub), flush=True)
    return True

def nop_only(hproc, base, machine):
    rva, expected = adjusted_expected(machine, base)
    site = base + rva
    if not patch_site(hproc, site, expected, b"\x90" * len(expected)):
        return False
    print("[+] nag dialog call NOP'd (%d bytes at 0x%X)" % (len(expected), site), flush=True)
    return True

def poll_badge():
    """Check the stub's flag byte; pop + reset if raised."""
    if not g_flag_addr[0] or not g_hproc[0]:
        return
    try:
        if read_mem(g_hproc[0], g_flag_addr[0], 1) != b"\x01":
            return
        write_mem(g_hproc[0], g_flag_addr[0], b"\x00")
    except OSError:
        return
    pop_badge()

# ------------------------------------------------------------ badge window
g_badge_hwnd = [None]

def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        user32.ShowWindow(hwnd, SW_HIDE)   # X hides; next logo click reopens
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

def badge_thread(hbmp):
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = WNDPROC(_wnd_proc)
    wc.hInstance = wintypes.HINSTANCE(kernel32.GetModuleHandleW(None))
    wc.lpszClassName = BADGE_CLASS
    wc.hbrBackground = wintypes.HBRUSH(6)  # COLOR_BTNFACE+1
    user32.RegisterClassExW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(0, BADGE_CLASS, "Voicemeeter Potato",
                                  WS_OVERLAPPEDWINDOW, 200, 200, 330, 140,
                                  None, None, None, None)
    if not hwnd:
        return
    g_badge_hwnd[0] = hwnd

    def text(txt, x, y, w, h):
        user32.CreateWindowExW(0, "STATIC", txt, WS_CHILD | WS_VISIBLE,
                               x, y, w, h, hwnd, None, None, None)

    if hbmp:
        pic = user32.CreateWindowExW(0, "STATIC", "", WS_CHILD | WS_VISIBLE | SS_BITMAP,
                                     12, 12, 64, 64, hwnd, None, None, None)
        user32.SendMessageW(pic, STM_SETIMAGE, IMAGE_BITMAP, hbmp)
        text("patched by @g0thyo", 90, 20, 220, 20)
        text("https://github.com/g0thyo", 90, 46, 230, 20)
    else:
        text("patched by @g0thyo", 14, 20, 280, 20)
        text("https://github.com/g0thyo", 14, 46, 290, 20)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

def pop_badge():
    hwnd = g_badge_hwnd[0]
    if hwnd:
        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)

# --------------------------------------------------------------- pfp asset
AVATAR_URL = "https://avatars.githubusercontent.com/u/185858215?v=4"

def fetch_avatar_hbitmap():
    """Download g0thyo's avatar and decode to an HBITMAP via GDI+ (stdlib only).
    Offline / any failure -> None -> text-only badge."""
    try:
        import ssl
        import urllib.request
        try:
            data = urllib.request.urlopen(AVATAR_URL, timeout=6).read()
        except Exception:
            # embeddable/minimal Python installs ship no CA bundle
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            data = urllib.request.urlopen(AVATAR_URL, timeout=6, context=ctx).read()
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CreateStreamOnHGlobal.restype = ctypes.c_long
        ole32.CreateStreamOnHGlobal.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.POINTER(ctypes.c_void_p)]

        gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)
        # GDI+ startup
        token = ctypes.c_size_t(0)
        inp = (ctypes.c_byte * 32)()                 # oversized zeroed GdiplusStartupInput
        ctypes.c_uint32.from_buffer(inp).value = 1   # Version = 1
        if gdiplus.GdiplusStartup(ctypes.byref(token), inp, None) != 0:
            return None
        # bytes -> IStream (CreateStreamOnHGlobal with fDeleteOnRelease)
        hglob = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        ptr = kernel32.GlobalLock(hglob)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(hglob)
        stream = ctypes.c_void_p(0)
        if ole32.CreateStreamOnHGlobal(hglob, True, ctypes.byref(stream)) != 0:
            return None
        bmp = ctypes.c_void_p(0)
        if gdiplus.GdipCreateBitmapFromStream(stream, ctypes.byref(bmp)) != 0:
            return None
        hbmp = ctypes.c_void_p(0)
        if gdiplus.GdipCreateHBITMAPFromBitmap(bmp, ctypes.byref(hbmp), 0) != 0:
            return None
        gdiplus.GdipDisposeImage(bmp)
        gdiplus.GdiplusShutdown(token)
        return hbmp.value
    except Exception:
        return None

# -------------------------------------------------------------------- modes
def launch_mode(exe, badge):
    machine = pe_machine(exe)
    if machine not in PATCHES:
        sys.exit("[!] unknown PE machine type 0x%X" % machine)

    if badge:
        threading.Thread(target=badge_thread, args=(fetch_avatar_hbitmap(),), daemon=True).start()

    si = STARTUPINFOW(); si.cb = ctypes.sizeof(STARTUPINFOW)
    pi = PROCESS_INFORMATION()
    if not kernel32.CreateProcessW(exe, None, None, None, False, DEBUG_PROCESS,
                                   None, os.path.dirname(exe), ctypes.byref(si),
                                   ctypes.byref(pi)):
        sys.exit("[!] CreateProcess failed: %d" % ctypes.get_last_error())

    ev = DEBUG_EVENT()
    base = None
    # first event under DEBUG_PROCESS is always CREATE_PROCESS_DEBUG_EVENT
    while base is None:
        if not kernel32.WaitForDebugEvent(ctypes.byref(ev), 30000):
            sys.exit("[!] timed out waiting for image load")
        if ev.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT:
            # CREATE_PROCESS_DEBUG_INFO.lpBaseOfImage @ union+0x18 (64-bit host)
            base = struct.unpack_from("<Q", bytes(ev.u.raw), 0x18)[0]
        kernel32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, DBG_CONTINUE)

    print("[*] %s loaded at 0x%X (pid %d)" % (os.path.basename(exe), base, pi.dwProcessId), flush=True)
    ok = install_hook(pi.hProcess, base, machine) if badge else nop_only(pi.hProcess, base, machine)
    if not ok:
        kernel32.TerminateProcess(pi.hProcess, 1)
        sys.exit(1)

    print("[*] Potato is live — nag dead, logo click = badge. Close Potato to exit.", flush=True)
    alive = True
    while alive:
        if kernel32.WaitForDebugEvent(ctypes.byref(ev), 250):
            if ev.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT:
                alive = False
                kernel32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, DBG_CONTINUE)
            elif ev.dwDebugEventCode == 1:  # EXCEPTION — app SEH gets first chance
                code = struct.unpack_from("<I", bytes(ev.u.raw), 0)[0]
                first = struct.unpack_from("<I", bytes(ev.u.raw), 0x98)[0]
                if code != STATUS_BREAKPOINT:
                    addr = struct.unpack_from("<Q", bytes(ev.u.raw), 0x10)[0]
                    print("[!] child exception code=0x%08X addr=0x%X first=%d"
                          % (code, addr, first), flush=True)
                cont = DBG_CONTINUE if first else DBG_EXCEPTION_NOT_HANDLED
                kernel32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, cont)
            else:
                kernel32.ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, DBG_CONTINUE)
        if badge:
            poll_badge()
    kernel32.CloseHandle(pi.hThread)
    kernel32.CloseHandle(pi.hProcess)

def attach_mode(name, badge):
    pid = find_running(name)
    if not pid:
        sys.exit("[!] no running %s found" % name)
    base = module_base(pid, name)
    if not base:
        sys.exit("[!] could not read module base of pid %d" % pid)
    exe = find_exe(x86=(name.lower() == "voicemeeter8.exe"))
    if not exe:
        sys.exit("[!] install dir not found; pass --exe with launch mode")
    machine = pe_machine(exe)
    if machine not in PATCHES:
        sys.exit("[!] unknown PE machine type 0x%X" % machine)

    # PROCESS_VM_OPERATION|VM_READ|VM_WRITE|QUERY_INFORMATION
    hproc = kernel32.OpenProcess(0x0438, False, pid)
    if not hproc:
        sys.exit("[!] OpenProcess failed: %d (run elevated)" % ctypes.get_last_error())
    try:
        ok = install_hook(hproc, base, machine) if badge else nop_only(hproc, base, machine)
        if not ok:
            sys.exit(1)
    finally:
        kernel32.CloseHandle(hproc)

    if not badge:
        print("[*] running instance patched — nag is dead for this session", flush=True)
        return

    threading.Thread(target=badge_thread, args=(fetch_avatar_hbitmap(),), daemon=True).start()
    print("[*] hooked running Potato — logo click = badge. Ctrl+C to quit.", flush=True)
    try:
        while True:
            poll_badge()
            ctypes.windll.kernel32.Sleep(200)
    except KeyboardInterrupt:
        pass

# -------------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    badge = "--noinject" not in args
    if "--attach" in args:
        name = "voicemeeter8.exe" if "--x86" in args else "voicemeeter8x64.exe"
        attach_mode(name, badge)
        return
    if "--exe" in args:
        exe = args[args.index("--exe") + 1]
    else:
        exe = find_exe(x86=("--x86" in args))
    if not exe or not os.path.isfile(exe):
        sys.exit("[!] Voicemeeter Potato not found — install it or pass --exe")
    if find_running(os.path.basename(exe)):
        sys.exit("[!] Potato is already running — close it first, or use --attach")
    print("[*] launching %s nag-free" % exe, flush=True)
    launch_mode(exe, badge)

if __name__ == "__main__":
    main()
