// The consent gate in front of every sign-in chrome-login.py fills.
//
// A credential leaves the vault only after the owner of this Mac has said yes
// to that one sign-in with Touch ID, an Apple Watch or the login password, in a
// system prompt no script, page or session can answer for them. The prompt
// reads "<this binary> is trying to <reason>", so the caller passes the reason
// as a lowercase clause naming the login and the host it is about to fill.
//
//   chrome-login <reason...>     exit 0 approved, 1 denied, 3 unavailable, 4 timed out
//   chrome-login --check         exit 0 when the prompt can be shown at all
//
// Built by `chrome-login.py install` into the state tree, never tracked: a
// compiled binary in a synced tree is a thing nobody can read before it runs.
import Foundation
import LocalAuthentication

let args = Array(CommandLine.arguments.dropFirst())
let context = LAContext()
context.localizedCancelTitle = "Deny"
var problem: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &problem) else {
    FileHandle.standardError.write(
        "unavailable: \(problem?.localizedDescription ?? "no owner authentication")\n"
            .data(using: .utf8)!)
    exit(3)
}
if args == ["--check"] {
    exit(0)
}
let reason = args.joined(separator: " ")
if reason.isEmpty {
    FileHandle.standardError.write("a reason is required\n".data(using: .utf8)!)
    exit(2)
}

let done = DispatchSemaphore(value: 0)
var approved = false
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, _ in
    approved = success
    done.signal()
}
// Nobody at the Mac means nobody approved. The prompt is withdrawn rather than
// left on screen for whoever sits down next to answer a request they never saw.
if done.wait(timeout: .now() + 120) == .timedOut {
    context.invalidate()
    exit(4)
}
exit(approved ? 0 : 1)
