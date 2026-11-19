//! The native video path on Windows: mpv draws with the GPU in a child window
//! of KINO's window, and gpui, composed above child windows
//! (DirectComposition), leaves the video's area transparent so the picture
//! shows under its controls. No frame goes through memory.
//!
//! The child window is disabled, and so are those mpv makes inside it: a
//! disabled child passes the mouse to its parent, so gpui's controls over the
//! video still receive their clicks.

use windows::Win32::Foundation::{FILETIME, HWND, LPARAM, POINT, RECT};
use windows::Win32::Graphics::Gdi::ClientToScreen;
use windows::Win32::System::Threading::{GetCurrentProcess, GetProcessTimes};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    EnableWindow, INPUT, INPUT_0, INPUT_MOUSE, IsWindowEnabled, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEINPUT, SendInput,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, EnumChildWindows, GW_CHILD, GW_HWNDNEXT, GetClassNameW, GetClientRect,
    GetWindow, SWP_NOACTIVATE, SWP_NOZORDER, SetCursorPos, SetWindowPos, WINDOW_EX_STYLE, WS_CHILD,
    WS_CLIPSIBLINGS, WS_DISABLED, WS_VISIBLE, WindowFromPoint,
};
use windows::core::{BOOL, w};

fn hwnd(handle: isize) -> HWND {
    HWND(handle as *mut _)
}

/// The child window mpv draws in, inside `parent` (KINO's window).
pub fn create_host(parent: isize) -> Option<isize> {
    // SAFETY: a plain child window of a window this process owns.
    unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            w!("STATIC"),
            w!(""),
            WS_CHILD | WS_VISIBLE | WS_DISABLED | WS_CLIPSIBLINGS,
            0,
            0,
            1,
            1,
            Some(hwnd(parent)),
            None,
            None,
            None,
        )
    }
    .ok()
    .map(|h| h.0 as isize)
}

/// The host fills `parent`'s client area below `top` (in physical pixels),
/// and every window mpv made inside it stays disabled.
pub fn place_host(parent: isize, host: isize, top: i32) {
    let mut client = RECT::default();
    // SAFETY: windows of this process; the callback only disables them.
    unsafe {
        if GetClientRect(hwnd(parent), &mut client).is_err() {
            return;
        }
        let _ = SetWindowPos(
            hwnd(host),
            None,
            0,
            top,
            client.right,
            (client.bottom - top).max(1),
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
        unsafe extern "system" fn disable(child: HWND, _: LPARAM) -> BOOL {
            // SAFETY: a child window of the host.
            unsafe {
                let _ = EnableWindow(child, false);
            }
            BOOL(1)
        }
        let _ = EnumChildWindows(Some(hwnd(host)), Some(disable), LPARAM(0));
    }
}

/// `--bench`: a real left click at `(x, y)` in `parent`'s client area
/// (physical pixels), through Windows' input queue like a user's: the cursor
/// moves, rests, then the button goes down and up, from another thread so
/// gpui's own thread handles each message as it comes.
pub fn click(parent: isize, x: i32, y: i32) {
    let mut point = POINT { x, y };
    // SAFETY: a query on this process's window.
    if !unsafe { ClientToScreen(hwnd(parent), &mut point) }.as_bool() {
        return;
    }
    std::thread::spawn(move || {
        let button = |flags| INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dwFlags: flags,
                    ..Default::default()
                },
            },
        };
        let send = |flags| {
            // SAFETY: one mouse input.
            unsafe { SendInput(&[button(flags)], std::mem::size_of::<INPUT>() as i32) };
        };
        // SAFETY: moves the cursor.
        if unsafe { SetCursorPos(point.x, point.y) }.is_err() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(150));
        send(MOUSEEVENTF_LEFTDOWN);
        std::thread::sleep(std::time::Duration::from_millis(60));
        send(MOUSEEVENTF_LEFTUP);
    });
}

/// `--bench`: what Windows finds at `(x, y)` in `parent`'s client area —
/// which window, of what class, enabled or not — and the host's children.
pub fn probe(parent: isize, host: isize, x: i32, y: i32) -> String {
    let mut point = POINT { x, y };
    let describe = |window: HWND| {
        let mut class = [0u16; 64];
        // SAFETY: a window handle and a buffer of its length.
        let length = unsafe { GetClassNameW(window, &mut class) };
        let role = match window.0 as isize {
            w if w == parent => "kino",
            w if w == host => "host",
            _ => "other",
        };
        // SAFETY: a window handle.
        let enabled = unsafe { IsWindowEnabled(window) }.as_bool();
        format!(
            "{role}:{}:{}",
            String::from_utf16_lossy(&class[..length.max(0) as usize]),
            if enabled { "enabled" } else { "disabled" }
        )
    };
    // SAFETY: plain queries on this process's windows.
    unsafe {
        let _ = ClientToScreen(hwnd(parent), &mut point);
        let under = WindowFromPoint(point);
        let mut children = Vec::new();
        let mut child = GetWindow(hwnd(host), GW_CHILD).ok();
        while let Some(c) = child {
            children.push(describe(c));
            child = GetWindow(c, GW_HWNDNEXT).ok();
        }
        format!(
            "under={} children=[{}]",
            describe(under),
            children.join(",")
        )
    }
}

/// Processor time this process has used, in ms.
pub fn cpu_ms() -> f64 {
    let (mut created, mut exited, mut kernel, mut user) = Default::default();
    // SAFETY: four FILETIMEs for this process.
    unsafe {
        let _ = GetProcessTimes(
            GetCurrentProcess(),
            &mut created,
            &mut exited,
            &mut kernel,
            &mut user,
        );
    }
    let ms = |t: FILETIME| {
        ((u64::from(t.dwHighDateTime) << 32) | u64::from(t.dwLowDateTime)) as f64 / 10_000.0
    };
    ms(kernel) + ms(user)
}
