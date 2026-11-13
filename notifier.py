"""macOS notifications for KINO (Notification Centre), through Apple's
UserNotifications framework. Only inside the KINO.app bundle: elsewhere (tests,
the development Python) `Notifier.create()` returns None, so that macOS never
asks permission for another program.

A click on a notification calls `on_click(info)` with the notification's
information (a series to open); a notification also shows while KINO is in
front.
"""
import threading

STATUS = {0: "not_determined", 1: "denied", 2: "authorized", 3: "provisional", 4: "ephemeral"}
BUNDLE_ID = "com.kino.desktop"


def running_bundle_id():
    try:
        from Foundation import NSBundle
        return str(NSBundle.mainBundle().bundleIdentifier() or "")
    except Exception:
        return ""


def _framework():
    import objc
    import UserNotifications
    from Foundation import NSObject
    return objc, UserNotifications, NSObject


_DELEGATE_CLASS = None


def _delegate_class():
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS
    objc, UN, NSObject = _framework()

    class KinoNotificationDelegate(NSObject, protocols=[objc.protocolNamed("UNUserNotificationCenterDelegate")]):
        def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, handler):
            handler(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList
                    | UN.UNNotificationPresentationOptionSound)

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, handler):
            try:
                info = response.notification().request().content().userInfo() or {}
                callback = getattr(self, "on_click", None)
                if callback:
                    callback({str(key): str(value) for key, value in dict(info).items()})
            except Exception:
                pass
            finally:
                handler()

    _DELEGATE_CLASS = KinoNotificationDelegate
    return _DELEGATE_CLASS


class Notifier:
    def __init__(self, center, delegate, framework):
        self._center = center
        self._delegate = delegate  # kept alive: the centre holds it weakly
        self._un = framework

    @classmethod
    def create(cls, on_click=None, bundle_id=None):
        if (bundle_id if bundle_id is not None else running_bundle_id()) != BUNDLE_ID:
            return None
        try:
            _, UN, _ = _framework()
            center = UN.UNUserNotificationCenter.currentNotificationCenter()
            delegate = _delegate_class().alloc().init()
            delegate.on_click = on_click
            center.setDelegate_(delegate)
            return cls(center, delegate, UN)
        except Exception:
            return None

    def request_permission(self, done=None):
        """macOS asks the user once; `done(granted)` gets the answer."""
        options = self._un.UNAuthorizationOptionAlert | self._un.UNAuthorizationOptionSound

        def answer(granted, error):
            if done:
                done(bool(granted))
        self._center.requestAuthorizationWithOptions_completionHandler_(options, answer)

    def status(self, timeout=3.0):
        """« authorized », « denied », « not_determined »… or « unknown »."""
        found = {}
        ready = threading.Event()

        def settings(value):
            found["status"] = STATUS.get(int(value.authorizationStatus()), "unknown")
            ready.set()
        self._center.getNotificationSettingsWithCompletionHandler_(settings)
        ready.wait(timeout)
        return found.get("status", "unknown")

    def send(self, identifier, title, body, info=None):
        content = self._un.UNMutableNotificationContent.alloc().init()
        content.setTitle_(str(title)[:120])
        content.setBody_(str(body)[:240])
        content.setSound_(self._un.UNNotificationSound.defaultSound())
        if info:
            content.setUserInfo_({str(key): str(value) for key, value in info.items()})
        request = self._un.UNNotificationRequest.requestWithIdentifier_content_trigger_(str(identifier)[:120], content, None)
        self._center.addNotificationRequest_withCompletionHandler_(request, None)
