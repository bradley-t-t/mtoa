// The Mac ear's audio. Captures the built-in microphone twice: through macOS voice
// processing, which cancels whatever this process plays, so Sunday's own replies
// played here never reach the wake word or the transcript, and plain, as the
// microphone hears the room. Voice processing also strips a voice from across the
// room down to a few unintelligible scraps (2026-10-13: three "Sunday, what time is
// it?" from his usual spot never woke it, and the plain microphone heard every word),
// so mac_ear.py listens to the plain one except while sound plays here. Plays the replies.
//
// Framing on both pipes: one type byte, a little-endian UInt32 length, the payload.
//   stdout  'A'  16 kHz mono s16le microphone audio, echo cancelled
//           'R'  16 kHz mono s16le microphone audio, plain: the built-in mic as a recording app gets it
//           'E'  one JSON event: {"event":"started"|"drained"|"restarted"|"error",...}
//   stdin   'P'  48 kHz mono s16le audio to play
//           'F'  the end of what is to be played; "drained" follows once it has
//           'S'  stop playing now and drop what is queued
//           'V'  a little-endian Float32: the reply volume, 1 for full, lower while he talks over it
//
// It is started by mac_ear.py, which owns the wake word and the connection. The
// microphone permission is asked for, and granted to, Sunday.app.

import AVFoundation
import CoreAudio
import CoreMedia
import Foundation

let outLock = NSLock()
func emit(_ type: UInt8, _ payload: Data) {
    var head = Data([type])
    var n = UInt32(payload.count).littleEndian
    head.append(Data(bytes: &n, count: 4))
    outLock.lock()
    FileHandle.standardOutput.write(head + payload)
    outLock.unlock()
}

func event(_ fields: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: fields) { emit(UInt8(ascii: "E"), d) }
}

func log(_ s: String) {
    FileHandle.standardError.write("ear-audio: \(s)\n".data(using: .utf8)!)
}

func builtInMicrophone() -> AudioDeviceID? {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr
    else { return nil }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr
    else { return nil }
    for id in ids {
        var transport: UInt32 = 0
        var tsize = UInt32(MemoryLayout<UInt32>.size)
        var taddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyTransportType,
                                               mScope: kAudioObjectPropertyScopeGlobal,
                                               mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(id, &taddr, 0, nil, &tsize, &transport) == noErr,
              transport == kAudioDeviceTransportTypeBuiltIn else { continue }
        var saddr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams,
                                               mScope: kAudioObjectPropertyScopeInput,
                                               mElement: kAudioObjectPropertyElementMain)
        var ssize: UInt32 = 0
        if AudioObjectGetPropertyDataSize(id, &saddr, 0, nil, &ssize) == noErr, ssize > 0 { return id }
    }
    return nil
}

// The built-in microphone with nothing done to it, through a capture session of its own
// (as any recording app gets it): voice processing on the engine does not reach it. An
// AVAudioEngine beside the voice-processing one shares the output device with it, and
// the two kept stopping each other.
final class PlainMic: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    let session = AVCaptureSession()
    let queue = DispatchQueue(label: "sunday.plain-mic", qos: .userInitiated)

    func device() -> AVCaptureDevice? {
        if let mic = builtInMicrophone() {
            var uid: Unmanaged<CFString>?
            var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID,
                                                  mScope: kAudioObjectPropertyScopeGlobal,
                                                  mElement: kAudioObjectPropertyElementMain)
            if AudioObjectGetPropertyData(mic, &addr, 0, nil, &size, &uid) == noErr,
               let owned = uid?.takeRetainedValue(), let dev = AVCaptureDevice(uniqueID: owned as String) {
                return dev
            }
        }
        return AVCaptureDevice.default(for: .audio)
    }

    func start() throws -> String {
        guard let dev = device() else { throw NSError(domain: "ear", code: 2) }
        let input = try AVCaptureDeviceInput(device: dev)
        let output = AVCaptureAudioDataOutput()
        output.audioSettings = [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        output.setSampleBufferDelegate(self, queue: queue)
        session.beginConfiguration()
        guard session.canAddInput(input), session.canAddOutput(output) else {
            session.commitConfiguration()
            throw NSError(domain: "ear", code: 3)
        }
        session.addInput(input)
        session.addOutput(output)
        session.commitConfiguration()
        session.startRunning()
        return dev.localizedName
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let block = CMSampleBufferGetDataBuffer(sample) else { return }
        let n = CMBlockBufferGetDataLength(block)
        guard n > 0 else { return }
        var data = Data(count: n)
        let ok = data.withUnsafeMutableBytes { raw in
            CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: n, destination: raw.baseAddress!) == kCMBlockBufferNoErr
        }
        if ok { emit(UInt8(ascii: "R"), data) }
    }
}

final class Ear {
    var engine = AVAudioEngine()
    var player = AVAudioPlayerNode()
    let plain = PlainMic()
    let playFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000, channels: 1, interleaved: false)!
    let micFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    var converter: AVAudioConverter?
    let lock = NSLock()
    var scheduled = 0          // buffers handed to the player and not yet played
    var ending = false         // 'F' arrived: report "drained" when scheduled reaches 0
    var generation = 0         // bumped by a stop, so late completions of dropped audio are ignored

    func start() throws {
        engine = AVAudioEngine()
        player = AVAudioPlayerNode()
        let input = engine.inputNode
        // The output side has to exist before voice processing is switched on, or
        // the voice-processing unit fails to initialize (-10875).
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)
        // The built-in microphone, whatever the default input is: an iPhone or a
        // headset as the default would move the ear away from the room.
        if ProcessInfo.processInfo.environment["EAR_PICK_DEVICE"] == "1", let mic = builtInMicrophone(), let unit = input.audioUnit {
            var dev = mic
            let err = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 1,
                                           &dev, UInt32(MemoryLayout<AudioDeviceID>.size))
            if err != noErr { log("could not select the built-in microphone (\(err)); using the default input") }
        }
        try input.setVoiceProcessingEnabled(true)
        // For testing with a recording played on this Mac, which voice processing would cancel.
        if ProcessInfo.processInfo.environment["EAR_BYPASS_VOICE_PROCESSING"] == "1" {
            input.isVoiceProcessingBypassed = true
            log("voice processing bypassed for a test")
        }
        // Voice processing ducks every other app's sound while it runs; the ear runs
        // all the time, so ask for the least ducking there is.
        if #available(macOS 14.0, *) {
            input.voiceProcessingOtherAudioDuckingConfiguration =
                AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
        }
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0 else { throw NSError(domain: "ear", code: 1) }
        // Voice processing can report several channels; the first carries the processed mic.
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inFormat.sampleRate, channels: 1,
                                 interleaved: false)!
        converter = AVAudioConverter(from: mono, to: micFormat)
        input.installTap(onBus: 0, bufferSize: AVAudioFrameCount(inFormat.sampleRate / 50), format: inFormat) {
            [weak self] buf, _ in self?.captured(buf, mono: mono)
        }
        engine.prepare()
        try engine.start()
        player.play()
        event(["event": "started", "rate": inFormat.sampleRate, "channels": inFormat.channelCount])
    }

    // Without the plain microphone the ear still works, on the voice-processed one alone.
    func startPlain() {
        do {
            event(["event": "plain started", "device": try plain.start()])
        } catch {
            event(["event": "error", "text": "plain microphone: \(error)"])
        }
    }

    func captured(_ buf: AVAudioPCMBuffer, mono: AVAudioFormat) {
        guard let conv = converter, let src = buf.floatChannelData, buf.frameLength > 0,
              let one = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: buf.frameLength) else { return }
        one.frameLength = buf.frameLength
        memcpy(one.floatChannelData![0], src[0], Int(buf.frameLength) * MemoryLayout<Float>.size)
        let cap = AVAudioFrameCount(Double(buf.frameLength) * 16000 / mono.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: micFormat, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        conv.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return one
        }
        if out.frameLength > 0, let p = out.int16ChannelData {
            emit(UInt8(ascii: "A"), Data(bytes: p[0], count: Int(out.frameLength) * 2))
        }
    }

    func play(_ pcm: Data) {
        let n = pcm.count / 2
        guard n > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(n)) else { return }
        buf.frameLength = AVAudioFrameCount(n)
        let dst = buf.floatChannelData![0]
        pcm.withUnsafeBytes { raw in
            let s = raw.bindMemory(to: Int16.self)
            for i in 0..<n { dst[i] = Float(Int16(littleEndian: s[i])) / 32768 }
        }
        lock.lock()
        scheduled += 1
        ending = false
        let gen = generation
        lock.unlock()
        if !player.isPlaying { player.play() }
        player.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.played(gen)
        }
    }

    func played(_ gen: Int) {
        lock.lock()
        guard gen == generation else { lock.unlock(); return }
        scheduled -= 1
        let done = scheduled <= 0 && ending
        if done { ending = false }
        lock.unlock()
        if done { event(["event": "drained"]) }
    }

    func finish() {
        lock.lock()
        let done = scheduled <= 0
        ending = !done
        lock.unlock()
        if done { event(["event": "drained"]) }
    }

    func volume(_ v: Float) {
        player.volume = max(0, min(1, v))
    }

    func stop() {
        lock.lock()
        generation += 1
        scheduled = 0
        ending = false
        lock.unlock()
        player.stop()
        player.play()
        event(["event": "drained", "stopped": true])
    }

    func restart() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        lock.lock(); generation += 1; scheduled = 0; ending = false; lock.unlock()
        do {
            try start()
            event(["event": "restarted"])
        } catch {
            event(["event": "error", "text": "restart failed: \(error)"])
            exit(1)
        }
    }
}

setvbuf(stdout, nil, _IONBF, 0)
signal(SIGPIPE, SIG_IGN)
let ear = Ear()

switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .denied, .restricted:
    event(["event": "error", "text": "microphone permission denied"])
    exit(2)
case .notDetermined:
    let sem = DispatchSemaphore(value: 0)
    var ok = false
    AVCaptureDevice.requestAccess(for: .audio) { granted in ok = granted; sem.signal() }
    event(["event": "asking", "text": "waiting for microphone permission"])
    sem.wait()
    if !ok { event(["event": "error", "text": "microphone permission denied"]); exit(2) }
default:
    break
}

do {
    try ear.start()
} catch {
    event(["event": "error", "text": "could not start audio: \(error)"])
    exit(1)
}
ear.startPlain()

// A new default output, a device that went away, or the voice-processing unit
// resetting itself stops the engine; build it again.
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: nil) { _ in
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { ear.restart() }
}

Thread.detachNewThread {
    let stdin = FileHandle.standardInput
    func read(_ n: Int) -> Data? {
        var d = Data()
        while d.count < n {
            let chunk = stdin.readData(ofLength: n - d.count)
            if chunk.isEmpty { return nil }
            d.append(chunk)
        }
        return d
    }
    while let head = read(5) {
        let type = head[head.startIndex]
        let n = head.subdata(in: 1..<5).withUnsafeBytes { Int(UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self))) }
        guard let body = n > 0 ? read(n) : Data() else { break }
        DispatchQueue.main.async {
            switch type {
            case UInt8(ascii: "P"): ear.play(body)
            case UInt8(ascii: "F"): ear.finish()
            case UInt8(ascii: "S"): ear.stop()
            case UInt8(ascii: "V") where body.count >= 4:
                ear.volume(body.withUnsafeBytes { Float(bitPattern: UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self))) })
            default: break
            }
        }
    }
    // The ear went away: nothing to listen for.
    exit(0)
}

RunLoop.main.run()
