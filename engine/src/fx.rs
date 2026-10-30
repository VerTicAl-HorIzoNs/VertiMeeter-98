//! Special FX: stereo reverb (Freeverb topology) and an 8-tap delay. All buffers preallocated.
use crate::params::AF;
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};

pub const TAPS: usize = 8;
const MAX_DELAY_S: f32 = 2.0;

pub struct FxParams {
    pub rvb_on: AtomicBool,
    pub room: AF,  // 0..1
    pub damp: AF,  // 0..1
    pub width: AF, // 0..1
    pub dly_on: AtomicBool,
    pub fb: AF, // 0..0.95
    pub tap_on: [AtomicBool; TAPS],
    pub tap_ms: [AF; TAPS],
    pub tap_gain: [AF; TAPS], // linear
    pub tap_pan: [AF; TAPS],  // -1..1
}
impl FxParams {
    pub fn new() -> Self {
        FxParams {
            rvb_on: AtomicBool::new(true),
            room: AF::new(0.5),
            damp: AF::new(0.5),
            width: AF::new(1.0),
            dly_on: AtomicBool::new(true),
            fb: AF::new(0.3),
            tap_on: std::array::from_fn(|i| AtomicBool::new(i < 2)),
            tap_ms: std::array::from_fn(|i| AF::new(120.0 * (i as f32 + 1.0))),
            tap_gain: std::array::from_fn(|i| AF::new(0.6 / (i as f32 + 1.0))),
            tap_pan: std::array::from_fn(|i| AF::new(if i % 2 == 0 { -0.5 } else { 0.5 })),
        }
    }
}

struct Comb {
    buf: Vec<f32>,
    idx: usize,
    store: f32,
}
impl Comb {
    fn new(n: usize) -> Self {
        Comb { buf: crate::params::zeros(n.max(1)), idx: 0, store: 0.0 }
    }
    #[inline]
    fn clear(&mut self) {
        self.buf.fill(0.0);
        self.store = 0.0;
    }
    #[inline]
    fn run(&mut self, x: f32, fb: f32, damp: f32) -> f32 {
        let out = self.buf[self.idx];
        self.store = out * (1.0 - damp) + self.store * damp;
        self.buf[self.idx] = x + self.store * fb;
        self.idx += 1;
        if self.idx == self.buf.len() {
            self.idx = 0;
        }
        out
    }
}
struct Allpass {
    buf: Vec<f32>,
    idx: usize,
}
impl Allpass {
    fn new(n: usize) -> Self {
        Allpass { buf: crate::params::zeros(n.max(1)), idx: 0 }
    }
    fn clear(&mut self) {
        self.buf.fill(0.0);
    }
    #[inline]
    fn run(&mut self, x: f32) -> f32 {
        let b = self.buf[self.idx];
        let out = b - x;
        self.buf[self.idx] = x + b * 0.5;
        self.idx += 1;
        if self.idx == self.buf.len() {
            self.idx = 0;
        }
        out
    }
}

pub struct Reverb {
    cl: Vec<Comb>,
    cr: Vec<Comb>,
    al: Vec<Allpass>,
    ar: Vec<Allpass>,
}
impl Reverb {
    pub fn new(sr: f32) -> Self {
        let k = sr / 44100.0;
        let sc = |n: usize| ((n as f32) * k) as usize;
        let combs = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617];
        let aps = [556, 441, 341, 225];
        Reverb {
            cl: combs.iter().map(|&n| Comb::new(sc(n))).collect(),
            cr: combs.iter().map(|&n| Comb::new(sc(n + 23))).collect(),
            al: aps.iter().map(|&n| Allpass::new(sc(n))).collect(),
            ar: aps.iter().map(|&n| Allpass::new(sc(n + 23))).collect(),
        }
    }
    /// Silence the whole tail (used when a bus stops being fed, so old audio never replays later).
    pub fn clear(&mut self) {
        self.cl.iter_mut().chain(self.cr.iter_mut()).for_each(|c| c.clear());
        self.al.iter_mut().chain(self.ar.iter_mut()).for_each(|a| a.clear());
    }
    pub fn process(&mut self, p: &FxParams, inl: &[f32], inr: &[f32], ol: &mut [f32], or_: &mut [f32]) {
        let fb = p.room.get() * 0.28 + 0.7;
        let damp = p.damp.get() * 0.4;
        let w = p.width.get();
        let (w1, w2) = (0.5 * (1.0 + w), 0.5 * (1.0 - w));
        for i in 0..inl.len() {
            let x = (inl[i] + inr[i]) * 0.015;
            let (mut l, mut r) = (0.0, 0.0);
            for c in self.cl.iter_mut() {
                l += c.run(x, fb, damp);
            }
            for c in self.cr.iter_mut() {
                r += c.run(x, fb, damp);
            }
            for a in self.al.iter_mut() {
                l = a.run(l);
            }
            for a in self.ar.iter_mut() {
                r = a.run(r);
            }
            ol[i] = (l * w1 + r * w2) * 1.5;
            or_[i] = (r * w1 + l * w2) * 1.5;
        }
    }
}

pub struct MultiTap {
    bl: Vec<f32>,
    br: Vec<f32>,
    w: usize,
    sr: f32,
}
impl MultiTap {
    pub fn new(sr: f32) -> Self {
        let n = (sr * MAX_DELAY_S) as usize + 2;
        MultiTap { bl: crate::params::zeros(n), br: crate::params::zeros(n), w: 0, sr }
    }
    pub fn clear(&mut self) {
        self.bl.fill(0.0);
        self.br.fill(0.0);
        self.w = 0;
    }
    pub fn process(&mut self, p: &FxParams, inl: &[f32], inr: &[f32], ol: &mut [f32], or_: &mut [f32]) {
        let n = self.bl.len();
        let fb = p.fb.get().clamp(0.0, 0.95);
        let mut off = [0usize; TAPS];
        let mut gl = [0.0f32; TAPS];
        let mut gr = [0.0f32; TAPS];
        for t in 0..TAPS {
            if p.tap_on[t].load(Relaxed) {
                off[t] = ((p.tap_ms[t].get() * 0.001 * self.sr) as usize).clamp(1, n - 1);
                let a = (p.tap_pan[t].get().clamp(-1.0, 1.0) + 1.0) * std::f32::consts::FRAC_PI_4;
                gl[t] = p.tap_gain[t].get() * a.cos() * std::f32::consts::SQRT_2;
                gr[t] = p.tap_gain[t].get() * a.sin() * std::f32::consts::SQRT_2;
            }
        }
        for i in 0..inl.len() {
            let (mut l, mut r) = (0.0f32, 0.0f32);
            for t in 0..TAPS {
                if off[t] != 0 {
                    let rp = (self.w + n - off[t]) % n;
                    l += self.bl[rp] * gl[t];
                    r += self.br[rp] * gr[t];
                }
            }
            self.bl[self.w] = inl[i] + l * fb;
            self.br[self.w] = inr[i] + r * fb;
            self.w += 1;
            if self.w == n {
                self.w = 0;
            }
            ol[i] = l;
            or_[i] = r;
        }
    }
}
