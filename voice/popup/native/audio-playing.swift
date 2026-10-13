// Prints "1" when this Mac is audibly playing sound and "0" when it stops, once per
// change. The popup app reads it so the speaker can tell sound from the computer apart
// from the owner talking. The popup itself makes no sound.
//
// It listens to what is actually coming out: a CoreAudio process tap (a stereo mixdown
// of every process except the popup and everything it started, which includes the
// Mac ear playing Sunday's own replies) feeds a private
// aggregate device, and its IOProc measures the level. Whether the output device is
// merely open says nothing, since browsers, Teams and Electron apps hold it open in
// silence. Needs macOS 14.2 and the "System Audio Recording" permission, which macOS
// asks the popup for once; until it is allowed the tap delivers silence and this
// reports "0".

import AudioToolbox
import CoreAudio
import Foundation

let threshold: Float = 0.00316        // -50 dBFS, as RMS over one IO buffer
let hold: TimeInterval = 1.5          // speech has gaps; stay "playing" across them

// Loudest buffer since the main loop last looked, written from the IO thread.
let levelLock = NSLock()
var loudest: Float = 0
var lastCallback = Date()

func prop<T: BitwiseCopyable>(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector, _ initial: T,
              qualifier: UnsafeRawPointer? = nil, qualifierSize: UInt32 = 0) -> T? {
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    var addr = AudioObjectPropertyAddress(mSelector: selector,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    let err = AudioObjectGetPropertyData(obj, &addr, qualifierSize, qualifier, &size, &value)
    return err == noErr ? value : nil
}

func processObject(_ pid: pid_t) -> AudioObjectID? {
    var p = pid
    let id = prop(AudioObjectID(kAudioObjectSystemObject),
                  kAudioHardwarePropertyTranslatePIDToProcessObject, AudioObjectID(kAudioObjectUnknown),
                  qualifier: &p, qualifierSize: UInt32(MemoryLayout<pid_t>.size))
    return (id == nil || id == AudioObjectID(kAudioObjectUnknown)) ? nil : id
}

func defaultOutputUID() -> String? {
    guard let dev = prop(AudioObjectID(kAudioObjectSystemObject),
                         kAudioHardwarePropertyDefaultOutputDevice, AudioDeviceID(0)),
          dev != 0 else { return nil }
    var uid: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, &uid) == noErr,
          let owned = uid?.takeRetainedValue() else { return nil }
    return owned as String
}

func parentOf(_ pid: pid_t) -> pid_t {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.size
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
    guard sysctl(&mib, 4, &info, &size, nil, 0) == 0, size > 0 else { return 0 }
    return info.kp_eproc.e_ppid
}

// The popup's own audio processes: anything descended from the app that started this.
// Sunday's replies on this Mac are not sound he is listening to.
func ownAudioProcesses() -> [AudioObjectID] {
    let root = getppid()
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr
    else { return [] }
    var objs = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &objs) == noErr
    else { return [] }
    var own: [AudioObjectID] = []
    for obj in objs {
        guard var pid = prop(obj, kAudioProcessPropertyPID, pid_t(0)), pid > 0 else { continue }
        for _ in 0..<12 {
            if pid == root || pid == getpid() { own.append(obj); break }
            pid = parentOf(pid)
            if pid <= 1 { break }
        }
    }
    return own.sorted()
}

final class Meter {
    var tap = AudioObjectID(kAudioObjectUnknown)
    var aggregate = AudioObjectID(kAudioObjectUnknown)
    var proc: AudioDeviceIOProcID?
    var outputUID: String?
    var excludedNow: [AudioObjectID] = []

    func start() -> Bool {
        outputUID = defaultOutputUID()
        let excluded = Array(Set([getpid(), getppid()].compactMap(processObject) + ownAudioProcesses())).sorted()
        excludedNow = excluded
        let desc = CATapDescription(stereoGlobalTapButExcludeProcesses: excluded)
        desc.uuid = UUID()
        desc.isPrivate = true
        desc.muteBehavior = .unmuted
        guard AudioHardwareCreateProcessTap(desc, &tap) == noErr else { return false }

        var config: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Sunday output meter",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                               kAudioSubTapUIDKey: desc.uuid.uuidString]],
        ]
        if let uid = outputUID {
            config[kAudioAggregateDeviceMainSubDeviceKey] = uid
            config[kAudioAggregateDeviceSubDeviceListKey] = [[kAudioSubDeviceUIDKey: uid]]
        }
        guard AudioHardwareCreateAggregateDevice(config as CFDictionary, &aggregate) == noErr else {
            stop(); return false
        }

        let queue = DispatchQueue(label: "sunday.meter", qos: .userInitiated)
        let err = AudioDeviceCreateIOProcIDWithBlock(&proc, aggregate, queue) { _, input, _, _, _ in
            var level: Float = 0
            for buf in UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input)) {
                guard let data = buf.mData else { continue }
                let n = Int(buf.mDataByteSize) / MemoryLayout<Float>.size
                if n == 0 { continue }
                let samples = data.bindMemory(to: Float.self, capacity: n)
                var sum: Float = 0
                for i in 0..<n { sum += samples[i] * samples[i] }
                level = max(level, (sum / Float(n)).squareRoot())
            }
            levelLock.lock()
            loudest = max(loudest, level)
            lastCallback = Date()
            levelLock.unlock()
        }
        guard err == noErr, AudioDeviceStart(aggregate, proc) == noErr else { stop(); return false }
        levelLock.lock(); lastCallback = Date(); levelLock.unlock()
        return true
    }

    func stop() {
        if aggregate != kAudioObjectUnknown {
            if let p = proc { AudioDeviceStop(aggregate, p); AudioDeviceDestroyIOProcID(aggregate, p) }
            AudioHardwareDestroyAggregateDevice(aggregate)
        }
        if tap != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tap) }
        proc = nil
        aggregate = AudioObjectID(kAudioObjectUnknown)
        tap = AudioObjectID(kAudioObjectUnknown)
    }
}

setvbuf(stdout, nil, _IOLBF, 0)
let meter = Meter()
for sig in [SIGTERM, SIGINT, SIGHUP] {
    signal(sig) { _ in meter.stop(); exit(0) }
}
if !meter.start() {
    FileHandle.standardError.write("audio-playing: could not create the process tap\n".data(using: .utf8)!)
    exit(1)
}

var last: Bool? = nil
var lastSound = Date.distantPast
var tick = 0
while true {
    usleep(100_000)
    levelLock.lock()
    let level = loudest
    loudest = 0
    let stalled = Date().timeIntervalSince(lastCallback) > 3
    levelLock.unlock()

    // A new default output (headphones, AirPods) or a device that stopped clocking
    // leaves the aggregate dead; build it again around the current one.
    // Once a second, see whether one of the popup's own processes started or stopped
    // using audio; the tap is built again to leave it out.
    tick += 1
    let ownChanged = tick % 10 == 0
        && Array(Set([getpid(), getppid()].compactMap(processObject) + ownAudioProcesses())).sorted() != meter.excludedNow
    if stalled || defaultOutputUID() != meter.outputUID || ownChanged {
        meter.stop()
        if !meter.start() { exit(1) }
    }

    if level > threshold { lastSound = Date() }
    let now = Date().timeIntervalSince(lastSound) < hold
    if now != last {
        print(now ? "1" : "0")
        last = now
    }
}
