//! Windows: mpv inside KINO's window. Its own window becomes a child covering
//! the client area, follows KINO's size, and is let go when mpv closes.

use windows_sys::Win32::Foundation::{HWND, LPARAM, RECT};
use windows_sys::Win32::Graphics::Gdi::{InvalidateRect, UpdateWindow};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::SetFocus;
use windows_sys::Win32::UI::WindowsAndMessaging::{
    BringWindowToTop, EnumWindows, GWL_STYLE, GetClientRect, GetWindowLongW,
    GetWindowThreadProcessId, IsWindowVisible, SWP_FRAMECHANGED, SWP_SHOWWINDOW, SetParent,
    SetWindowLongW, SetWindowPos, WS_CHILD, WS_CLIPCHILDREN, WS_CLIPSIBLINGS, WS_POPUP, WS_VISIBLE,
};

struct Search {
    pid: u32,
    found: HWND,
}

unsafe extern "system" fn visit(window: HWND, data: LPARAM) -> i32 {
    // SAFETY: `data` is the Search passed to EnumWindows below.
    let search = unsafe { &mut *(data as *mut Search) };
    let mut pid = 0u32;
    // SAFETY: a window handle given by EnumWindows.
    unsafe { GetWindowThreadProcessId(window, &mut pid) };
    if pid == search.pid && unsafe { IsWindowVisible(window) } != 0 {
        search.found = window;
        return 0;
    }
    1
}

/// mpv's visible window, waited for up to about two seconds.
pub fn find(pid: u32) -> Option<isize> {
    for _ in 0..60 {
        std::thread::sleep(std::time::Duration::from_millis(30));
        let mut search = Search {
            pid,
            found: std::ptr::null_mut(),
        };
        // SAFETY: the callback only reads the Search it is given.
        unsafe { EnumWindows(Some(visit), &mut search as *mut Search as LPARAM) };
        if !search.found.is_null() {
            return Some(search.found as isize);
        }
    }
    None
}

fn client(parent: HWND) -> RECT {
    let mut rect = RECT {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    // SAFETY: KINO's own window.
    unsafe { GetClientRect(parent, &mut rect) };
    rect
}

/// mpv's window as a child of KINO's, over its whole client area.
pub fn dock(parent: isize, player: isize) {
    let (parent, player) = (parent as HWND, player as HWND);
    // SAFETY: two live windows; styles changed as Win32 documents for SetParent.
    unsafe {
        // KINO's window does not paint over its child.
        SetWindowLongW(
            parent,
            GWL_STYLE,
            GetWindowLongW(parent, GWL_STYLE) | WS_CLIPCHILDREN as i32,
        );
        let style = (GetWindowLongW(player, GWL_STYLE) as u32 & !WS_POPUP)
            | WS_CHILD
            | WS_CLIPSIBLINGS
            | WS_VISIBLE;
        SetWindowLongW(player, GWL_STYLE, style as i32);
        SetParent(player, parent);
    }
    fit(parent as isize, player as isize);
    // SAFETY: the docked player's window.
    unsafe {
        BringWindowToTop(player);
        SetFocus(player);
    }
}

/// The docked player over KINO's client area, after a resize.
pub fn fit(parent: isize, player: isize) {
    let rect = client(parent as HWND);
    // SAFETY: the docked player's window, sized to its parent's client area.
    unsafe {
        SetWindowPos(
            player as HWND,
            std::ptr::null_mut(),
            0,
            0,
            rect.right,
            rect.bottom,
            SWP_SHOWWINDOW | SWP_FRAMECHANGED,
        )
    };
}

/// KINO's window repainted once the player is gone.
pub fn refresh(parent: isize) {
    let parent = parent as HWND;
    // SAFETY: KINO's own window.
    unsafe {
        InvalidateRect(parent, std::ptr::null(), 1);
        UpdateWindow(parent);
        SetFocus(parent);
    }
}
