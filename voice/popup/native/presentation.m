// Hides the menu bar and the Dock for Sunday.app while the full deck is up, from inside the app.
// They are this process's presentation options, which macOS applies only while Sunday is the
// active app and drops by itself once another app takes the front or Sunday quits or crashes,
// so nothing here can leave them hidden. It also notes where he was before the deck cleared the
// desktop, and puts that back when he closes the deck. main.js loads this as a Node-API addon.
#import <AppKit/AppKit.h>
#include <node_api.h>

static const NSApplicationPresentationOptions HIDDEN =
    NSApplicationPresentationHideDock | NSApplicationPresentationHideMenuBar;

// hideBars(on): true hides both bars while Sunday is in front, false gives them back.
// Returns whether they are set hidden now.
static napi_value HideBars(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value argv[1];
  bool on = false;
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  if (argc > 0) napi_get_value_bool(env, argv[0], &on);
  @try {
    [NSApp setPresentationOptions:on ? HIDDEN : NSApplicationPresentationDefault];
  } @catch (NSException *e) {
    // Options macOS refuses leave the bars as they were.
  }
  napi_value result;
  napi_get_boolean(env, ([NSApp presentationOptions] & HIDDEN) == HIDDEN, &result);
  return result;
}

// toDesktop(): Sunday, in front, hands the front to Finder, which owns the desktop. Returns
// whether it did.
static napi_value ToDesktop(napi_env env, napi_callback_info info) {
  bool done = false;
  if ([NSApp isActive]) {
    NSRunningApplication *finder =
        [NSRunningApplication runningApplicationsWithBundleIdentifier:@"com.apple.finder"].firstObject;
    done = finder && [finder activateWithOptions:0];
  }
  napi_value result;
  napi_get_boolean(env, done, &result);
  return result;
}

// snapshot(): where he was before the deck cleared the desktop. { front: pid or 0, visible: [pid] },
// the regular apps not hidden, Sunday aside.
static napi_value Snapshot(napi_env env, napi_callback_info info) {
  pid_t own = [NSProcessInfo processInfo].processIdentifier;
  NSRunningApplication *front = [NSWorkspace sharedWorkspace].frontmostApplication;
  napi_value result, visible, value;
  napi_create_object(env, &result);
  napi_create_int32(env, front && front.processIdentifier != own ? front.processIdentifier : 0, &value);
  napi_set_named_property(env, result, "front", value);
  napi_create_array(env, &visible);
  uint32_t n = 0;
  for (NSRunningApplication *app in [NSWorkspace sharedWorkspace].runningApplications) {
    if (app.activationPolicy != NSApplicationActivationPolicyRegular || app.hidden || app.processIdentifier == own) continue;
    napi_create_int32(env, app.processIdentifier, &value);
    napi_set_element(env, visible, n++, value);
  }
  napi_set_named_property(env, result, "visible", visible);
  return result;
}

// restore(visible, front): shows again the apps snapshot() found showing, then gives the front
// back to the app that had it. Returns whether that app took the front.
static napi_value Restore(napi_env env, napi_callback_info info) {
  size_t argc = 2;
  napi_value argv[2];
  napi_get_cb_info(env, info, &argc, argv, NULL, NULL);
  uint32_t n = 0;
  if (argc > 0) napi_get_array_length(env, argv[0], &n);
  for (uint32_t i = 0; i < n; i++) {
    napi_value item;
    int32_t pid = 0;
    napi_get_element(env, argv[0], i, &item);
    napi_get_value_int32(env, item, &pid);
    [[NSRunningApplication runningApplicationWithProcessIdentifier:pid] unhide];
  }
  int32_t frontPid = 0;
  if (argc > 1) napi_get_value_int32(env, argv[1], &frontPid);
  NSRunningApplication *front = frontPid ? [NSRunningApplication runningApplicationWithProcessIdentifier:frontPid] : nil;
  bool done = false;
  if (front) {
    [front unhide];
    done = [front activateWithOptions:NSApplicationActivateAllWindows];
  }
  napi_value result;
  napi_get_boolean(env, done, &result);
  return result;
}

NAPI_MODULE_INIT() {
  napi_value fn;
  napi_create_function(env, "hideBars", NAPI_AUTO_LENGTH, HideBars, NULL, &fn);
  napi_set_named_property(env, exports, "hideBars", fn);
  napi_create_function(env, "toDesktop", NAPI_AUTO_LENGTH, ToDesktop, NULL, &fn);
  napi_set_named_property(env, exports, "toDesktop", fn);
  napi_create_function(env, "snapshot", NAPI_AUTO_LENGTH, Snapshot, NULL, &fn);
  napi_set_named_property(env, exports, "snapshot", fn);
  napi_create_function(env, "restore", NAPI_AUTO_LENGTH, Restore, NULL, &fn);
  napi_set_named_property(env, exports, "restore", fn);
  return exports;
}
