//! KINO's image cache: posters, backdrops and thumbnails, decoded off the main
//! thread and kept within a memory budget, least recently shown out first.
//! An image leaving the cache leaves the GPU's atlas too.
//!
//! Where bytes come from is the `Loader`'s business: the network in G2 (KINO's
//! own HTTP client and disk cache), anything in tests.

use std::collections::{HashMap, VecDeque};
use std::hash::Hash;
use std::sync::Arc;

use gpui::{Context, RenderImage, SharedString};

/// Fetches an image's bytes (JPEG, PNG or WebP) from its key, off the main
/// thread.
pub type Loader = Arc<dyn Fn(&str) -> Result<Vec<u8>, String> + Send + Sync>;

/// Least-recently-used order with a size budget, independent of gpui.
#[derive(Debug)]
pub struct Lru<K> {
    sizes: HashMap<K, usize>,
    /// Oldest first; a key appears once.
    order: VecDeque<K>,
    used: usize,
    budget: usize,
}

impl<K: Clone + Eq + Hash> Lru<K> {
    pub fn new(budget: usize) -> Self {
        Self {
            sizes: HashMap::new(),
            order: VecDeque::new(),
            used: 0,
            budget,
        }
    }

    pub fn used(&self) -> usize {
        self.used
    }

    pub fn len(&self) -> usize {
        self.sizes.len()
    }

    /// `key` was just shown: it becomes the most recent.
    pub fn touch(&mut self, key: &K) {
        if self.sizes.contains_key(key) {
            if let Some(at) = self.order.iter().position(|k| k == key) {
                self.order.remove(at);
            }
            self.order.push_back(key.clone());
        }
    }

    /// Adds `key` of `size` bytes as the most recent; returns the keys evicted
    /// to stay within the budget (never `key` itself, even when it alone is
    /// larger than the budget: the image on screen is always kept).
    #[must_use]
    pub fn insert(&mut self, key: K, size: usize) -> Vec<K> {
        if let Some(old) = self.sizes.insert(key.clone(), size) {
            self.used -= old;
            if let Some(at) = self.order.iter().position(|k| *k == key) {
                self.order.remove(at);
            }
        }
        self.used += size;
        self.order.push_back(key);
        let mut evicted = Vec::new();
        while self.used > self.budget && self.order.len() > 1 {
            if let Some(oldest) = self.order.pop_front() {
                if let Some(size) = self.sizes.remove(&oldest) {
                    self.used -= size;
                }
                evicted.push(oldest);
            }
        }
        evicted
    }
}

enum Entry {
    Loading,
    Ready(Arc<RenderImage>),
    Failed,
}

/// The cache, an entity the screens share: `get` returns an image once it is
/// decoded and asks for it the first time.
pub struct ImageCache {
    entries: HashMap<SharedString, Entry>,
    lru: Lru<SharedString>,
    loader: Loader,
}

/// 256 MB of decoded images: some 1,500 posters at 171×256.
pub const DEFAULT_BUDGET: usize = 256 * 1024 * 1024;

impl ImageCache {
    pub fn new(loader: Loader, budget: usize) -> Self {
        Self {
            entries: HashMap::new(),
            lru: Lru::new(budget),
            loader,
        }
    }

    /// Decoded bytes held now.
    pub fn used(&self) -> usize {
        self.lru.used()
    }

    /// Images held now.
    pub fn len(&self) -> usize {
        self.lru.len()
    }

    /// The image for `key` if it is ready; otherwise it is requested once (a
    /// failed one is not retried) and the caller draws a placeholder.
    pub fn get(&mut self, key: &SharedString, cx: &mut Context<Self>) -> Option<Arc<RenderImage>> {
        match self.entries.get(key) {
            Some(Entry::Ready(image)) => {
                let image = image.clone();
                self.lru.touch(key);
                Some(image)
            }
            Some(Entry::Loading | Entry::Failed) => None,
            None => {
                self.entries.insert(key.clone(), Entry::Loading);
                self.load(key.clone(), cx);
                None
            }
        }
    }

    fn load(&mut self, key: SharedString, cx: &mut Context<Self>) {
        let loader = self.loader.clone();
        let source = key.clone();
        let task = cx
            .background_executor()
            .spawn(async move { loader(&source).and_then(|bytes| decode(&bytes)) });
        cx.spawn(async move |cache, cx| {
            let decoded = task.await;
            let _ = cache.update(cx, |cache, cx| {
                match decoded {
                    Ok((image, size)) => {
                        for old in cache.lru.insert(key.clone(), size) {
                            if let Some(Entry::Ready(image)) = cache.entries.remove(&old) {
                                // Out of the cache, out of the GPU's atlas.
                                cx.drop_image(image, None);
                            }
                        }
                        cache.entries.insert(key, Entry::Ready(Arc::new(image)));
                    }
                    Err(_) => {
                        cache.entries.insert(key, Entry::Failed);
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }
}

/// Bytes to an image gpui draws (BGRA), and its size in memory.
fn decode(bytes: &[u8]) -> Result<(RenderImage, usize), String> {
    let mut pixels = image::load_from_memory(bytes)
        .map_err(|e| format!("cannot decode image: {e}"))?
        .into_rgba8();
    // gpui's images are BGRA.
    for pixel in pixels.chunks_exact_mut(4) {
        pixel.swap(0, 2);
    }
    let size = pixels.as_raw().len();
    Ok((RenderImage::new(vec![image::Frame::new(pixels)]), size))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_least_recently_shown_leaves_first() {
        let mut lru = Lru::new(30);
        assert!(lru.insert("a", 10).is_empty());
        assert!(lru.insert("b", 10).is_empty());
        assert!(lru.insert("c", 10).is_empty());
        lru.touch(&"a");
        assert_eq!(lru.insert("d", 10), vec!["b"]);
        assert_eq!(lru.used(), 30);
        assert_eq!(lru.len(), 3);
    }

    #[test]
    fn one_large_image_evicts_as_many_as_needed() {
        let mut lru = Lru::new(30);
        for key in ["a", "b", "c"] {
            let _ = lru.insert(key, 10);
        }
        assert_eq!(lru.insert("big", 25), vec!["a", "b", "c"]);
        assert_eq!(lru.used(), 25);
    }

    #[test]
    fn an_image_larger_than_the_budget_is_still_kept() {
        let mut lru = Lru::new(10);
        assert!(lru.insert("huge", 50).is_empty());
        assert_eq!(lru.len(), 1);
    }

    #[test]
    fn inserting_again_replaces_the_size() {
        let mut lru = Lru::new(100);
        let _ = lru.insert("a", 10);
        let _ = lru.insert("a", 40);
        assert_eq!((lru.used(), lru.len()), (40, 1));
    }

    #[test]
    fn decoding_gives_bgra_pixels() {
        // A 1×1 red PNG.
        let mut png = Vec::new();
        image::RgbaImage::from_pixel(1, 1, image::Rgba([255, 0, 0, 255]))
            .write_to(&mut std::io::Cursor::new(&mut png), image::ImageFormat::Png)
            .unwrap();
        let (image, size) = decode(&png).unwrap();
        assert_eq!(size, 4);
        assert_eq!(image.as_bytes(0).unwrap(), &[0, 0, 255, 255]);
        assert!(decode(b"not an image").is_err());
    }
}
