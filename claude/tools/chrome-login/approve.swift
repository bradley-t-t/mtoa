// The approval step of a CryptoFort sign-in, built by `chrome-login.py install`.
//
// One system authentication prompt naming the site and the account, answered by
// Touch ID, a paired Apple Watch or the login password. Nothing a session can
// drive answers it: the prompt belongs to the operating system, and the finger,
// the watch and the password all belong to the person at the machine.
//
//   CryptoFort "<reason>" [timeout-seconds]
//
// The binary is named CryptoFort because macOS words the prompt as
// "<process> is trying to <reason>". Exit 0 approved, 1 refused or timed out,
// 2 when this Mac has no way to ask.
import Foundation
import LocalAuthentication

let arguments = CommandLine.arguments
guard arguments.count >= 2, !arguments[1].isEmpty else {
    FileHandle.standardError.write("usage: CryptoFort <reason> [timeout-seconds]\n".data(using: .utf8)!)
    exit(2)
}
let reason = arguments[1]
let timeout = arguments.count >= 3 ? (Double(arguments[2]) ?? 90) : 90

let context = LAContext()
var unavailable: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &unavailable) else {
    FileHandle.standardError.write("no authentication method: \(unavailable?.localizedDescription ?? "unknown")\n".data(using: .utf8)!)
    exit(2)
}

let answered = DispatchSemaphore(value: 0)
var approved = false
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, _ in
    approved = ok
    answered.signal()
}
// A prompt nobody answers would hold the native host, and the page waiting on
// it, open forever.
if answered.wait(timeout: .now() + timeout) == .timedOut {
    context.invalidate()
    exit(1)
}
exit(approved ? 0 : 1)
