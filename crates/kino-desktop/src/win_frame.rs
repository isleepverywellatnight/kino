//! KINO's window on Windows: no native title bar (KINO draws its own
//! buttons), yet minimized and restored from the taskbar, and maximized
//! within the screen's work area so the taskbar stays visible.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use windows_sys::Win32::Foundation::{HWND, RECT};
use windows_sys::Win32::Graphics::Dwm::DwmSetWindowAttribute;
use windows_sys::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromWindow,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GWL_EXSTYLE, GWL_STYLE, GetWindowLongW, GetWindowRect, SWP_FRAMECHANGED, SWP_NOACTIVATE,
    SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SetWindowLongW, SetWindowPos, WS_CAPTION,
    WS_EX_APPWINDOW, WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_SYSMENU, WS_THICKFRAME,
};

/// Window → its place before KINO maximized it.
static RESTORE: LazyLock<Mutex<HashMap<isize, (i32, i32, i32, i32)>>> =
    LazyLock::new(Default::default);

fn rect_of(window: HWND) -> (i32, i32, i32, i32) {
    let mut r = RECT {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    // SAFETY: KINO's own window.
    unsafe { GetWindowRect(window, &mut r) };
    (r.left, r.top, r.right - r.left, r.bottom - r.top)
}

/// The screen the window is on, without the taskbar.
fn work_area(window: HWND) -> (i32, i32, i32, i32) {
    // SAFETY: a zeroed MONITORINFO with its size, filled by Windows.
    unsafe {
        let mut info: MONITORINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<MONITORINFO>() as u32;
        GetMonitorInfoW(
            MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST),
            &mut info,
        );
        let r = info.rcWork;
        (r.left, r.top, r.right - r.left, r.bottom - r.top)
    }
}

fn place(window: HWND, (x, y, w, h): (i32, i32, i32, i32)) {
    // SAFETY: KINO's own window.
    unsafe {
        SetWindowPos(
            window,
            std::ptr::null_mut(),
            x,
            y,
            w,
            h,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
    };
}

/// No caption and no sizing frame; the taskbar still minimizes and restores it.
pub fn frameless(window: isize) {
    let window = window as HWND;
    // SAFETY: style changes on KINO's own window, applied with SWP_FRAMECHANGED.
    unsafe {
        let style = (GetWindowLongW(window, GWL_STYLE) as u32
            & !(WS_CAPTION | WS_THICKFRAME | WS_MAXIMIZEBOX))
            | WS_MINIMIZEBOX
            | WS_SYSMENU;
        SetWindowLongW(window, GWL_STYLE, style as i32);
        SetWindowLongW(
            window,
            GWL_EXSTYLE,
            GetWindowLongW(window, GWL_EXSTYLE) | WS_EX_APPWINDOW as i32,
        );
        SetWindowPos(
            window,
            std::ptr::null_mut(),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        );
        // Windows 11: rounded corners, no coloured border.
        let round: i32 = 2;
        let no_border: u32 = 0xFFFF_FFFE;
        DwmSetWindowAttribute(window, 33, (&raw const round).cast(), 4);
        DwmSetWindowAttribute(window, 34, (&raw const no_border).cast(), 4);
    }
}

pub fn is_maximized(window: isize) -> bool {
    RESTORE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .contains_key(&window)
}

pub fn set_maximized(window: isize, on: bool) {
    let mut restore = RESTORE.lock().unwrap_or_else(|e| e.into_inner());
    let handle = window as HWND;
    if on {
        restore.entry(window).or_insert_with(|| rect_of(handle));
        place(handle, work_area(handle));
    } else if let Some(previous) = restore.remove(&window) {
        place(handle, previous);
    }
}
