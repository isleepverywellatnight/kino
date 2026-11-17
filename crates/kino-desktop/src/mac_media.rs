//! macOS « À l'écoute » and the Mac's playback keys: what the player shows
//! (the state both players send each second to the phone remote) appears in
//! Control Centre and on the lock screen, and the keyboard's, AirPods' and
//! Control Centre's keys reach the player through the remote's command queue.
//! Only inside the KINO.app bundle. Runs on the main thread.

use std::collections::HashMap;
use std::ptr::NonNull;
use std::sync::Arc;

use block2::RcBlock;
use kino::nowplaying::{Fields, command_for, fields, needs_update};
use kino::remote::Remote;
use objc2::rc::Retained;
use objc2::runtime::AnyObject;
use objc2::{AllocAnyThread, MainThreadMarker};
use objc2_app_kit::NSImage;
use objc2_foundation::{NSBundle, NSData, NSDictionary, NSNumber, NSString};
use objc2_media_player::{
    MPChangePlaybackPositionCommandEvent, MPMediaItemArtwork, MPMediaItemPropertyArtist,
    MPMediaItemPropertyArtwork, MPMediaItemPropertyPlaybackDuration, MPMediaItemPropertyTitle,
    MPNowPlayingInfoCenter, MPNowPlayingInfoPropertyElapsedPlaybackTime,
    MPNowPlayingInfoPropertyPlaybackRate, MPNowPlayingPlaybackState, MPRemoteCommandCenter,
    MPRemoteCommandEvent, MPRemoteCommandHandlerStatus,
};

const BUNDLE_ID: &str = "com.kino.desktop";
const POSTER_MAX_BYTES: usize = 3 * 1024 * 1024;

pub struct NowPlaying {
    remote: Arc<Remote>,
    shown: Option<Fields>,
    sent_at: f64,
    /// Poster address → its artwork (None while loading or failed).
    artwork: HashMap<String, Option<Retained<MPMediaItemArtwork>>>,
    /// Fetches a poster and hands its bytes back to the main thread.
    fetch: Box<dyn Fn(String)>,
}

fn now() -> f64 {
    kino::db::now()
}

impl NowPlaying {
    /// Inside the KINO.app bundle only; registers the keys.
    pub fn create(
        _main: MainThreadMarker,
        remote: Arc<Remote>,
        fetch: Box<dyn Fn(String)>,
    ) -> Option<Self> {
        if NSBundle::mainBundle().bundleIdentifier()?.to_string() != BUNDLE_ID {
            return None;
        }
        let center = unsafe { MPRemoteCommandCenter::sharedCommandCenter() };
        let commands = unsafe {
            [
                ("toggle", center.togglePlayPauseCommand()),
                ("play", center.playCommand()),
                ("pause", center.pauseCommand()),
                ("next", center.nextTrackCommand()),
                ("previous", center.previousTrackCommand()),
                (
                    "position",
                    Retained::into_super(center.changePlaybackPositionCommand()),
                ),
            ]
        };
        for (kind, command) in commands {
            let remote = remote.clone();
            let handler = RcBlock::new(move |event: NonNull<MPRemoteCommandEvent>| {
                let event: &AnyObject = unsafe { event.as_ref() }.as_ref();
                let position = event
                    .downcast_ref::<MPChangePlaybackPositionCommandEvent>()
                    .map(|e| unsafe { e.positionTime() });
                match command_for(kind, &remote.state(), now(), position) {
                    Some((action, value)) => {
                        remote.push(&serde_json::json!(action), &value);
                        MPRemoteCommandHandlerStatus::Success
                    }
                    None => MPRemoteCommandHandlerStatus::NoActionableNowPlayingItem,
                }
            });
            unsafe {
                command.setEnabled(true);
                // The centre keeps the handler; the token it returns is not needed.
                let _ = command.addTargetWithHandler(&handler);
            }
        }
        Some(Self {
            remote,
            shown: None,
            sent_at: 0.0,
            artwork: HashMap::new(),
            fetch,
        })
    }

    /// Once a second: tell the system when what plays has changed.
    pub fn tick(&mut self) {
        let at = now();
        let current = fields(&self.remote.state(), at);
        if let Some(poster) = current
            .as_ref()
            .map(|f| f.poster.clone())
            .filter(|p| !p.is_empty())
            && !self.artwork.contains_key(&poster)
        {
            if self.artwork.len() >= 20 {
                self.artwork.clear();
            }
            self.artwork.insert(poster.clone(), None);
            (self.fetch)(poster);
        }
        if !needs_update(self.shown.as_ref(), current.as_ref(), at, self.sent_at) {
            return;
        }
        self.shown = current.clone();
        self.sent_at = at;
        let center = unsafe { MPNowPlayingInfoCenter::defaultCenter() };
        let Some(f) = current else {
            unsafe {
                center.setNowPlayingInfo(None);
                center.setPlaybackState(MPNowPlayingPlaybackState::Stopped);
            }
            return;
        };
        let title = NSString::from_str(&f.title);
        let artist = NSString::from_str(&f.artist);
        let (elapsed, rate, duration) = (
            NSNumber::new_f64(f.elapsed),
            NSNumber::new_f64(f.rate),
            NSNumber::new_f64(f.duration),
        );
        let mut keys: Vec<&NSString> = unsafe {
            vec![
                MPMediaItemPropertyTitle,
                MPMediaItemPropertyArtist,
                MPNowPlayingInfoPropertyElapsedPlaybackTime,
                MPNowPlayingInfoPropertyPlaybackRate,
            ]
        };
        let mut values: Vec<&AnyObject> = vec![
            title.as_ref(),
            artist.as_ref(),
            elapsed.as_ref(),
            rate.as_ref(),
        ];
        if f.duration > 0.0 {
            keys.push(unsafe { MPMediaItemPropertyPlaybackDuration });
            values.push(duration.as_ref());
        }
        if let Some(Some(artwork)) = self.artwork.get(&f.poster) {
            keys.push(unsafe { MPMediaItemPropertyArtwork });
            values.push(artwork.as_ref());
        }
        let info = NSDictionary::from_slices(&keys, &values);
        unsafe {
            center.setNowPlayingInfo(Some(&info));
            center.setPlaybackState(if f.rate > 0.0 {
                MPNowPlayingPlaybackState::Playing
            } else {
                MPNowPlayingPlaybackState::Paused
            });
        }
    }

    /// A poster's bytes, back on the main thread: its artwork, shown at the next tick.
    pub fn poster(&mut self, url: String, bytes: Vec<u8>) {
        if bytes.is_empty() || bytes.len() > POSTER_MAX_BYTES {
            return;
        }
        let data = NSData::with_bytes(&bytes);
        let Some(image) = NSImage::initWithData(NSImage::alloc(), &data) else {
            return;
        };
        let size = image.size();
        let handler = RcBlock::new(move |_size| NonNull::from(&*image));
        let artwork = unsafe {
            MPMediaItemArtwork::initWithBoundsSize_requestHandler(
                MPMediaItemArtwork::alloc(),
                size,
                &handler,
            )
        };
        self.artwork.insert(url, Some(artwork));
        self.shown = None;
    }
}
