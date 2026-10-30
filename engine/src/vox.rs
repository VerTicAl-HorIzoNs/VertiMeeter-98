//! Voice modeler (pitch shift + ring modulator) and the C5 multiband compressor.
//! Parameters are atomics written by the control thread; state lives on the audio thread (no locks, no allocation).
use crate::dsp::Biquad;
use crate::params::{zeros, AF};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering::Relaxed};

// ---------------------------------------------------------------- voice modeler
pub struct VoiceParams {
    pub on: AtomicBool,
    pub pitch: AF, // semitones, -12 .. +12
    pub mix: AF,   // 0 dry .. 1 fully modelled
    pub ring: AF,  // ring modulator Hz (0 = off): the classic robot voice
}
impl VoiceParams {
    pub fn new() -> Self {
        VoiceParams { on: AtomicBool::new(false), pitch: AF::new(0.0), mix: AF::new(1.0), ring: AF::new(0.0) }
    }
}

const VW: usize = 2048; // pitch window in samples (~43 ms at 48 kHz): latency is about half of it
const VB: usize = 8192; // delay buffer (power of two)
const TAU: f32 = std::f32::consts::TAU;

/// Granular delay-line pitch shifter: two taps a half window apart read the input at a different speed, each
/// faded in and out with a Hann window so the jump back is inaudible. Followed by an optional ring modulator.
pub struct Voice {
    buf: [Vec<f32>; 2],
    w: usize,
    ph: f32,
    rph: f32,
}
impl Voice {
    pub fn new() -> Self {
        Voice { buf: [zeros(VB), zeros(VB)], w: 0, ph: 0.0, rph: 0.0 }
    }
    #[inline]
    fn tap(&self, ch: usize, d: f32) -> f32 {
        let pos = self.w as f32 + VB as f32 - d;
        let i0 = pos.floor();
        let fr = pos - i0;
        let i0 = i0 as usize;
        let a = self.buf[ch][i0 & (VB - 1)];
        let b = self.buf[ch][(i0 + 1) & (VB - 1)];
        a + (b - a) * fr
    }
    pub fn process(&mut self, p: &VoiceParams, l: &mut [f32], r: &mut [f32], sr: f32) {
        let pitch = p.pitch.get().clamp(-12.0, 12.0);
        let shifting = pitch.abs() > 0.01;
        let ratio = 2f32.powf(pitch / 12.0);
        let dph = (1.0 - ratio) / VW as f32;
        let mix = p.mix.get().clamp(0.0, 1.0);
        let ring = p.ring.get().clamp(0.0, 2000.0);
        let rstep = ring / sr;
        for i in 0..l.len() {
            let (dl, dr) = (l[i], r[i]);
            let (mut yl, mut yr) = (dl, dr);
            if shifting {
                self.buf[0][self.w & (VB - 1)] = dl;
                self.buf[1][self.w & (VB - 1)] = dr;
                let p1 = self.ph;
                let p2 = (self.ph + 0.5).fract();
                let (w1, w2) = (0.5 - 0.5 * (TAU * p1).cos(), 0.5 - 0.5 * (TAU * p2).cos());
                let (d1, d2) = (p1 * VW as f32 + 1.0, p2 * VW as f32 + 1.0);
                yl = self.tap(0, d1) * w1 + self.tap(0, d2) * w2;
                yr = self.tap(1, d1) * w1 + self.tap(1, d2) * w2;
                self.ph = (self.ph + dph).rem_euclid(1.0);
                self.w = (self.w + 1) & (VB - 1);
            }
            if ring > 0.5 {
                let m = (TAU * self.rph).sin();
                self.rph = (self.rph + rstep).fract();
                yl *= m;
                yr *= m;
            }
            l[i] = dl + (yl - dl) * mix;
            r[i] = dr + (yr - dr) * mix;
        }
    }
}

// ---------------------------------------------------------------- C5 multiband compressor
pub const BANDS: usize = 5;

pub struct C5Params {
    pub on: AtomicBool,
    pub ver: AtomicU32,
    pub xo: [AF; BANDS - 1], // crossover frequencies, ascending
    pub thr: [AF; BANDS],    // dB
    pub ratio: [AF; BANDS],
    pub gain: [AF; BANDS],   // makeup dB
}
impl C5Params {
    pub fn new() -> Self {
        let xo = [120.0, 500.0, 2000.0, 6000.0];
        C5Params {
            on: AtomicBool::new(false),
            ver: AtomicU32::new(0),
            xo: std::array::from_fn(|i| AF::new(xo[i])),
            thr: std::array::from_fn(|_| AF::new(-20.0)),
            ratio: std::array::from_fn(|_| AF::new(3.0)),
            gain: std::array::from_fn(|_| AF::new(0.0)),
        }
    }
}

type Xo = [[Biquad; 2]; 2]; // [0] = LR4 low-pass (two Butterworth biquads), [1] = LR4 high-pass

#[inline]
fn xo_split(x: &mut Xo, ch: usize, v: f32) -> (f32, f32) {
    let [lp, hp] = x;
    let lo = lp[0].run(ch, v);
    let hi = hp[0].run(ch, v);
    (lp[1].run(ch, lo), hp[1].run(ch, hi))
}

/// Five bands from four Linkwitz-Riley (LR4) crossovers applied one after another: each splits what is left into
/// low and high. A low band is then run through the all-pass (low + high) of every crossover above it, so with all
/// compressors idle the bands add back to a flat response. Each band has its own peak compressor
/// (10 ms attack, 120 ms release).
pub struct C5 {
    xo: [Xo; BANDS - 1],
    comp: [[Xo; BANDS - 1]; BANDS - 2], // comp[j][k]: all-pass of crossover k applied to band j (j < k)
    env: [f32; BANDS],
    seen: u32,
}
impl C5 {
    pub fn new() -> Self {
        C5 {
            xo: [[[Biquad::default(); 2]; 2]; BANDS - 1],
            comp: [[[[Biquad::default(); 2]; 2]; BANDS - 1]; BANDS - 2],
            env: [0.0; BANDS],
            seen: u32::MAX,
        }
    }
    fn setup(x: &mut Xo, sr: f32, f: f32) {
        for j in 0..2 {
            x[0][j].set(3, sr, f, 0.0, 0.70710678);
            x[1][j].set(4, sr, f, 0.0, 0.70710678);
        }
    }
    pub fn process(&mut self, p: &C5Params, l: &mut [f32], r: &mut [f32], sr: f32) {
        let ver = p.ver.load(Relaxed);
        if ver != self.seen {
            self.seen = ver;
            for k in 0..BANDS - 1 {
                let f = p.xo[k].get();
                Self::setup(&mut self.xo[k], sr, f);
                for j in 0..BANDS - 2 {
                    if j < k {
                        Self::setup(&mut self.comp[j][k], sr, f);
                    }
                }
            }
        }
        let ca = 1.0 - (-1.0 / (0.010 * sr)).exp();
        let cr = 1.0 - (-1.0 / (0.120 * sr)).exp();
        let mut thr = [0.0f32; BANDS];
        let mut ratio = [1.0f32; BANDS];
        let mut mk = [1.0f32; BANDS];
        for b in 0..BANDS {
            thr[b] = p.thr[b].get();
            ratio[b] = p.ratio[b].get().max(1.0);
            mk[b] = 10f32.powf(p.gain[b].get() / 20.0);
        }
        for i in 0..l.len() {
            let mut rem = [l[i], r[i]];
            let mut bands = [[0.0f32; 2]; BANDS];
            for k in 0..BANDS - 1 {
                for ch in 0..2 {
                    let (lo, hi) = xo_split(&mut self.xo[k], ch, rem[ch]);
                    bands[k][ch] = lo;
                    rem[ch] = hi;
                }
            }
            bands[BANDS - 1] = rem;
            for j in 0..BANDS - 2 {
                for k in (j + 1)..BANDS - 1 {
                    for ch in 0..2 {
                        let (lo, hi) = xo_split(&mut self.comp[j][k], ch, bands[j][ch]);
                        bands[j][ch] = lo + hi;
                    }
                }
            }
            let mut out = [0.0f32; 2];
            for b in 0..BANDS {
                let lv = bands[b][0].abs().max(bands[b][1].abs());
                self.env[b] += (lv - self.env[b]) * if lv > self.env[b] { ca } else { cr };
                let db = 20.0 * self.env[b].max(1e-9).log10();
                let over = db - thr[b];
                let g = if over > 0.0 { 10f32.powf(-(over * (1.0 - 1.0 / ratio[b])) / 20.0) } else { 1.0 } * mk[b];
                out[0] += bands[b][0] * g;
                out[1] += bands[b][1] * g;
            }
            l[i] = out[0];
            r[i] = out[1];
        }
    }
}
