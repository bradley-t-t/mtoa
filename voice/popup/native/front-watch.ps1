# Prints a line whenever another window comes to the front: its process id, a tab, the window's
# class. Sunday reads it to fold the deck down to the transcript strip once he turns to an app.
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class Front {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
}
"@
$last = [IntPtr]::Zero
while ($true) {
  $h = [Front]::GetForegroundWindow()
  if ($h -ne $last -and $h -ne [IntPtr]::Zero) {
    $last = $h
    $procId = 0
    [void][Front]::GetWindowThreadProcessId($h, [ref]$procId)
    $cls = New-Object System.Text.StringBuilder 256
    [void][Front]::GetClassName($h, $cls, 256)
    [Console]::Out.WriteLine("$procId`t$($cls.ToString())")
    [Console]::Out.Flush()
  }
  Start-Sleep -Milliseconds 300
}
