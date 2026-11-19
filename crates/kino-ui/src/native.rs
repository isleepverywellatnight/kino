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
    EnableWindow, INPUT, INPUT_0, INPUT_MOUSE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEINPUT, SendInput,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, EnumChildWindows, GetClientRect, SWP_NOACTIVATE, SWP_NOZORDER, SetCursorPos,
    SetWindowPos, WINDOW_EX_STYLE, WS_CHILD, WS_CLIPSIBLINGS, WS_DISABLED, WS_VISIBLE,
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
/// (physical pixels), through Windows' input queue like a user's.
pub fn click(parent: isize, x: i32, y: i32) {
    let mut point = POINT { x, y };
    let button = |flags| INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dwFlags: flags,
                ..Default::default()
            },
        },
    };
    // SAFETY: plain input calls on this process's window.
    unsafe {
        if !ClientToScreen(hwnd(parent), &mut point).as_bool() {
            return;
        }
        if SetCursorPos(point.x, point.y).is_err() {
            return;
        }
        SendInput(
            &[button(MOUSEEVENTF_LEFTDOWN), button(MOUSEEVENTF_LEFTUP)],
            std::mem::size_of::<INPUT>() as i32,
        );
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
