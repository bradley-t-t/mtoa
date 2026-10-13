# Prints one line of JSON whenever the answer changes: is a game running on this PC,
# and which one. The popup keeps its overlay off while one is, unless he turns it on.
#
#   {"gaming":true,"game":"KSP_x64.exe","pid":17456,"why":"game library"}
#   {"gaming":false}
#
# A game is any of these, for as long as its process is alive:
#   - an app that went fullscreen in front: Direct3D exclusive, or a window with no title
#     bar covering its whole screen. The desktop, the shell and browsers don't count, and
#     Steam's Big Picture and hosted Store apps count only while they are in front.
#   - a program inside a game library: Steam, Epic, Riot, EA, GOG, Ubisoft, Rockstar, Xbox.
#   - a program started by a launcher (Steam, Epic, Battle.net, Riot, EA, GOG, Ubisoft,
#     Rockstar, Xbox) that is not part of the launcher itself or of Windows.
#   - a known game executable, or any Unreal Engine game ("...-Win64-Shipping.exe").
# A launcher on its own is not a game: Steam sits in the background all day.

$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class GameWatch {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct PROCESSENTRY32 {
    public uint dwSize; public uint cntUsage; public uint th32ProcessID; public IntPtr th32DefaultHeapID;
    public uint th32ModuleID; public uint cntThreads; public uint th32ParentProcessID; public int pcPriClassBase;
    public uint dwFlags; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile;
  }
  [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] struct MONITORINFO { public int cbSize; public RECT rcMonitor; public RECT rcWork; public int dwFlags; }

  [DllImport("kernel32.dll")] static extern IntPtr CreateToolhelp32Snapshot(uint flags, uint pid);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32FirstW")] static extern bool First(IntPtr h, ref PROCESSENTRY32 e);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, EntryPoint = "Process32NextW")] static extern bool Next(IntPtr h, ref PROCESSENTRY32 e);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool QueryFullProcessImageNameW(IntPtr h, uint flags, StringBuilder s, ref uint n);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
  [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr m, ref MONITORINFO mi);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetWindowLong(IntPtr h, int index);
  [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
  [DllImport("shell32.dll")] static extern int SHQueryUserNotificationState(out int state);

  public class Proc { public uint Pid, Parent; public string Name, Path; }

  static readonly HashSet<string> Browsers = Set("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera_gx.exe", "vivaldi.exe", "arc.exe", "iexplore.exe", "chromium.exe", "thorium.exe", "librewolf.exe",
    "waterfox.exe", "zen.exe", "msedgewebview2.exe");
  static readonly HashSet<string> Shell = Set("explorer.exe", "sunday.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe", "lockapp.exe", "logonui.exe",
    "textinputhost.exe", "screenclippinghost.exe", "snippingtool.exe", "dwm.exe");
  // Shared, long-lived hosts: a game only while their fullscreen window is in front.
  static readonly HashSet<string> FrontOnly = Set("applicationframehost.exe", "steamwebhelper.exe");
  static readonly HashSet<string> Launchers = Set("steam.exe", "epicgameslauncher.exe", "battle.net.exe",
    "riotclientservices.exe", "eadesktop.exe", "origin.exe", "galaxyclient.exe", "upc.exe", "ubisoftconnect.exe",
    "rockstarlauncher.exe", "xboxpcapp.exe");
  // Pieces of the launchers, and crash reporters, which are never the game itself.
  static readonly HashSet<string> Helpers = Set("steamwebhelper.exe", "steamservice.exe", "steamerrorreporter.exe",
    "steamerrorreporter64.exe", "gameoverlayui.exe", "gameoverlayui64.exe", "epicwebhelper.exe",
    "unrealcefsubprocess.exe", "crashreportclient.exe", "crashpad_handler.exe", "unitycrashhandler32.exe",
    "unitycrashhandler64.exe", "agent.exe", "battle.net helper.exe", "blizzarderror.exe", "riotclientux.exe",
    "riotclientuxrender.exe", "riotclientcrashhandler.exe", "eabackgroundservice.exe", "eaconnect_microsoft.exe",
    "qtwebengineprocess.exe", "galaxyclient helper.exe", "galaxycommunication.exe", "uplaywebcore.exe",
    "upcwebhelper.exe", "socialclubhelper.exe", "conhost.exe", "cmd.exe", "werfault.exe");
  static readonly string[] Libraries = {
    @"\steamapps\common\", @"\epic games\", @"\riot games\", @"\ea games\", @"\origin games\", @"\gog galaxy\games\",
    @"\gog games\", @"\ubisoft game launcher\games\", @"\rockstar games\", @"\xboxgames\" };
  // Inside a library folder but not a game: the launchers themselves and shared installers.
  static readonly string[] NotGames = {
    @"\epic games\launcher\", @"\epic games\epic online services\", @"\riot games\riot client\",
    @"\rockstar games\launcher\", @"\rockstar games\social club\", @"\steamworks shared\", @"\_commonredist\",
    @"\steamapps\common\wallpaper_engine\", @"\xboxgames\gamesave\" };
  static readonly HashSet<string> KnownGames = Set("league of legends.exe", "leagueclient.exe", "valorant.exe",
    "cs2.exe", "csgo.exe", "dota2.exe", "overwatch.exe", "r5apex.exe", "r5apex_dx12.exe", "gta5.exe",
    "gta5_enhanced.exe", "rdr2.exe", "eldenring.exe", "minecraft.windows.exe", "robloxplayerbeta.exe",
    "rocketleague.exe", "destiny2.exe", "cod.exe", "modernwarfare.exe", "wow.exe", "hearthstone.exe",
    "diablo iv.exe", "cyberpunk2077.exe", "witcher3.exe", "bg3.exe", "bg3_dx11.exe", "starfield.exe",
    "eurotrucks2.exe", "amtrucks.exe", "ksp_x64.exe", "ksp2_x64.exe", "factorio.exe", "terraria.exe",
    "stardew valley.exe");

  static HashSet<string> Set(params string[] names) { return new HashSet<string>(names, StringComparer.OrdinalIgnoreCase); }

  static readonly Dictionary<uint, Proc> known = new Dictionary<uint, Proc>();
  static readonly Dictionary<uint, string> remembered = new Dictionary<uint, string>();   // went fullscreen: pid -> name
  public static uint Self = 0;

  public static void Init() { try { SetProcessDPIAware(); } catch { } }

  public static Dictionary<uint, Proc> Snapshot() {
    var now = new Dictionary<uint, Proc>();
    IntPtr h = CreateToolhelp32Snapshot(2, 0);
    if (h == IntPtr.Zero || h == new IntPtr(-1)) return now;
    try {
      var e = new PROCESSENTRY32(); e.dwSize = (uint)Marshal.SizeOf(typeof(PROCESSENTRY32));
      for (bool ok = First(h, ref e); ok; ok = Next(h, ref e)) {
        Proc p;
        if (!known.TryGetValue(e.th32ProcessID, out p) || !string.Equals(p.Name, e.szExeFile, StringComparison.OrdinalIgnoreCase))
          p = new Proc { Pid = e.th32ProcessID, Name = e.szExeFile, Path = PathOf(e.th32ProcessID) };
        p.Parent = e.th32ParentProcessID;
        now[p.Pid] = p;
      }
    } finally { CloseHandle(h); }
    known.Clear();
    foreach (var kv in now) known[kv.Key] = kv.Value;
    return now;
  }

  static string PathOf(uint pid) {
    IntPtr h = OpenProcess(0x1000, false, pid);   // PROCESS_QUERY_LIMITED_INFORMATION
    if (h == IntPtr.Zero) return null;
    try {
      var s = new StringBuilder(1024); uint n = 1024;
      return QueryFullProcessImageNameW(h, 0, s, ref n) ? s.ToString() : null;
    } finally { CloseHandle(h); }
  }

  // The process whose window is in front and fullscreen, and whether only while in front.
  static Proc Fullscreen(Dictionary<uint, Proc> procs) {
    IntPtr w = GetForegroundWindow();
    if (w == IntPtr.Zero || IsIconic(w)) return null;
    var cls = new StringBuilder(64); GetClassName(w, cls, 64);
    string c = cls.ToString();
    if (c == "Progman" || c == "WorkerW" || c == "Shell_TrayWnd" || c == "Shell_SecondaryTrayWnd") return null;
    uint pid; GetWindowThreadProcessId(w, out pid);
    Proc p;
    if (!procs.TryGetValue(pid, out p) || Browsers.Contains(p.Name) || Shell.Contains(p.Name)) return null;
    int state;
    if (SHQueryUserNotificationState(out state) == 0 && state == 3) return p;   // Direct3D exclusive
    RECT r; var mi = new MONITORINFO(); mi.cbSize = Marshal.SizeOf(typeof(MONITORINFO));
    if (!GetWindowRect(w, out r) || !GetMonitorInfo(MonitorFromWindow(w, 2), ref mi)) return null;
    var m = mi.rcMonitor;
    bool covers = r.L <= m.L && r.T <= m.T && r.R >= m.R && r.B >= m.B;
    bool titled = (GetWindowLong(w, -16) & 0x00C00000) == 0x00C00000;   // WS_CAPTION: a maximized window
    return covers && !titled ? p : null;
  }

  static bool Under(string path, string dir) {
    return path != null && dir != null && path.StartsWith(dir, StringComparison.OrdinalIgnoreCase);
  }

  static string Dir(string path) {
    if (path == null) return null;
    int i = path.LastIndexOf('\\');
    return i > 0 ? path.Substring(0, i + 1) : null;
  }

  // Why this process is a game, or null.
  static string Why(Proc p, Dictionary<uint, Proc> procs) {
    if (p.Pid == Self || Helpers.Contains(p.Name) || Launchers.Contains(p.Name) || Shell.Contains(p.Name) || Browsers.Contains(p.Name)) return null;
    string lower = p.Path == null ? null : p.Path.ToLowerInvariant();
    if (KnownGames.Contains(p.Name)) return "known game";
    string n = p.Name.ToLowerInvariant();
    if (n.EndsWith("-win64-shipping.exe") || n.EndsWith("-wingdk-shipping.exe")) return "unreal engine game";
    if (lower != null) {
      bool skip = false;
      foreach (var x in NotGames) if (lower.Contains(x)) { skip = true; break; }
      if (!skip) foreach (var lib in Libraries) if (lower.Contains(lib)) return "game library";
    }
    Proc parent;
    if (procs.TryGetValue(p.Parent, out parent) && Launchers.Contains(parent.Name) && lower != null) {
      string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows).ToLowerInvariant() + "\\";
      if (!lower.StartsWith(windows) && !Under(lower, (Dir(parent.Path) ?? "").ToLowerInvariant())) return "started by " + parent.Name;
    }
    return null;
  }

  static uint current = 0;

  // One line of JSON describing the game now running, or none.
  public static string Check() {
    var procs = Snapshot();
    foreach (var pid in new List<uint>(remembered.Keys)) {
      Proc p;
      if (!procs.TryGetValue(pid, out p) || !string.Equals(p.Name, remembered[pid], StringComparison.OrdinalIgnoreCase)) remembered.Remove(pid);
    }
    Proc front = Fullscreen(procs);
    if (front != null && !FrontOnly.Contains(front.Name)) remembered[front.Pid] = front.Name;
    // The game already reported stays the game while it runs, so two at once don't flicker.
    var found = new List<KeyValuePair<Proc, string>>();
    if (front != null) found.Add(new KeyValuePair<Proc, string>(front, "fullscreen"));
    foreach (var p in procs.Values) {
      string why = remembered.ContainsKey(p.Pid) ? "fullscreen" : Why(p, procs);
      if (why != null) found.Add(new KeyValuePair<Proc, string>(p, why));
    }
    if (found.Count == 0) { current = 0; return "{\"gaming\":false}"; }
    var pick = found[0];
    foreach (var f in found) if (f.Key.Pid == current) { pick = f; break; }
    current = pick.Key.Pid;
    return "{\"gaming\":true,\"game\":\"" + Esc(pick.Key.Name) + "\",\"pid\":" + pick.Key.Pid + ",\"why\":\"" + Esc(pick.Value) + "\"}";
  }

  public static bool Alive(uint pid) { return known.ContainsKey(pid); }
  public static uint ParentOf(uint pid) { Proc p; return known.TryGetValue(pid, out p) ? p.Parent : 0; }

  static string Esc(string s) { return s.Replace("\\", "\\\\").Replace("\"", "\\\""); }
}
'@

[GameWatch]::Init()
[GameWatch]::Self = [uint32]$PID
$null = [GameWatch]::Check()
$app = [GameWatch]::ParentOf([uint32]$PID)
$last = $null
while ($true) {
  try { $now = [GameWatch]::Check() } catch { $now = $last }
  if ($now -ne $last -and $now) {
    [Console]::Out.WriteLine($now)
    [Console]::Out.Flush()
    $last = $now
  }
  # The popup that started this is gone: so is the reason to watch.
  if ($app -and -not [GameWatch]::Alive($app)) { exit 0 }
  Start-Sleep -Milliseconds 2000
}
