// language: C#, file: vmactivator.cs, target: Windows .NET 4.x x64, compile:
//   csc /platform:x64 /target:winexe /r:System.Windows.Forms.dll /r:System.Drawing.dll vmactivator.cs
//
// Voicemeeter Potato donationware nag killer + badge — MEMORY-ONLY, the on-disk exe
// stays factory-pristine (Potato self-hashes its file at startup to derive the panel
// decryption key; any byte change on disk kills the UI at window creation).
//
// The nag launcher's DialogBoxIndirectParamA call site is rewritten in RAM to call an
// injected stub: OpenEventA("Local\VM_GOTHYO_BADGE") + SetEvent + ret. The About/Registration
// dialog never renders; the logo click pops a "patched by @g0thyo" badge instead.
//
// Patch sites (Potato v3.1.1.9; byte signature checked before writing — other builds refused):
//   x64 voicemeeter8x64.exe RVA 0x96F17  6 bytes  ff15eb491100 -> E8 rel32 90
//   x86 voicemeeter8.exe    RVA 0xB50E3 15 bytes  5368d0434b00565150ff1574945b00 -> E8 rel32 + 10x90
//
// usage: vmactivator.exe [path-to-voicemeeter8x64.exe] [--x86]
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

class VMActivator {
    const uint DEBUG_PROCESS = 0x1;
    const uint CREATE_PROCESS_DEBUG_EVENT = 3, EXCEPTION_DEBUG_EVENT = 1,
               EXIT_PROCESS_DEBUG_EVENT = 5, CREATE_THREAD_DEBUG_EVENT = 2,
               LOAD_DLL_DEBUG_EVENT = 6;
    const uint DBG_CONTINUE = 0x00010002, DBG_EXCEPTION_NOT_HANDLED = 0x80010001;
    const uint STATUS_BREAKPOINT = 0x80000003;
    const int CONTEXT_FLAGS = 0x100000 | 0x1 | 0x2; // AMD64 | CONTROL | INTEGER

    const string EVENT_NAME = "Local\\VM_GOTHYO_BADGE";

    [StructLayout(LayoutKind.Sequential)]
    struct DEBUG_EVENT {
        public uint dwDebugEventCode, dwProcessId, dwThreadId, pad;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 512)]
        public byte[] u;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct STARTUPINFO { public int cb; public string r1,r2,r3; public int x,y,cx,cy,xs,ys,fc,fs; public short r4,r5; public IntPtr h1,h2,h3; }
    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public uint pid, tid; }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcess(string app, string cmd, IntPtr pa, IntPtr ta, bool inherit,
        uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll")] static extern bool WaitForDebugEvent(out DEBUG_EVENT ev, uint ms);
    [DllImport("kernel32.dll")] static extern bool ContinueDebugEvent(uint pid, uint tid, uint cont);
    [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr h, long addr, byte[] buf, int n, out int read);
    [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr h, long addr, byte[] buf, int n, out int wr);
    [DllImport("kernel32.dll")] static extern bool FlushInstructionCache(IntPtr h, long addr, int n);
    [DllImport("kernel32.dll")] static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr addr, uint size, uint type, uint prot);
    [DllImport("kernel32.dll")] static extern bool VirtualProtectEx(IntPtr h, IntPtr addr, uint size, uint fl, out uint old);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr CreateEventW(IntPtr attr, bool manual, bool init, string name);
    [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll")] static extern bool ResetEvent(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string m);

    static void Log(string m) {
        try { System.IO.File.AppendAllText(@"C:\vmwork\activator.log",
            DateTime.Now.ToString("HH:mm:ss.fff") + " " + m + "\r\n"); } catch { }
    }

    // ---------------------------------------------------------- badge window
    static System.Windows.Forms.Form g_form = null;
    static long g_flagAddr = 0;
    static IntPtr g_hProc = IntPtr.Zero;

    const string AVATAR_URL = "https://avatars.githubusercontent.com/u/185858215?v=4";

    static System.Drawing.Image FetchAvatar() {
        try {
            var wc = new System.Net.WebClient();
            var data = wc.DownloadData(AVATAR_URL);
            return System.Drawing.Image.FromStream(new System.IO.MemoryStream(data));
        } catch { return null; }   // offline -> text-only badge
    }

    static void BadgeThread() {
        var img = FetchAvatar();
        var f = new System.Windows.Forms.Form();
        f.Text = "Voicemeeter Potato";
        f.ClientSize = new System.Drawing.Size(320, 96);
        f.FormBorderStyle = System.Windows.Forms.FormBorderStyle.FixedToolWindow;
        f.StartPosition = System.Windows.Forms.FormStartPosition.CenterScreen;
        f.TopMost = true;
        if (img != null) {
            var pic = new System.Windows.Forms.PictureBox();
            pic.Image = img;
            pic.SetBounds(10, 12, 64, 64);
            pic.SizeMode = System.Windows.Forms.PictureBoxSizeMode.StretchImage;
            f.Controls.Add(pic);
        }
        var l1 = new System.Windows.Forms.Label();
        l1.Text = "patched by @g0thyo";
        l1.Font = new System.Drawing.Font("Segoe UI", 11f, System.Drawing.FontStyle.Bold);
        l1.AutoSize = true; l1.Location = new System.Drawing.Point(86, 18);
        f.Controls.Add(l1);
        var l2 = new System.Windows.Forms.LinkLabel();
        l2.Text = "https://github.com/g0thyo";
        l2.Font = new System.Drawing.Font("Segoe UI", 9f);
        l2.AutoSize = true; l2.Location = new System.Drawing.Point(86, 48);
        l2.LinkClicked += (s, e) => System.Diagnostics.Process.Start("https://github.com/g0thyo");
        f.Controls.Add(l2);
        f.FormClosing += (s, e) => { e.Cancel = true; f.Hide(); };  // X hides; next click reopens
        g_form = f;
        var forceHandle = f.Handle;   // create the handle now so BeginInvoke can't throw later
        System.Windows.Forms.Application.Run();   // loop only; form stays hidden until the click
    }
    // ---------------------------------------------------------- remote helpers
    static byte[] ReadMem(IntPtr h, long addr, int n) {
        byte[] b = new byte[n]; int rn;
        ReadProcessMemory(h, addr, b, n, out rn);
        return b;
    }
    static long RemoteExport(IntPtr h, long modbase, string name) {
        try {
            var dos = ReadMem(h, modbase, 0x40);
            if (dos[0] != 0x4D || dos[1] != 0x5A) return 0;
            int peoff = BitConverter.ToInt32(dos, 0x3C);
            var nt = ReadMem(h, modbase + peoff, 0x108);
            int magic = BitConverter.ToUInt16(nt, 0x18);
            int expOff = magic == 0x20B ? 0x88 : 0x78;
            int expRva = BitConverter.ToInt32(nt, expOff);
            if (expRva == 0) return 0;
            var exp = ReadMem(h, modbase + expRva, 40);
            int nNames = BitConverter.ToInt32(exp, 0x18);
            int funcsRva = BitConverter.ToInt32(exp, 0x1C);
            int namesRva = BitConverter.ToInt32(exp, 0x20);
            int ordsRva = BitConverter.ToInt32(exp, 0x24);
            var namesRaw = ReadMem(h, modbase + namesRva, 4 * nNames);
            byte[] target = Encoding.ASCII.GetBytes(name);
            for (int i = 0; i < nNames; i++) {
                int nrva = BitConverter.ToInt32(namesRaw, 4 * i);
                var nm = ReadMem(h, modbase + nrva, target.Length + 1);
                bool eq = true;
                for (int j = 0; j < target.Length; j++) if (nm[j] != target[j]) { eq = false; break; }
                if (eq && nm[target.Length] == 0) {
                    int ord = BitConverter.ToUInt16(ReadMem(h, modbase + ordsRva + 2 * i, 2), 0);
                    int frva = BitConverter.ToInt32(ReadMem(h, modbase + funcsRva + 4 * ord, 4), 0);
                    return modbase + frva;
                }
            }
        } catch { }
        return 0;
    }
    static long AllocNear(IntPtr h, long anchor) {
        for (long delta = 0x10000; delta < 0x70000000; delta += 0x10000) {
            long[] cands = { anchor + delta, anchor - delta };
            foreach (var c in cands) {
                if (c <= 0x10000) continue;
                var p = VirtualAllocEx(h, (IntPtr)c, 0x1000, 0x3000, 0x40);
                if (p != IntPtr.Zero) return (long)p;
            }
        }
        return 0;
    }

    static byte[] BuildStubX64(long stub, long flagAddr) {
        // mov byte [rip+flag], 1; ret
        var s = new List<byte>();
        int relPos = s.Count + 2;
        s.AddRange(new byte[]{0xC6,0x05,0,0,0,0,0x01, 0xC3});
        byte[] sa = s.ToArray();
        Buffer.BlockCopy(BitConverter.GetBytes((int)(flagAddr - (stub + relPos + 5))), 0, sa, relPos, 4); // +5: rel32 field end + imm8 byte
        return sa;
    }
    static byte[] BuildStubX86(long stub, long flagAddr) {
        // mov byte [flag], 1; ret  (absolute addr — 32-bit space)
        var s = new List<byte>();
        s.AddRange(new byte[]{0xC6,0x05});
        s.AddRange(BitConverter.GetBytes((uint)(flagAddr & 0xFFFFFFFF)));
        s.AddRange(new byte[]{0x01, 0xC3});
        return s.ToArray();
    }
    static bool InstallHook(IntPtr hProc, long baseAddr, bool is64, long k32base) {
        long rva = is64 ? 0x96F17 : 0xB50E3;
        byte[] expected = is64
            ? new byte[]{0xff,0x15,0xeb,0x49,0x11,0x00}
            : new byte[]{0x53,0x68,0xd0,0x43,0x4b,0x00,0x56,0x51,0x50,0xff,0x15,0x74,0x94,0x5b,0x00};
        long site = baseAddr + rva;
        if (!is64) {
            // x86 site carries two absolute immediates that the loader relocates:
            // dlgproc 0x004B43D0 at [2] and the IAT slot 0x005B9474 at [11].
            long delta = baseAddr - 0x400000;
            Buffer.BlockCopy(BitConverter.GetBytes((uint)(0x4B43D0 + delta)), 0, expected, 2, 4);
            Buffer.BlockCopy(BitConverter.GetBytes((uint)(0x5B9474 + delta)), 0, expected, 11, 4);
        }
        var cur = ReadMem(hProc, site, expected.Length);
        for (int i = 0; i < expected.Length; i++) {
            if (cur[i] == 0xE8) return true; // already hooked
            if (cur[i] != expected[i]) {
                Log("SIGFAIL site=0x" + site.ToString("X") + " got=" + BitConverter.ToString(cur));
                return false;
            }
        }
        long stub = is64 ? AllocNear(hProc, baseAddr)
                         : (long)VirtualAllocEx(hProc, IntPtr.Zero, 0x1000, 0x3000, 0x40);
        if (stub == 0) { Log("ALLOCFAIL"); return false; }
        g_flagAddr = stub + 0x800;
        int wn;
        byte[] stubBytes = is64 ? BuildStubX64(stub, g_flagAddr)
                                : BuildStubX86(stub, g_flagAddr);
        WriteProcessMemory(hProc, stub, stubBytes, stubBytes.Length, out wn);
        int rel = (int)(stub - (site + 5));
        byte[] rep = new byte[expected.Length];
        rep[0] = 0xE8;
        Buffer.BlockCopy(BitConverter.GetBytes(rel), 0, rep, 1, 4);
        for (int i = 5; i < rep.Length; i++) rep[i] = 0x90;
        uint oldProt, tmp;
        VirtualProtectEx(hProc, (IntPtr)site, (uint)rep.Length, 0x40, out oldProt);
        WriteProcessMemory(hProc, site, rep, rep.Length, out wn);
        VirtualProtectEx(hProc, (IntPtr)site, (uint)rep.Length, oldProt, out tmp);
        FlushInstructionCache(hProc, site, rep.Length);
        return wn == rep.Length;
    }

    // ---------------------------------------------------------- main
    static void Main(string[] args) {
        bool x86 = false;
        string exe = null;
        foreach (var a in args) {
            if (a == "--x86") x86 = true;
            else exe = a;
        }
        if (exe == null) {
            exe = FindExe(x86);
            if (exe == null) return;
        }
        // PE machine sanity
        bool is64;
        using (var fs = System.IO.File.OpenRead(exe)) {
            var head = new byte[0x400];
            fs.Read(head, 0, head.Length);
            int peoff = BitConverter.ToInt32(head, 0x3C);
            int machine = BitConverter.ToUInt16(head, peoff + 4);
            is64 = machine == 0x8664;
            if (machine != 0x8664 && machine != 0x14C) return;
        }

        Log("start exe=" + exe + " is64=" + is64);
        // no kernel event — the stub raises a memory flag we poll
        var bt = new Thread(BadgeThread);
        bt.SetApartmentState(ApartmentState.STA);
        bt.IsBackground = true;
        bt.Start();

        var si = new STARTUPINFO(); si.cb = Marshal.SizeOf(si);
        PROCESS_INFORMATION pi;
        if (!CreateProcess(exe, null, IntPtr.Zero, IntPtr.Zero, false, DEBUG_PROCESS, IntPtr.Zero,
                           System.IO.Path.GetDirectoryName(exe), ref si, out pi)) return;

        long baseAddr = 0;
        IntPtr hProc = pi.hProcess;
        g_hProc = pi.hProcess;
        bool hooked = false, done = false;
        while (!done) {
            DEBUG_EVENT ev;
            if (!WaitForDebugEvent(out ev, 250)) {
                ServiceBadge();
                continue;
            }
            uint cont = DBG_CONTINUE;
            switch (ev.dwDebugEventCode) {
                case CREATE_PROCESS_DEBUG_EVENT:
                    baseAddr = Marshal.ReadInt64(ev.u, 0x18);
                    Log("base=0x" + baseAddr.ToString("X"));
                    break;
                case LOAD_DLL_DEBUG_EVENT:
                    if (!hooked && baseAddr != 0) {
                        long dllBase = Marshal.ReadInt64(ev.u, 0x08);
                        if (RemoteExport(hProc, dllBase, "OpenEventA") != 0) {
                            hooked = InstallHook(hProc, baseAddr, is64, dllBase);
                            Log("hook install at dll 0x" + dllBase.ToString("X") + " => " + hooked);
                        }
                    }
                    break;
                case EXCEPTION_DEBUG_EVENT: {
                    uint code = (uint)Marshal.ReadInt32(ev.u, 0);
                    uint first = (uint)Marshal.ReadInt32(ev.u, 0x98);
                    // app SEH gets first chance; only second chance passes through unhandled
                    if (code != STATUS_BREAKPOINT && first == 0) cont = DBG_EXCEPTION_NOT_HANDLED;
                    break;
                }
                case EXIT_PROCESS_DEBUG_EVENT:
                    done = true;
                    break;
            }
            ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, cont);
            ServiceBadge();
        }
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    }

    static void ServiceBadge() {
        if (g_flagAddr == 0 || g_hProc == IntPtr.Zero) return;
        int rn;
        byte[] b = new byte[1];
        if (!ReadProcessMemory(g_hProc, g_flagAddr, b, 1, out rn) || rn != 1) return;
        if (b[0] == 0) return;
        byte[] zero = new byte[1];
        WriteProcessMemory(g_hProc, g_flagAddr, zero, 1, out rn);
        Log("FLAG raised — popping badge, form=" + (g_form != null));
        if (g_form != null) {
            try {
                g_form.BeginInvoke((Action)(() => {
                    try {
                        if (!g_form.Visible) g_form.Show();
                        g_form.WindowState = System.Windows.Forms.FormWindowState.Normal;
                        g_form.BringToFront();
                        g_form.Activate();
                        Log("badge Show() done, visible=" + g_form.Visible);
                    } catch (Exception ex2) { Log("show fail: " + ex2.Message); }
                }));
            } catch (Exception ex) { Log("BeginInvoke fail: " + ex.Message); }
        }
    }
    static string FindExe(bool x86) {
        string name = x86 ? "voicemeeter8.exe" : "voicemeeter8x64.exe";
        try {
            using (var k = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
                @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}")) {
                if (k != null) {
                    var u = k.GetValue("UninstallString") as string;
                    if (u != null) {
                        var p = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(u.Trim('"')), name);
                        if (System.IO.File.Exists(p)) return p;
                    }
                }
            }
        } catch { }
        var def = System.IO.Path.Combine(@"C:\Program Files (x86)\VB\Voicemeeter", name);
        return System.IO.File.Exists(def) ? def : null;
    }
}
