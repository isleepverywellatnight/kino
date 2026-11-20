//! KINO's look, from the web version's tokens (web/css/design.css `--k-*`):
//! the same colours, type sizes, spacing and radii, so both interfaces match
//! while they live side by side.

// The whole vocabulary, ahead of the screens of G1–G5 that use it.
#![allow(dead_code)]

use gpui::{Hsla, Pixels, Rgba, px, rgb, rgba};

// Colours.
pub fn bg() -> Rgba {
    rgb(0x09090b)
}
pub fn surface_1() -> Rgba {
    rgb(0x131316)
}
pub fn surface_2() -> Rgba {
    rgb(0x1b1b1f)
}
pub fn surface_3() -> Rgba {
    rgb(0x26262b)
}
/// rgba(255, 255, 255, .08)
pub fn line() -> Rgba {
    rgba(0xffffff14)
}
/// rgba(255, 255, 255, .16)
pub fn line_strong() -> Rgba {
    rgba(0xffffff29)
}
/// rgba(255, 255, 255, .55): the focus ring.
pub fn ring() -> Rgba {
    rgba(0xffffff8c)
}
pub fn text() -> Rgba {
    rgb(0xfafafa)
}
pub fn text_2() -> Rgba {
    rgb(0xc7c7cc)
}
pub fn text_3() -> Rgba {
    rgb(0x8e8e96)
}
/// Text on a light surface (the white primary button).
pub fn on_light() -> Rgba {
    rgb(0x0b0b0d)
}
/// The window's close button under the pointer (Windows' own red).
pub fn danger() -> Rgba {
    rgb(0xc42b1c)
}
/// The progress of a film.
pub fn progress() -> Rgba {
    rgb(0xe50914)
}

/// A colour with its alpha replaced, for overlays.
pub fn with_alpha(colour: Rgba, alpha: f32) -> Hsla {
    let mut colour: Hsla = colour.into();
    colour.a = alpha;
    colour
}

// Type sizes (rem at 16 px).
pub const FS_XS: Pixels = px(12.0);
pub const FS_SM: Pixels = px(13.0);
pub const FS_MD: Pixels = px(14.0);
pub const FS_BASE: Pixels = px(15.0);
pub const FS_LG: Pixels = px(18.0);
pub const FS_XL: Pixels = px(21.0);

// Spacing.
pub const SPACE_1: Pixels = px(4.0);
pub const SPACE_2: Pixels = px(8.0);
pub const SPACE_3: Pixels = px(12.0);
pub const SPACE_4: Pixels = px(16.0);
pub const SPACE_5: Pixels = px(24.0);
pub const SPACE_6: Pixels = px(32.0);
pub const SPACE_7: Pixels = px(48.0);

// Radii.
pub const RADIUS_SM: Pixels = px(8.0);
pub const RADIUS: Pixels = px(12.0);
pub const RADIUS_LG: Pixels = px(20.0);

#[cfg(test)]
mod tests {
    use super::*;

    /// The tokens match web/css/design.css, so the two interfaces agree.
    #[test]
    fn colours_match_design_css() {
        let css = std::fs::read_to_string(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../web/css/design.css"
        ))
        .unwrap();
        let hex = |c: Rgba| {
            format!(
                "#{:02x}{:02x}{:02x}",
                (c.r * 255.0).round() as u8,
                (c.g * 255.0).round() as u8,
                (c.b * 255.0).round() as u8
            )
        };
        for (token, colour) in [
            ("--k-bg", bg()),
            ("--k-surface-1", surface_1()),
            ("--k-surface-2", surface_2()),
            ("--k-surface-3", surface_3()),
            ("--k-text", text()),
            ("--k-text-2", text_2()),
            ("--k-text-3", text_3()),
            ("--k-on-light", on_light()),
        ] {
            let line = format!("{token}: {};", hex(colour));
            assert!(css.contains(&line), "design.css has no `{line}`");
        }
        for (token, size) in [("--k-space-4", SPACE_4), ("--k-radius", RADIUS)] {
            let line = format!("{token}: {}px;", f32::from(size));
            assert!(css.contains(&line), "design.css has no `{line}`");
        }
    }
}
