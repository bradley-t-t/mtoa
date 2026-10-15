// Prints a line whenever an app comes to the front: its process id, a tab, its bundle id.
// Sunday.app reads it to fold the deck down to the transcript strip once he turns to another app.
import AppKit

setvbuf(stdout, nil, _IOLBF, 0)
NSWorkspace.shared.notificationCenter.addObserver(
  forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { note in
  guard let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
  print("\(app.processIdentifier)\t\(app.bundleIdentifier ?? app.localizedName ?? "")")
}
RunLoop.main.run()
