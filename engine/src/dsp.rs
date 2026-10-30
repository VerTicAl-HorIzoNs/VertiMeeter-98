//! DSP building blocks. Parameters are atomics (written by control thread); state lives on the audio thread.
use crate::params::AF;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering::Relaxed};

pub const CELLS: usize = 9; // 0..5 parametric EQ, 6..8 tone knobs

// ---------------------------------------------------------------- biquad
pub struct BqParams {
    pub on: AtomicBool,
    pub kind: AtomicU32, // 0 peak 1 lowshelf 2 highshelf 3 lowpass 4 highpass 5 notch 6 bandpass
    pub freq: AF,
    pub gain: AF, // dB
    pub q: AF,
}
impl BqParams {
    pub fn new(kind: u32, freq: f32) -> Self {
        BqParams { on: AtomicBool::new(false), kind: AtomicU32::new(kind), freq: AF::new(freq), gain: AF::new(0.0), q: AF::new(0.707) }
    }
}
pub fn default_cells() -> [BqParams; CELLS] {
    [
        BqParams::new(1, 100.0),
        BqParams::new(0, 250.0),
        BqParams::new(0, 1000.0),
        BqParams::new(0, 3000.0),
        BqParams::new(0, 6000.0),
        BqParams::new(2, 10000.0),
        // cells 6..8: the BASS / MID / TREB tone knobs (kept separate from the 6-cell PEQ)
        BqParams::new(1, 120.0),
        BqParams::new(0, 1000.0),
        BqParams::new(2, 8000.0),
    ]
}

#[derive(Clone, Copy, Default)]
pub struct Biquad {
    b0: f32, b1: f32, b2: f32, a1: f32, a2: f32,
    z1: [f32; 2], z2: [f32; 2],
    pub bypass: bool,
}
impl Biquad {
    pub fn set(&mut self, kind: u32, sr: f32, f: f32, gain_db: f32, q: f32) {
        // RBJ cookbook
        let f = f.clamp(10.0, sr * 0.45);
        let q = q.clamp(0.1, 18.0);
        let a = 10f32.powf(gain_db / 40.0);
        let w = 2.0 * std::f32::consts::PI * f / sr;
        let (sw, cw) = (w.sin(), w.cos());
        let al = sw / (2.0 * q);
        let (b0, b1, b2, a0, a1, a2);
        match kind {
            1 => {
                let s = 2.0 * a.sqrt() * al;
                b0 = a * ((a + 1.0) - (a - 1.0) * cw + s);
                b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cw);
                b2 = a * ((a + 1.0) - (a - 1.0) * cw - s);
                a0 = (a + 1.0) + (a - 1.0) * cw + s;
                a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cw);
                a2 = (a + 1.0) + (a - 1.0) * cw - s;
            }
            2 => {
                let s = 2.0 * a.sqrt() * al;
                b0 = a * ((a + 1.0) + (a - 1.0) * cw + s);
                b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cw);
                b2 = a * ((a + 1.0) + (a - 1.0) * cw - s);
                a0 = (a + 1.0) - (a - 1.0) * cw + s;
                a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cw);
                a2 = (a + 1.0) - (a - 1.0) * cw - s;
            }
            3 => {
                b0 = (1.0 - cw) / 2.0; b1 = 1.0 - cw; b2 = (1.0 - cw) / 2.0;
                a0 = 1.0 + al; a1 = -2.0 * cw; a2 = 1.0 - al;
            }
            4 => {
                b0 = (1.0 + cw) / 2.0; b1 = -(1.0 + cw); b2 = (1.0 + cw) / 2.0;
                a0 = 1.0 + al; a1 = -2.0 * cw; a2 = 1.0 - al;
            }
            5 => {
                b0 = 1.0; b1 = -2.0 * cw; b2 = 1.0;
                a0 = 1.0 + al; a1 = -2.0 * cw; a2 = 1.0 - al;
            }
            6 => {
                b0 = al; b1 = 0.0; b2 = -al;
                a0 = 1.0 + al; a1 = -2.0 * cw; a2 = 1.0 - al;
            }
            _ => {
                b0 = 1.0 + al * a; b1 = -2.0 * cw; b2 = 1.0 - al * a;
                a0 = 1.0 + al / a; a1 = -2.0 * cw; a2 = 1.0 - al / a;
            }
        }
        self.b0 = b0 / a0; self.b1 = b1 / a0; self.b2 = b2 / a0;
        self.a1 = a1 / a0; self.a2 = a2 / a0;
    }
    #[inline]
    pub fn run(&mut self, ch: usize, x: f32) -> f32 {
        let y = self.b0 * x + self.z1[ch];
        self.z1[ch] = self.b1 * x - self.a1 * y + self.z2[ch];
        self.z2[ch] = self.b2 * x - self.a2 * y;
        y
    }
}

/// Six-cell EQ whose coefficients are refreshed whenever the control thread bumps `ver`.
pub struct Eq {
    pub cells: [Biquad; CELLS],
    seen: u32,
}
impl Eq {
    pub fn new() -> Self {
        let mut e = Eq { cells: [Biquad::default(); CELLS], seen: u32::MAX };
        for c in e.cells.iter_mut() {
            c.bypass = true;
        }
        e
    }
    pub fn refresh(&mut self, p: &[BqParams; CELLS], ver: u32, sr: f32) {
        if ver == self.seen {
            return;
        }
        self.seen = ver;
        for (c, q) in self.cells.iter_mut().zip(p.iter()) {
            c.bypass = !q.on.load(Relaxed);
            c.set(q.kind.load(Relaxed), sr, q.freq.get(), q.gain.get(), q.q.get());
        }
    }
    pub fn active(&self) -> bool {
        self.cells.iter().any(|c| !c.bypass)
    }
    pub fn process(&mut self, l: &mut [f32], r: &mut [f32]) {
        for c in self.cells.iter_mut().filter(|c| !c.bypass) {
            for i in 0..l.len() {
                l[i] = c.run(0, l[i]);
                r[i] = c.run(1, r[i]);
            }
        }
    }
}

// ---------------------------------------------------------- comp / gate
pub struct CompParams {
    pub on: AtomicBool,
    pub thr: AF,    // dB
    pub ratio: AF,
    pub att: AF,    // ms
    pub rel: AF,    // ms
    pub makeup: AF, // dB
}
impl CompParams {
    pub fn new() -> Self {
        CompParams { on: AtomicBool::new(false), thr: AF::new(-20.0), ratio: AF::new(3.0), att: AF::new(10.0), rel: AF::new(100.0), makeup: AF::new(0.0) }
    }
}
pub struct GateParams {
    pub on: AtomicBool,
    pub thr: AF, // dB
    pub att: AF, // ms
    pub rel: AF, // ms
}
impl GateParams {
    pub fn new() -> Self {
        GateParams { on: AtomicBool::new(false), thr: AF::new(-50.0), att: AF::new(2.0), rel: AF::new(150.0) }
    }
}

#[derive(Default)]
pub struct DynState {
    comp_env: f32,
    gate_env: f32,
    gate_gain: f32,
    pub gr_db: f32, // last compressor gain reduction (for UI)
}

#[inline]
fn coef(ms: f32, sr: f32) -> f32 {
    1.0 - (-1.0 / (0.001 * ms.max(0.05) * sr)).exp()
}
#[inline]
fn to_db(x: f32) -> f32 {
    20.0 * x.max(1e-9).log10()
}

impl DynState {
    pub fn gate(&mut self, p: &GateParams, l: &mut [f32], r: &mut [f32], sr: f32) {
        let thr = p.thr.get();
        let (ca, cr) = (coef(p.att.get(), sr), coef(p.rel.get(), sr));
        let (det, det_rel) = (coef(1.0, sr), coef(40.0, sr));
        for i in 0..l.len() {
            let lv = l[i].abs().max(r[i].abs());
            self.gate_env += (lv - self.gate_env) * if lv > self.gate_env { det } else { det_rel };
            let target = if to_db(self.gate_env) > thr { 1.0 } else { 0.0 };
            self.gate_gain += (target - self.gate_gain) * if target > self.gate_gain { ca } else { cr };
            l[i] *= self.gate_gain;
            r[i] *= self.gate_gain;
        }
    }
    pub fn comp(&mut self, p: &CompParams, l: &mut [f32], r: &mut [f32], sr: f32) {
        let (thr, ratio) = (p.thr.get(), p.ratio.get().max(1.0));
        let (ca, cr) = (coef(p.att.get(), sr), coef(p.rel.get(), sr));
        let mk = 10f32.powf(p.makeup.get() / 20.0);
        let mut worst = 0.0f32;
        for i in 0..l.len() {
            let lv = l[i].abs().max(r[i].abs());
            self.comp_env += (lv - self.comp_env) * if lv > self.comp_env { ca } else { cr };
            let over = to_db(self.comp_env) - thr;
            let gr = if over > 0.0 { over * (1.0 - 1.0 / ratio) } else { 0.0 };
            worst = worst.max(gr);
            let g = 10f32.powf(-gr / 20.0) * mk;
            l[i] *= g;
            r[i] *= g;
        }
        self.gr_db = worst;
    }
}

// ------------------------------------------------------------ 3D / Intellipan
/// Binaural-lite positioner: interaural time + level difference, head-shadow low-pass on the far ear,
/// and a darkening shelf when the source is placed behind the listener. Mono-sums its input.
pub struct Spatial {
    dl: [f32; 128],
    dr: [f32; 128],
    w: usize,
    lp_l: f32,
    lp_r: f32,
    sh_l: f32,
    sh_r: f32,
    az: f32,
    fr: f32,
}
impl Spatial {
    pub fn new() -> Self {
        Spatial { dl: [0.0; 128], dr: [0.0; 128], w: 0, lp_l: 0.0, lp_r: 0.0, sh_l: 0.0, sh_r: 0.0, az: 0.0, fr: 1.0 }
    }
    pub fn process(&mut self, l: &mut [f32], r: &mut [f32], sr: f32, x: f32, y: f32) {
        let n = l.len();
        let (az_t, fr_t) = (x.clamp(-1.0, 1.0), y.clamp(-1.0, 1.0));
        let (az0, fr0) = (self.az, self.fr);
        for i in 0..n {
            let t = (i + 1) as f32 / n as f32;
            let az = az0 + (az_t - az0) * t; // glide across the block: no zipper noise
            let fr = fr0 + (fr_t - fr0) * t;
            let s = (az * std::f32::consts::FRAC_PI_2).sin(); // -1 left .. +1 right
            let itd = 0.00066 * s * sr; // samples; positive: right ear leads
            let (dly_l, dly_r) = (itd.max(0.0), (-itd).max(0.0));
            let m = 0.5 * (l[i] + r[i]);
            self.dl[self.w & 127] = m;
            self.dr[self.w & 127] = m;
            let tap = |buf: &[f32; 128], d: f32, w: usize| {
                let di = d.floor() as usize;
                let fr_ = d - di as f32;
                let a = buf[(w + 128 - di) & 127];
                let b = buf[(w + 128 - di - 1) & 127];
                a + (b - a) * fr_
            };
            let (mut ol, mut or_) = (tap(&self.dl, dly_l, self.w), tap(&self.dr, dly_r, self.w));
            self.w = (self.w + 1) & 127;
            let a = s.abs();
            // head shadow: far ear loses highs and level
            let fc_far = 18000.0 * (1.0 - a) + 2500.0 * a;
            let k_far = 1.0 - (-2.0 * std::f32::consts::PI * fc_far / sr).exp();
            let far_gain = 1.0 - 0.7 * a;
            let near_gain = 1.0 + 0.15 * a;
            if s >= 0.0 {
                self.lp_l += (ol - self.lp_l) * k_far;
                ol = self.lp_l * far_gain;
                or_ *= near_gain;
            } else {
                self.lp_r += (or_ - self.lp_r) * k_far;
                or_ = self.lp_r * far_gain;
                ol *= near_gain;
            }
            // behind the listener: darker (shelf cut above ~3 kHz) and slightly quieter
            let back = ((-fr).max(0.0)).min(1.0);
            if back > 0.001 {
                let kb = 1.0 - (-2.0 * std::f32::consts::PI * 3000.0 / sr).exp();
                self.sh_l += (ol - self.sh_l) * kb;
                self.sh_r += (or_ - self.sh_r) * kb;
                let g = 1.0 - 0.6 * back; // gain of the high part
                ol = self.sh_l + (ol - self.sh_l) * g;
                or_ = self.sh_r + (or_ - self.sh_r) * g;
                ol *= 1.0 - 0.15 * back;
                or_ *= 1.0 - 0.15 * back;
            }
            l[i] = ol;
            r[i] = or_;
        }
        self.az = az_t;
        self.fr = fr_t;
    }
}

// ------------------------------------------------------------ denoiser (RNNoise, pure-Rust port)
const FR: usize = 480; // RNNoise frame: 10 ms at 48 kHz (sample rate must be 48 kHz)
const RING: usize = 16384;
const RM: usize = RING - 1;

struct Chan {
    st: Box<nnnoiseless::DenoiseState<'static>>,
    inb: [f32; FR],
    fill: usize,
    t_in: usize,
    wet: [f32; RING],
    dry: [f32; RING],
}
impl Chan {
    fn new() -> Box<Self> {
        let mut c = Box::new(Chan {
            st: nnnoiseless::DenoiseState::new(),
            inb: [0.0; FR],
            fill: 0,
            t_in: 0,
            wet: [0.0; RING],
            dry: [0.0; RING],
        });
        c.wet.fill(0.0); // touch every page now, not in the callback
        c.dry.fill(0.0);
        c
    }
    fn reset(&mut self) {
        self.fill = 0;
        self.t_in = 0;
        self.wet.fill(0.0);
        self.dry.fill(0.0);
    }
    fn run(&mut self, buf: &mut [f32], amount: f32) {
        let n = buf.len();
        let mut o = [0.0f32; FR];
        for i in 0..n {
            self.dry[(self.t_in + i) & RM] = buf[i];
            self.inb[self.fill] = buf[i] * 32768.0;
            self.fill += 1;
            if self.fill == FR {
                self.st.process_frame(&mut o, &self.inb);
                let start = self.t_in + i + 1 - FR;
                for k in 0..FR {
                    self.wet[(start + k) & RM] = o[k] / 32768.0;
                }
                self.fill = 0;
            }
        }
        self.t_in += n;
        for i in 0..n {
            let t = self.t_in - n + i;
            buf[i] = if t >= FR {
                let j = (t - FR) & RM;
                self.dry[j] * (1.0 - amount) + self.wet[j] * amount
            } else {
                0.0
            };
        }
    }
}

/// Stereo RNNoise with a wet/dry amount. Fixed 10 ms latency; the dry path is delayed to match.
pub struct Denoise {
    ch: [Box<Chan>; 2],
    pub active: bool,
}
impl Denoise {
    pub fn new() -> Self {
        Denoise { ch: [Chan::new(), Chan::new()], active: false }
    }
    pub fn process(&mut self, l: &mut [f32], r: &mut [f32], amount: f32) {
        if !self.active {
            self.ch[0].reset();
            self.ch[1].reset();
            self.active = true;
        }
        self.ch[0].run(l, amount);
        self.ch[1].run(r, amount);
    }
}


/// Stereo-linked peak limiter: instant attack (never exceeds the ceiling), smooth release.
pub struct Limiter {
    g: f32,
}
impl Limiter {
    pub fn new() -> Self {
        Limiter { g: 1.0 }
    }
    #[inline]
    pub fn tick(&mut self, l: &mut f32, r: &mut f32, thr: f32, rel: f32) {
        self.g += (1.0 - self.g) * rel;
        let p = l.abs().max(r.abs());
        if p * self.g > thr {
            self.g = thr / p;
        }
        *l *= self.g;
        *r *= self.g;
    }
    pub fn gr_db(&self) -> f32 {
        20.0 * self.g.max(1e-6).log10()
    }
}
