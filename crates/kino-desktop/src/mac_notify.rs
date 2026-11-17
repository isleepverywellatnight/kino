//! macOS notifications (Notification Centre) through Apple's UserNotifications
//! framework. Only inside the KINO.app bundle: elsewhere (development builds)
//! `create` returns None, so macOS never asks permission for another program.
//! A click brings KINO to the front and opens the series; a notification also
//! shows while KINO is in front.

use std::ptr::NonNull;
use std::sync::mpsc;
use std::time::Duration;

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::{AnyObject, Bool, ProtocolObject};
use objc2::{AllocAnyThread, DefinedClass, define_class, msg_send};
use objc2_foundation::{NSBundle, NSDictionary, NSError, NSObject, NSObjectProtocol, NSString};
use objc2_user_notifications::{
    UNAuthorizationOptions, UNMutableNotificationContent, UNNotification,
    UNNotificationPresentationOptions, UNNotificationRequest, UNNotificationResponse,
    UNNotificationSettings, UNNotificationSound, UNUserNotificationCenter,
    UNUserNotificationCenterDelegate,
};
use serde_json::{Map, Value};

const BUNDLE_ID: &str = "com.kino.desktop";

type OnClick = Box<dyn Fn(Map<String, Value>) + Send + Sync>;

pub struct Ivars {
    on_click: OnClick,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "KinoNotificationDelegate"]
    #[ivars = Ivars]
    struct Delegate;

    unsafe impl NSObjectProtocol for Delegate {}

    unsafe impl UNUserNotificationCenterDelegate for Delegate {
        #[unsafe(method(userNotificationCenter:willPresentNotification:withCompletionHandler:))]
        fn will_present(
            &self,
            _center: &UNUserNotificationCenter,
            _notification: &UNNotification,
            handler: &block2::DynBlock<dyn Fn(UNNotificationPresentationOptions)>,
        ) {
            handler.call((UNNotificationPresentationOptions::Banner
                | UNNotificationPresentationOptions::List
                | UNNotificationPresentationOptions::Sound,));
        }

        #[unsafe(method(userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:))]
        fn did_receive(
            &self,
            _center: &UNUserNotificationCenter,
            response: &UNNotificationResponse,
            handler: &block2::DynBlock<dyn Fn()>,
        ) {
            let info = response.notification().request().content().userInfo();
            let mut found = Map::new();
            for key in info.allKeys().iter() {
                let (Some(k), Some(v)) = (key.downcast_ref::<NSString>(), info.objectForKey(&key))
                else {
                    continue;
                };
                if let Some(v) = v.downcast_ref::<NSString>() {
                    found.insert(k.to_string(), Value::String(v.to_string()));
                }
            }
            (self.ivars().on_click)(found);
            handler.call(());
        }
    }
);

impl Delegate {
    fn new(on_click: OnClick) -> Retained<Self> {
        let this = Self::alloc().set_ivars(Ivars { on_click });
        unsafe { msg_send![super(this), init] }
    }
}

pub struct MacNotifier;

/// The notifier, inside the KINO.app bundle only.
pub fn create(on_click: OnClick) -> Option<MacNotifier> {
    let bundle = NSBundle::mainBundle().bundleIdentifier()?.to_string();
    if bundle != BUNDLE_ID {
        return None;
    }
    let delegate = Delegate::new(on_click);
    UNUserNotificationCenter::currentNotificationCenter()
        .setDelegate(Some(ProtocolObject::from_ref(&*delegate)));
    // The centre holds its delegate weakly: this one lives as long as KINO.
    let _ = Retained::into_raw(delegate);
    Some(MacNotifier)
}

impl kino::alerts::Notifier for MacNotifier {
    fn status(&self) -> String {
        let (tx, rx) = mpsc::channel();
        let block = RcBlock::new(move |settings: NonNull<UNNotificationSettings>| {
            let status = unsafe { settings.as_ref() }.authorizationStatus().0;
            let _ = tx.send(status);
        });
        UNUserNotificationCenter::currentNotificationCenter()
            .getNotificationSettingsWithCompletionHandler(&block);
        match rx.recv_timeout(Duration::from_secs(3)) {
            Ok(0) => "not_determined",
            Ok(1) => "denied",
            Ok(2) => "authorized",
            Ok(3) => "provisional",
            Ok(4) => "ephemeral",
            _ => "unknown",
        }
        .into()
    }

    fn request_permission(&self) -> bool {
        let (tx, rx) = mpsc::channel();
        let block = RcBlock::new(move |granted: Bool, _error: *mut NSError| {
            let _ = tx.send(granted.as_bool());
        });
        UNUserNotificationCenter::currentNotificationCenter()
            .requestAuthorizationWithOptions_completionHandler(
                UNAuthorizationOptions::Alert | UNAuthorizationOptions::Sound,
                &block,
            );
        rx.recv_timeout(Duration::from_secs(60)).is_ok()
    }

    fn send(&self, id: &str, title: &str, body: &str, info: &Map<String, Value>) {
        let cut = |s: &str, n: usize| NSString::from_str(&s.chars().take(n).collect::<String>());
        let content = UNMutableNotificationContent::new();
        content.setTitle(&cut(title, 120));
        content.setBody(&cut(body, 240));
        content.setSound(Some(&UNNotificationSound::defaultSound()));
        if !info.is_empty() {
            let keys: Vec<Retained<NSString>> =
                info.keys().map(|k| NSString::from_str(k)).collect();
            let values: Vec<Retained<NSString>> = info
                .values()
                .map(|v| NSString::from_str(&kino::play::text(v)))
                .collect();
            let keys: Vec<&NSString> = keys.iter().map(|k| &**k).collect();
            let values: Vec<&NSString> = values.iter().map(|v| &**v).collect();
            let dictionary = NSDictionary::from_slices(&keys, &values);
            // SAFETY: an NSDictionary of strings is an NSDictionary of objects.
            let untyped: Retained<NSDictionary<AnyObject, AnyObject>> =
                unsafe { Retained::cast_unchecked(dictionary) };
            unsafe { content.setUserInfo(&untyped) };
        }
        let request = UNNotificationRequest::requestWithIdentifier_content_trigger(
            &cut(id, 120),
            &content,
            None,
        );
        UNUserNotificationCenter::currentNotificationCenter()
            .addNotificationRequest_withCompletionHandler(&request, None);
    }
}
