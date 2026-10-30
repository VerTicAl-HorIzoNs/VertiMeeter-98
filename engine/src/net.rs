//! Shared network plumbing: the lock-free stereo ring between a network thread and the mixer, and the worker handle.
//! The VBAN / OBAN protocol code lives in oban.rs. Addresses and ports only ever come from the control socket.
use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::*};
use std::sync::Arc;

pub const RING_FRAMES: usize = 65536; // power of two, ~1.4 s at 48 kHz

/// Single-producer single-consumer stereo ring of f32 frames.
pub struct Ring {
    l: UnsafeCell<Vec<f32>>,
    r: UnsafeCell<Vec<f32>>,
    head: AtomicUsize, // total frames written
    tail: AtomicUsize, // total frames read
}
unsafe impl Sync for Ring {}
unsafe impl Send for Ring {}
impl Ring {
    pub fn new() -> Self {
        Ring {
            l: UnsafeCell::new(crate::params::zeros(RING_FRAMES)),
            r: UnsafeCell::new(crate::params::zeros(RING_FRAMES)),
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
        }
    }
    pub fn len(&self) -> usize {
        self.head.load(Acquire).wrapping_sub(self.tail.load(Acquire))
    }
    /// producer: returns frames accepted (drops the rest if full)
    pub fn push(&self, l: &[f32], r: &[f32]) -> usize {
        let h = self.head.load(Relaxed);
        let free = RING_FRAMES - h.wrapping_sub(self.tail.load(Acquire));
        let n = l.len().min(r.len()).min(free);
        let (bl, br) = unsafe { (&mut *self.l.get(), &mut *self.r.get()) };
        for i in 0..n {
            bl[(h + i) & (RING_FRAMES - 1)] = l[i];
            br[(h + i) & (RING_FRAMES - 1)] = r[i];
        }
        self.head.store(h.wrapping_add(n), Release);
        n
    }
    /// consumer: pops up to out.len() frames, returns how many
    pub fn pop(&self, l: &mut [f32], r: &mut [f32]) -> usize {
        let t = self.tail.load(Relaxed);
        let avail = self.head.load(Acquire).wrapping_sub(t);
        let n = l.len().min(r.len()).min(avail);
        let (bl, br) = unsafe { (&*self.l.get(), &*self.r.get()) };
        for i in 0..n {
            l[i] = bl[(t + i) & (RING_FRAMES - 1)];
            r[i] = br[(t + i) & (RING_FRAMES - 1)];
        }
        self.tail.store(t.wrapping_add(n), Release);
        n
    }
    /// consumer: discard frames (keeps latency bounded)
    pub fn skip(&self, n: usize) {
        let t = self.tail.load(Relaxed);
        let avail = self.head.load(Acquire).wrapping_sub(t);
        self.tail.store(t.wrapping_add(n.min(avail)), Release);
    }
}

/// Handle to a worker thread. Dropping it stops the thread and waits for it, so the UDP port is
/// free again before anything restarts (sockets time out every few ms to notice the flag).
pub struct Stop(Arc<AtomicBool>, Option<std::thread::JoinHandle<()>>);
impl Stop {
    pub fn new(flag: Arc<AtomicBool>, h: std::thread::JoinHandle<()>) -> Self {
        Stop(flag, Some(h))
    }
}
impl Drop for Stop {
    fn drop(&mut self) {
        self.0.store(true, Relaxed);
        if let Some(h) = self.1.take() {
            let _ = h.join();
        }
    }
}
