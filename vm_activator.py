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

def badge_thread(bmp_path):
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

    hbmp = None
    if bmp_path and os.path.isfile(bmp_path):
        hbmp = user32.LoadImageW(None, bmp_path, IMAGE_BITMAP, 0, 0, LR_LOADFROMFILE)
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
def extract_bmp():
    try:
        data = base64.b64decode(_PFP_B64)
        p = os.path.join(tempfile.gettempdir(), "g0thyo_pfp.bmp")
        with open(p, "wb") as f:
            f.write(data)
        return p
    except Exception:
        return None

# -------------------------------------------------------------------- modes
def launch_mode(exe, badge):
    machine = pe_machine(exe)
    if machine not in PATCHES:
        sys.exit("[!] unknown PE machine type 0x%X" % machine)

    if badge:
        threading.Thread(target=badge_thread, args=(extract_bmp(),), daemon=True).start()

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

    threading.Thread(target=badge_thread, args=(extract_bmp(),), daemon=True).start()
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

# g0thyo's GitHub avatar (avatars.githubusercontent.com/u/185858215), 64x64 24-bit BMP
_PFP_B64 = """
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
"""
