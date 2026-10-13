# Prints "1" when this PC's default output is playing sound and "0" when it stops,
# once per change. Reads the endpoint's peak meter (IAudioMeterInformation). The popup
# app reads it so the speaker can tell sound from the computer apart from the owner
# talking. The popup itself makes no sound.

$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCom {}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int NotImpl1();
  [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
}

[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioMeterInformation {
  [PreserveSig] int GetPeakValue(out float peak);
}

public static class OutputMeter {
  public static float Peak() {
    var e = (IMMDeviceEnumerator)new MMDeviceEnumeratorCom();
    IMMDevice dev;
    if (e.GetDefaultAudioEndpoint(0, 1, out dev) != 0) return 0;   // render, multimedia
    var iid = typeof(IAudioMeterInformation).GUID;
    object o;
    if (dev.Activate(ref iid, 23, IntPtr.Zero, out o) != 0) return 0;
    float p;
    ((IAudioMeterInformation)o).GetPeakValue(out p);
    return p;
  }
}
"@

$threshold = 0.01
$holdMs = 1500          # speech has gaps; stay "playing" across them
$last = $null
$lastSound = [DateTime]::MinValue
while ($true) {
  try { $peak = [OutputMeter]::Peak() } catch { $peak = 0 }
  if ($peak -gt $threshold) { $lastSound = Get-Date }
  $now = ((Get-Date) - $lastSound).TotalMilliseconds -lt $holdMs
  if ($now -ne $last) {
    if ($now) { [Console]::Out.WriteLine("1") } else { [Console]::Out.WriteLine("0") }
    [Console]::Out.Flush()
    $last = $now
  }
  Start-Sleep -Milliseconds 250
}
