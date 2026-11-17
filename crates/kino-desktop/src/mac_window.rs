//! The Mac's side of KINO's window: the menu bar (KINO, Édition with copy
//! and paste, Fenêtre with macOS tiling), the unified title bar that centres
//! the window buttons in KINO's header, full screen from the green button,
//! and the web view's own full screen and picture in picture for videos.

use objc2::rc::Retained;
use objc2::runtime::{AnyObject, Sel};
use objc2::{MainThreadMarker, MainThreadOnly, Message, msg_send, sel};
use objc2_app_kit::{
    NSApplication, NSEventModifierFlags, NSMenu, NSMenuItem, NSToolbar, NSView, NSWindow,
    NSWindowCollectionBehavior, NSWindowDidExitFullScreenNotification, NSWindowToolbarStyle,
    NSWindowWillEnterFullScreenNotification,
};
use objc2_foundation::{NSNotification, NSNotificationCenter, NSString};

fn item(
    mtm: MainThreadMarker,
    title: &str,
    action: Option<Sel>,
    key: &str,
) -> Retained<NSMenuItem> {
    // SAFETY: standard responder-chain actions, handled by AppKit or the web view.
    unsafe {
        NSMenuItem::initWithTitle_action_keyEquivalent(
            NSMenuItem::alloc(mtm),
            &NSString::from_str(title),
            action,
            &NSString::from_str(key),
        )
    }
}

fn submenu(
    mtm: MainThreadMarker,
    title: &str,
    items: Vec<Retained<NSMenuItem>>,
) -> (Retained<NSMenuItem>, Retained<NSMenu>) {
    let menu = NSMenu::initWithTitle(NSMenu::alloc(mtm), &NSString::from_str(title));
    for i in items {
        menu.addItem(&i);
    }
    let holder = item(mtm, title, None, "");
    holder.setSubmenu(Some(&menu));
    (holder, menu)
}

fn with_modifiers(item: Retained<NSMenuItem>, flags: NSEventModifierFlags) -> Retained<NSMenuItem> {
    item.setKeyEquivalentModifierMask(flags);
    item
}

/// KINO's menu bar, in French as the rest of the app.
fn menu_bar(mtm: MainThreadMarker, window: &NSWindow) {
    let app = NSApplication::sharedApplication(mtm);
    let bar = NSMenu::initWithTitle(NSMenu::alloc(mtm), &NSString::from_str(""));
    let separator = || NSMenuItem::separatorItem(mtm);
    let (kino, _) = submenu(
        mtm,
        "KINO",
        vec![
            item(
                mtm,
                "À propos de KINO",
                Some(sel!(orderFrontStandardAboutPanel:)),
                "",
            ),
            separator(),
            item(mtm, "Masquer KINO", Some(sel!(hide:)), "h"),
            with_modifiers(
                item(
                    mtm,
                    "Masquer les autres",
                    Some(sel!(hideOtherApplications:)),
                    "h",
                ),
                NSEventModifierFlags::Option | NSEventModifierFlags::Command,
            ),
            item(mtm, "Tout afficher", Some(sel!(unhideAllApplications:)), ""),
            separator(),
            item(mtm, "Quitter KINO", Some(sel!(terminate:)), "q"),
        ],
    );
    let (edit, _) = submenu(
        mtm,
        "Édition",
        vec![
            item(mtm, "Annuler", Some(sel!(undo:)), "z"),
            with_modifiers(
                item(mtm, "Rétablir", Some(sel!(redo:)), "z"),
                NSEventModifierFlags::Shift | NSEventModifierFlags::Command,
            ),
            separator(),
            item(mtm, "Couper", Some(sel!(cut:)), "x"),
            item(mtm, "Copier", Some(sel!(copy:)), "c"),
            item(mtm, "Coller", Some(sel!(paste:)), "v"),
            item(mtm, "Tout sélectionner", Some(sel!(selectAll:)), "a"),
        ],
    );
    // « Fenêtre »: macOS adds « Déplacer et redimensionner » (tiling) to it.
    let (windows, windows_menu) = submenu(
        mtm,
        "Fenêtre",
        vec![
            item(
                mtm,
                "Placer dans le Dock",
                Some(sel!(performMiniaturize:)),
                "m",
            ),
            item(mtm, "Réduire/agrandir", Some(sel!(performZoom:)), ""),
            with_modifiers(
                item(mtm, "Plein écran", Some(sel!(toggleFullScreen:)), "f"),
                NSEventModifierFlags::Control | NSEventModifierFlags::Command,
            ),
            separator(),
            item(
                mtm,
                "Tout ramener au premier plan",
                Some(sel!(arrangeInFront:)),
                "",
            ),
        ],
    );
    for menu in [kino, edit, windows] {
        bar.addItem(&menu);
    }
    app.setMainMenu(Some(&bar));
    app.setWindowsMenu(Some(&windows_menu));
    app.addWindowsItem_title_filename(window, &NSString::from_str("KINO"), false);
}

/// Whether this macOS's WebKit has the setter.
fn has(target: &AnyObject, setter: Sel) -> bool {
    // SAFETY: respondsToSelector: is NSObject's.
    unsafe { msg_send![target, respondsToSelector: setter] }
}

pub fn setup(window: &tao::window::Window, page: &NSView) {
    use tao::platform::macos::WindowExtMacOS;
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    // SAFETY: tao's window is an NSWindow living as long as the event loop.
    let ns_window: &NSWindow = unsafe { &*(window.ns_window() as *const NSWindow) };
    // A unified toolbar centres the window buttons in KINO's header.
    if ns_window.toolbar().is_none() {
        let toolbar = NSToolbar::initWithIdentifier(
            NSToolbar::alloc(mtm),
            &NSString::from_str("kino.toolbar"),
        );
        ns_window.setToolbar(Some(&toolbar));
    }
    ns_window.setToolbarStyle(NSWindowToolbarStyle::Unified);
    // An empty toolbar paints a grey band over full-screen video: hidden meanwhile.
    let center = NSNotificationCenter::defaultCenter();
    for (name, visible) in unsafe {
        [
            (NSWindowWillEnterFullScreenNotification, false),
            (NSWindowDidExitFullScreenNotification, true),
        ]
    } {
        let target: Retained<NSWindow> = ns_window.retain();
        let block = block2::RcBlock::new(move |_: std::ptr::NonNull<NSNotification>| {
            if let Some(toolbar) = target.toolbar() {
                toolbar.setVisible(visible);
            }
        });
        // SAFETY: an observer kept for the window's life (the token is not needed).
        let token = unsafe {
            center.addObserverForName_object_queue_usingBlock(
                Some(name),
                Some(ns_window),
                None,
                &block,
            )
        };
        std::mem::forget(token);
    }
    // The green button's layouts and full screen.
    ns_window.setCollectionBehavior(
        ns_window.collectionBehavior() | NSWindowCollectionBehavior::FullScreenPrimary,
    );
    menu_bar(mtm, ns_window);
    // Videos in the page: their own full screen and picture in picture.
    // SAFETY: WKWebView's configuration and preferences, read as objects.
    unsafe {
        let configuration: Option<Retained<AnyObject>> = msg_send![page, configuration];
        if let Some(configuration) = configuration {
            if has(
                &configuration,
                sel!(setAllowsPictureInPictureMediaPlayback:),
            ) {
                let _: () =
                    msg_send![&*configuration, setAllowsPictureInPictureMediaPlayback: true];
            }
            let preferences: Option<Retained<AnyObject>> = msg_send![&*configuration, preferences];
            if let Some(preferences) =
                preferences.filter(|p| has(p, sel!(setElementFullscreenEnabled:)))
            {
                let _: () = msg_send![&*preferences, setElementFullscreenEnabled: true];
            }
        }
    }
}
