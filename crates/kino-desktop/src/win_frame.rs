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
/// (x, y, width, height) of a window.
type Place = (i32, i32, i32, i32);

static RESTORE: LazyLock<Mutex<HashMap<isize, Place>>> = LazyLock::new(Default::default);

fn rect_of(window: HWND) -> Place {
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
fn work_area(window: HWND) -> Place {
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

fn place(window: HWND, (x, y, w, h): Place) {
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

/// Whether the main mouse button is still down: a drag starts only then, or
/// Windows' move loop would wait for a release that already happened (and
/// swallow the next press, the second half of a double press).
pub fn button_down() -> bool {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, VK_LBUTTON, VK_RBUTTON,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_SWAPBUTTON};
    // SAFETY: plain Win32 queries.
    unsafe {
        let button = if GetSystemMetrics(SM_SWAPBUTTON) != 0 {
            VK_RBUTTON
        } else {
            VK_LBUTTON
        };
        (GetAsyncKeyState(button as i32) as u16 & 0x8000) != 0
    }
}

/// The latest presses of the main button: when, and where on screen.
static PRESSES: LazyLock<Mutex<Vec<(std::time::Instant, (i32, i32))>>> =
    LazyLock::new(Default::default);

fn double_click_time() -> std::time::Duration {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::GetDoubleClickTime;
    // SAFETY: a plain Win32 query.
    std::time::Duration::from_millis(u64::from(unsafe { GetDoubleClickTime() }))
}

/// Records each press of the main button, all along. The page may receive a
/// press late, or never the second half of a double press: the app knows.
pub fn watch_presses() {
    std::thread::spawn(|| {
        let mut was_down = false;
        loop {
            let down = button_down();
            if down && !was_down {
                let mut presses = PRESSES.lock().unwrap_or_else(|e| e.into_inner());
                presses.push((std::time::Instant::now(), cursor()));
                let keep = presses.len().saturating_sub(4);
                presses.drain(..keep);
            }
            was_down = down;
            std::thread::sleep(std::time::Duration::from_millis(8));
        }
    });
}

/// Whether the last two presses make a double press (at one spot, within the
/// double-click time, just now). Taken once: the presses are forgotten.
pub fn take_double() -> bool {
    let mut presses = PRESSES.lock().unwrap_or_else(|e| e.into_inner());
    let found = match presses.as_slice() {
        [.., (first, a), (second, b)] => {
            second.duration_since(*first) <= double_click_time()
                && second.elapsed() < std::time::Duration::from_millis(1500)
                && (a.0 - b.0).abs() <= 5
                && (a.1 - b.1).abs() <= 5
        }
        _ => false,
    };
    if found {
        presses.clear();
    }
    found
}

/// What a press on KINO's bar turned out to be.
pub enum Press {
    /// The mouse moved 4 px with the button held.
    Drag,
    /// The second press of a double press.
    Double,
    Click,
}

/// Follows a press on KINO's bar while its button is down, then for the
/// double-click time in case a second press follows.
pub fn follow_press(start: (i32, i32)) -> Press {
    let near = |p: (i32, i32)| (p.0 - start.0).abs() <= 3 && (p.1 - start.1).abs() <= 3;
    while button_down() {
        if !near(cursor()) {
            return Press::Drag;
        }
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    let released = std::time::Instant::now();
    while released.elapsed() < double_click_time() {
        if take_double() {
            return Press::Double;
        }
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    Press::Click
}

/// The cursor on screen, in pixels.
pub fn cursor() -> (i32, i32) {
    use windows_sys::Win32::Foundation::POINT;
    use windows_sys::Win32::UI::WindowsAndMessaging::GetCursorPos;
    let mut p = POINT { x: 0, y: 0 };
    // SAFETY: a plain Win32 query.
    unsafe { GetCursorPos(&mut p) };
    (p.x, p.y)
}

/// A maximized window dragged by its bar: back to its size, under the cursor
/// at the same relative place, as Windows does. Returns the window's new place.
pub fn unmaximize_under(window: isize, cursor: (i32, i32)) -> Option<(i32, i32)> {
    let previous = RESTORE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .remove(&window)?;
    let handle = window as HWND;
    let (x, y, w, _) = rect_of(handle);
    let ratio = if w > 0 {
        f64::from(cursor.0 - x) / f64::from(w)
    } else {
        0.5
    };
    let left = cursor.0 - (f64::from(previous.2) * ratio) as i32;
    place(handle, (left, y, previous.2, previous.3));
    Some((left, y))
}
