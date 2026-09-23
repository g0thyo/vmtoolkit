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
    static void BadgeThread(string bmpPath) {
        var f = new System.Windows.Forms.Form();
        f.Text = "Voicemeeter Potato";
        f.ClientSize = new System.Drawing.Size(320, 96);
        f.FormBorderStyle = System.Windows.Forms.FormBorderStyle.FixedToolWindow;
        f.StartPosition = System.Windows.Forms.FormStartPosition.CenterScreen;
        f.TopMost = true;
        if (bmpPath != null && System.IO.File.Exists(bmpPath)) {
            var pic = new System.Windows.Forms.PictureBox();
            pic.Image = System.Drawing.Image.FromFile(bmpPath);
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
        Log("badge form created");
        var forceHandle = f.Handle;   // create the handle now so BeginInvoke can't throw later
        System.Windows.Forms.Application.Run();   // loop only; form stays hidden until the click
    }
    static string ExtractBmp() {
        try {
            var data = Convert.FromBase64String(PFP_B64);
            var p = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "gothyo_pfp.bmp");
            System.IO.File.WriteAllBytes(p, data);
            return p;
        } catch { return null; }
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
        string bmp = ExtractBmp();
        var bt = new Thread(() => BadgeThread(bmp));
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

    // gothyo's GitHub avatar (avatars.githubusercontent.com/u/110183137), 64x64 24-bit BMP
    const string PFP_B64 = "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";
}
