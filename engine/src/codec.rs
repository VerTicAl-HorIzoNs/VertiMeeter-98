//! Codecs and resampling for the network code (OBAN / VBAN).
//! Worker threads only: nothing in here ever runs in the JACK callback.
//! Every decoder is bounds-checked (the release build aborts on panic, so a hostile packet must never panic).
use std::f64::consts::PI;
use std::os::raw::{c_int, c_void};
use std::sync::OnceLock;

// =============================================================== resampler (windowed sinc)
const TAB_RES: usize = 512;
const HW_UNITS: usize = 12;

fn table() -> &'static [f32] {
    static T: OnceLock<Vec<f32>> = OnceLock::new();
    T.get_or_init(|| {
        (0..HW_UNITS * TAB_RES + 2)
            .map(|i| {
                let s = i as f64 / TAB_RES as f64;
                let x = 0.95 * s;
                let sinc = if x.abs() < 1e-9 { 1.0 } else { (PI * x).sin() / (PI * x) };
                let t = (s / HW_UNITS as f64).min(1.0);
                let w = 0.42 + 0.5 * (PI * t).cos() + 0.08 * (2.0 * PI * t).cos();
                (sinc * w) as f32
            })
            .collect()
    })
}

pub struct Resampler {
    step: f64,
    m: f64,
    hw: f64,
    bl: Vec<f32>,
    br: Vec<f32>,
    pos: f64,
    pass: bool,
    tab: &'static [f32],
}

impl Resampler {
    pub fn new(in_sr: u32, out_sr: u32) -> Self {
        let step = in_sr.max(1) as f64 / out_sr.max(1) as f64;
        let m = step.max(1.0);
        let hw = HW_UNITS as f64 * m;
        let pad = hw.ceil() as usize + 2;
        Resampler { step, m, hw, bl: vec![0.0; pad], br: vec![0.0; pad], pos: pad as f64, pass: in_sr == out_sr, tab: table() }
    }

    #[inline]
    fn kern(&self, s: f64) -> f32 {
        let x = s * TAB_RES as f64;
        let i = x as usize;
        if i + 1 >= self.tab.len() {
            return 0.0;
        }
        let f = (x - i as f64) as f32;
        self.tab[i] + (self.tab[i + 1] - self.tab[i]) * f
    }

    /// Append the converted audio to ol / or_.
    pub fn process(&mut self, l: &[f32], r: &[f32], ol: &mut Vec<f32>, or_: &mut Vec<f32>) {
        if self.pass {
            ol.extend_from_slice(l);
            or_.extend_from_slice(r);
            return;
        }
        self.bl.extend_from_slice(l);
        self.br.extend_from_slice(r);
        let len = self.bl.len() as f64;
        while self.pos + self.hw + 1.0 < len {
            let lo = (self.pos - self.hw).ceil().max(0.0) as usize;
            let hi = ((self.pos + self.hw).floor() as usize).min(self.bl.len() - 1);
            let (mut al, mut ar, mut ws) = (0f32, 0f32, 0f32);
            for i in lo..=hi {
                let w = self.kern((i as f64 - self.pos).abs() / self.m);
                al += self.bl[i] * w;
                ar += self.br[i] * w;
                ws += w;
            }
            if ws.abs() > 1e-6 {
                al /= ws;
                ar /= ws;
            }
            ol.push(al);
            or_.push(ar);
            self.pos += self.step;
        }
        let drop = ((self.pos - self.hw).floor().max(0.0) as usize).saturating_sub(2).min(self.bl.len());
        if drop > 0 {
            self.bl.drain(..drop);
            self.br.drain(..drop);
            self.pos -= drop as f64;
        }
    }
}

// =============================================================== G.711 mu-law
pub fn mulaw_enc(s: i16) -> u8 {
    const BIAS: i32 = 0x84;
    const CLIP: i32 = 32635;
    let mut x = s as i32;
    let sign = if x < 0 {
        x = -x;
        0x80
    } else {
        0
    };
    if x > CLIP {
        x = CLIP;
    }
    x += BIAS;
    let mut exp = 7;
    let mut mask = 0x4000;
    while exp > 0 && (x & mask) == 0 {
        exp -= 1;
        mask >>= 1;
    }
    let man = (x >> (exp + 3)) & 0x0F;
    !((sign | (exp << 4) | man) as u8)
}

pub fn mulaw_dec(u: u8) -> i16 {
    let u = !u as i32;
    let sign = u & 0x80;
    let exp = (u >> 4) & 7;
    let man = u & 0x0F;
    let t = ((man << 3) + 0x84) << exp;
    (if sign != 0 { 0x84 - t } else { t - 0x84 }) as i16
}

// =============================================================== VFLAC / VERTIFLAC (lossless, FLAC-style, one self-contained frame per datagram)
// frame: mode byte (0 independent, 1 mid/side, 2 raw), then per channel: [order<<5 | rice k], order warm-up samples
// (i32 LE), Rice-coded residuals of a fixed polynomial predictor (order 0..3), padded to a byte.
pub const LL_MAX: usize = 1400;

struct BitW {
    out: Vec<u8>,
    acc: u64,
    n: u32,
}
impl BitW {
    fn new() -> Self {
        BitW { out: Vec::new(), acc: 0, n: 0 }
    }
    fn put(&mut self, v: u32, bits: u32) {
        if bits == 0 {
            return;
        }
        self.acc = (self.acc << bits) | (v as u64 & ((1u64 << bits) - 1));
        self.n += bits;
        while self.n >= 8 {
            self.n -= 8;
            self.out.push((self.acc >> self.n) as u8);
        }
        self.acc &= (1u64 << self.n) - 1;
    }
    fn rice(&mut self, u: u32, k: u32) -> bool {
        let q = u >> k;
        if q > 3000 || self.out.len() > LL_MAX {
            return false;
        }
        for _ in 0..q {
            self.put(0, 1);
        }
        self.put(1, 1);
        self.put(u & ((1u32 << k) - 1), k);
        true
    }
    fn finish(mut self) -> Vec<u8> {
        if self.n > 0 {
            let pad = 8 - self.n;
            self.put(0, pad);
        }
        self.out
    }
}

struct BitR<'a> {
    d: &'a [u8],
    pos: usize,
}
impl<'a> BitR<'a> {
    fn bit(&mut self) -> Option<u32> {
        let byte = *self.d.get(self.pos >> 3)?;
        let b = (byte >> (7 - (self.pos & 7))) & 1;
        self.pos += 1;
        Some(b as u32)
    }
    fn bits(&mut self, n: u32) -> Option<u32> {
        let mut v = 0u32;
        for _ in 0..n {
            v = (v << 1) | self.bit()?;
        }
        Some(v)
    }
    fn rice(&mut self, k: u32) -> Option<u64> {
        let mut q = 0u64;
        while self.bit()? == 0 {
            q += 1;
            if q > 3000 {
                return None;
            }
        }
        let low = self.bits(k)? as u64;
        Some((q << k) | low)
    }
    fn bytes_used(&self) -> usize {
        (self.pos + 7) >> 3
    }
}

fn resid(x: &[i64], p: usize, i: usize) -> i64 {
    match p {
        0 => x[i],
        1 => x[i] - x[i - 1],
        2 => x[i] - 2 * x[i - 1] + x[i - 2],
        _ => x[i] - 3 * x[i - 1] + 3 * x[i - 2] - x[i - 3],
    }
}

fn enc_chan(x: &[i64], w: &mut Vec<u8>) -> bool {
    let n = x.len();
    let mut best_p = 0usize;
    let mut best_sum = u64::MAX;
    for p in 0..=3usize {
        if p > n {
            break;
        }
        let s: u64 = (p..n).map(|i| resid(x, p, i).unsigned_abs()).fold(0u64, |a, b| a.saturating_add(b));
        if s < best_sum {
            best_sum = s;
            best_p = p;
        }
    }
    let p = best_p;
    let mut us = Vec::with_capacity(n);
    for i in p..n {
        let e = resid(x, p, i);
        let u = ((e << 1) ^ (e >> 63)) as u64;
        if u > u32::MAX as u64 {
            return false;
        }
        us.push(u as u32);
    }
    let mut best_k = 0u32;
    let mut best_bits = u64::MAX;
    for k in 0..=24u32 {
        let bits: u64 = us.iter().map(|&u| (u >> k) as u64 + 1 + k as u64).sum();
        if bits < best_bits {
            best_bits = bits;
            best_k = k;
        }
    }
    w.push(((p as u8) << 5) | best_k as u8);
    for i in 0..p {
        w.extend_from_slice(&(x[i] as i32).to_le_bytes());
    }
    let mut bw = BitW::new();
    for &u in &us {
        if !bw.rice(u, best_k) {
            return false;
        }
    }
    w.extend_from_slice(&bw.finish());
    w.len() <= LL_MAX
}

fn dec_chan(d: &[u8], pos: &mut usize, n: usize) -> Option<Vec<i64>> {
    let h = *d.get(*pos)?;
    *pos += 1;
    let p = (h >> 5) as usize;
    let k = (h & 31) as u32;
    if p > 3 || p > n || k > 30 {
        return None;
    }
    let mut x: Vec<i64> = Vec::with_capacity(n);
    for _ in 0..p {
        let b = d.get(*pos..*pos + 4)?;
        x.push(i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as i64);
        *pos += 4;
    }
    let mut br = BitR { d: d.get(*pos..)?, pos: 0 };
    for i in p..n {
        let u = br.rice(k)? as i64;
        let e = (u >> 1) ^ (-(u & 1));
        let pred = match p {
            0 => 0,
            1 => x[i - 1],
            2 => 2 * x[i - 1] - x[i - 2],
            _ => 3 * x[i - 1] - 3 * x[i - 2] + x[i - 3],
        };
        x.push(pred.wrapping_add(e));
    }
    *pos += br.bytes_used();
    Some(x)
}

/// depth is 16 or 24. Returns None when the frame does not fit one datagram (the caller splits it).
pub fn ll_encode(l: &[i32], r: &[i32], nch: usize, depth: u8) -> Option<Vec<u8>> {
    let n = l.len();
    if n == 0 || nch == 0 || nch > 2 || (nch == 2 && r.len() != n) {
        return None;
    }
    let raw_len = 1 + n * nch * (depth as usize / 8);
    let xl: Vec<i64> = l.iter().map(|&v| v as i64).collect();
    let mut best: Option<Vec<u8>> = None;
    let mut w0 = vec![0u8];
    let ok0 = enc_chan(&xl, &mut w0) && (nch == 1 || {
        let xr: Vec<i64> = r.iter().map(|&v| v as i64).collect();
        enc_chan(&xr, &mut w0)
    });
    if ok0 {
        best = Some(w0);
    }
    if nch == 2 {
        let m: Vec<i64> = (0..n).map(|i| (l[i] as i64 + r[i] as i64) >> 1).collect();
        let s: Vec<i64> = (0..n).map(|i| l[i] as i64 - r[i] as i64).collect();
        let mut w1 = vec![1u8];
        if enc_chan(&m, &mut w1) && enc_chan(&s, &mut w1) && best.as_ref().map_or(true, |b| w1.len() < b.len()) {
            best = Some(w1);
        }
    }
    match best {
        Some(b) if b.len() < raw_len => Some(b),
        _ if raw_len <= LL_MAX => {
            let mut w = vec![2u8];
            let bytes = depth as usize / 8;
            for i in 0..n {
                for c in 0..nch {
                    let v = if c == 0 { l[i] } else { r[i] };
                    w.extend_from_slice(&v.to_le_bytes()[..bytes]);
                }
            }
            Some(w)
        }
        _ => None,
    }
}

pub fn ll_decode(d: &[u8], n: usize, nch: usize, depth: u8) -> Option<(Vec<i32>, Vec<i32>)> {
    if n == 0 || n > 256 || nch == 0 || nch > 2 || (depth != 16 && depth != 24) {
        return None;
    }
    let mode = *d.first()?;
    let lim = 1i64 << depth;
    let clampi = |v: i64| v.clamp(-lim, lim - 1) as i32;
    let mut pos = 1usize;
    match mode {
        2 => {
            let bytes = depth as usize / 8;
            let need = 1 + n * nch * bytes;
            if d.len() < need {
                return None;
            }
            let (mut l, mut r) = (Vec::with_capacity(n), Vec::with_capacity(n));
            for i in 0..n {
                for c in 0..nch {
                    let o = 1 + (i * nch + c) * bytes;
                    let b = &d[o..o + bytes];
                    let v = if bytes == 2 {
                        i16::from_le_bytes([b[0], b[1]]) as i32
                    } else {
                        (((b[0] as i32) | (b[1] as i32) << 8 | (b[2] as i32) << 16) << 8) >> 8
                    };
                    if c == 0 {
                        l.push(v);
                    } else {
                        r.push(v);
                    }
                }
            }
            if nch == 1 {
                r = l.clone();
            }
            Some((l, r))
        }
        0 => {
            let a = dec_chan(d, &mut pos, n)?;
            let b = if nch == 2 { dec_chan(d, &mut pos, n)? } else { a.clone() };
            Some((a.into_iter().map(clampi).collect(), b.into_iter().map(clampi).collect()))
        }
        1 if nch == 2 => {
            let m = dec_chan(d, &mut pos, n)?;
            let s = dec_chan(d, &mut pos, n)?;
            let (mut l, mut r) = (Vec::with_capacity(n), Vec::with_capacity(n));
            for i in 0..n {
                let mid = (m[i] << 1) | (s[i] & 1);
                l.push(clampi((mid + s[i]) >> 1));
                r.push(clampi((mid - s[i]) >> 1));
            }
            Some((l, r))
        }
        _ => None,
    }
}

// =============================================================== Opus (system libopus, thin FFI)
#[cfg(not(windows))]
#[link(name = "opus")]
extern "C" {
    fn opus_encoder_create(fs: i32, channels: c_int, application: c_int, error: *mut c_int) -> *mut c_void;
    fn opus_encoder_destroy(st: *mut c_void);
    fn opus_encode_float(st: *mut c_void, pcm: *const f32, frame_size: c_int, data: *mut u8, max_data_bytes: i32) -> i32;
    fn opus_encoder_ctl(st: *mut c_void, request: c_int, ...) -> c_int;
    fn opus_decoder_create(fs: i32, channels: c_int, error: *mut c_int) -> *mut c_void;
    fn opus_decoder_destroy(st: *mut c_void);
    fn opus_decode_float(st: *mut c_void, data: *const u8, len: i32, pcm: *mut f32, frame_size: c_int, decode_fec: c_int) -> c_int;
}

/// Windows: opus.dll is loaded when first needed (put opus.dll / libopus-0.dll next to the engine). No link-time
/// dependency, so the engine starts without it; the Opus codecs then report "unavailable" instead of the exe failing.
#[cfg(windows)]
mod winopus {
    use super::*;
    use std::sync::OnceLock;
    type EncCreate = unsafe extern "C" fn(i32, c_int, c_int, *mut c_int) -> *mut c_void;
    type Destroy = unsafe extern "C" fn(*mut c_void);
    type Encode = unsafe extern "C" fn(*mut c_void, *const f32, c_int, *mut u8, i32) -> i32;
    type Ctl = unsafe extern "C" fn(*mut c_void, c_int, ...) -> c_int;
    type DecCreate = unsafe extern "C" fn(i32, c_int, *mut c_int) -> *mut c_void;
    type Decode = unsafe extern "C" fn(*mut c_void, *const u8, i32, *mut f32, c_int, c_int) -> c_int;
    pub struct Lib {
        _l: libloading::Library,
        pub enc_create: EncCreate,
        pub enc_destroy: Destroy,
        pub encode: Encode,
        pub ctl: Ctl,
        pub dec_create: DecCreate,
        pub dec_destroy: Destroy,
        pub decode: Decode,
    }
    pub fn lib() -> Option<&'static Lib> {
        static L: OnceLock<Option<Lib>> = OnceLock::new();
        L.get_or_init(|| unsafe {
            let dir = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf()));
            let mut names: Vec<std::path::PathBuf> = vec![];
            for n in ["opus.dll", "libopus-0.dll", "libopus.dll"] {
                if let Some(d) = &dir {
                    names.push(d.join(n));
                }
                names.push(n.into());
            }
            let l = names.iter().find_map(|n| libloading::Library::new(n).ok())?;
            Some(Lib {
                enc_create: *l.get::<EncCreate>(b"opus_encoder_create\0").ok()?,
                enc_destroy: *l.get::<Destroy>(b"opus_encoder_destroy\0").ok()?,
                encode: *l.get::<Encode>(b"opus_encode_float\0").ok()?,
                ctl: *l.get::<Ctl>(b"opus_encoder_ctl\0").ok()?,
                dec_create: *l.get::<DecCreate>(b"opus_decoder_create\0").ok()?,
                dec_destroy: *l.get::<Destroy>(b"opus_decoder_destroy\0").ok()?,
                decode: *l.get::<Decode>(b"opus_decode_float\0").ok()?,
                _l: l,
            })
        })
        .as_ref()
    }
}
#[cfg(windows)]
unsafe fn opus_encoder_create(fs: i32, ch: c_int, app: c_int, err: *mut c_int) -> *mut c_void {
    match winopus::lib() {
        Some(l) => (l.enc_create)(fs, ch, app, err),
        None => std::ptr::null_mut(),
    }
}
#[cfg(windows)]
unsafe fn opus_encoder_destroy(st: *mut c_void) {
    if let Some(l) = winopus::lib() {
        (l.enc_destroy)(st)
    }
}
#[cfg(windows)]
unsafe fn opus_encode_float(st: *mut c_void, pcm: *const f32, fr: c_int, data: *mut u8, max: i32) -> i32 {
    winopus::lib().map_or(-1, |l| (l.encode)(st, pcm, fr, data, max))
}
#[cfg(windows)]
unsafe fn opus_encoder_ctl(st: *mut c_void, req: c_int, v: i32) -> c_int {
    winopus::lib().map_or(-1, |l| (l.ctl)(st, req, v))
}
#[cfg(windows)]
unsafe fn opus_decoder_create(fs: i32, ch: c_int, err: *mut c_int) -> *mut c_void {
    match winopus::lib() {
        Some(l) => (l.dec_create)(fs, ch, err),
        None => std::ptr::null_mut(),
    }
}
#[cfg(windows)]
unsafe fn opus_decoder_destroy(st: *mut c_void) {
    if let Some(l) = winopus::lib() {
        (l.dec_destroy)(st)
    }
}
#[cfg(windows)]
unsafe fn opus_decode_float(st: *mut c_void, d: *const u8, len: i32, pcm: *mut f32, fr: c_int, fec: c_int) -> c_int {
    winopus::lib().map_or(-1, |l| (l.decode)(st, d, len, pcm, fr, fec))
}

pub const OPUS_MAX_FRAME: usize = 5760; // 120 ms at 48 kHz

pub struct OpusEnc {
    st: *mut c_void,
    pub ch: usize,
}
unsafe impl Send for OpusEnc {}
impl OpusEnc {
    pub fn new(ch: usize, voice: bool, kbps: u32) -> Option<Self> {
        if ch == 0 || ch > 2 {
            return None;
        }
        unsafe {
            let mut err: c_int = 0;
            let st = opus_encoder_create(48000, ch as c_int, if voice { 2048 } else { 2049 }, &mut err);
            if st.is_null() || err != 0 {
                return None;
            }
            opus_encoder_ctl(st, 4002 as c_int, (kbps.clamp(6, 510) * 1000) as i32); // bitrate
            opus_encoder_ctl(st, 4010 as c_int, 10i32); // complexity
            opus_encoder_ctl(st, 4024 as c_int, if voice { 3001i32 } else { 3002i32 }); // signal voice / music
            opus_encoder_ctl(st, 4012 as c_int, 1i32); // in-band FEC
            opus_encoder_ctl(st, 4014 as c_int, if voice { 10i32 } else { 3i32 }); // expected loss %
            Some(OpusEnc { st, ch })
        }
    }
    /// pcm is interleaved, frame samples per channel.
    pub fn encode(&mut self, pcm: &[f32], frame: usize, out: &mut [u8]) -> Option<usize> {
        if pcm.len() < frame * self.ch || frame == 0 {
            return None;
        }
        let r = unsafe { opus_encode_float(self.st, pcm.as_ptr(), frame as c_int, out.as_mut_ptr(), out.len() as i32) };
        if r > 0 {
            Some(r as usize)
        } else {
            None
        }
    }
}
impl Drop for OpusEnc {
    fn drop(&mut self) {
        unsafe { opus_encoder_destroy(self.st) }
    }
}

pub struct OpusDec {
    st: *mut c_void,
    pub ch: usize,
}
unsafe impl Send for OpusDec {}
impl OpusDec {
    pub fn new(ch: usize) -> Option<Self> {
        if ch == 0 || ch > 2 {
            return None;
        }
        unsafe {
            let mut err: c_int = 0;
            let st = opus_decoder_create(48000, ch as c_int, &mut err);
            if st.is_null() || err != 0 {
                return None;
            }
            Some(OpusDec { st, ch })
        }
    }
    /// data = Some(packet) for normal decode (frame = max) or FEC recovery (fec, frame = size of the lost frame);
    /// data = None for packet-loss concealment of `frame` samples. Returns samples per channel.
    pub fn decode(&mut self, data: Option<&[u8]>, out: &mut [f32], frame: usize, fec: bool) -> Option<usize> {
        let cap = out.len() / self.ch;
        let fs = if data.is_some() && !fec { cap.min(OPUS_MAX_FRAME) } else { frame.min(cap).min(OPUS_MAX_FRAME) };
        if fs == 0 {
            return None;
        }
        let (p, len) = match data {
            Some(d) => (d.as_ptr(), d.len() as i32),
            None => (std::ptr::null(), 0),
        };
        let r = unsafe { opus_decode_float(self.st, p, len, out.as_mut_ptr(), fs as c_int, fec as c_int) };
        if r > 0 {
            Some(r as usize)
        } else {
            None
        }
    }
}
impl Drop for OpusDec {
    fn drop(&mut self) {
        unsafe { opus_decoder_destroy(self.st) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(n: usize, sr: f64, f: f64) -> Vec<f32> {
        (0..n).map(|i| (2.0 * PI * f * i as f64 / sr).sin() as f32 * 0.5).collect()
    }

    #[test]
    fn mulaw_roundtrip_is_close() {
        for s in (-32000i32..32000).step_by(97) {
            let d = mulaw_dec(mulaw_enc(s as i16)) as i32;
            assert!((d - s).abs() <= (s.abs() / 16).max(40), "{s} -> {d}");
        }
    }

    #[test]
    fn lossless_is_bit_exact() {
        for depth in [16u8, 24] {
            let scale = (1i64 << (depth - 1)) as f64 - 1.0;
            let l: Vec<i32> = tone(240, 48000.0, 440.0).iter().map(|&x| (x as f64 * 2.0 * scale) as i32).collect();
            let r: Vec<i32> = tone(240, 48000.0, 660.0).iter().map(|&x| (x as f64 * 2.0 * scale) as i32).collect();
            let enc = ll_encode(&l, &r, 2, depth).unwrap();
            assert!(enc.len() < 240 * 2 * (depth as usize / 8), "should compress a tone");
            let (dl, dr) = ll_decode(&enc, 240, 2, depth).unwrap();
            assert_eq!(dl, l);
            assert_eq!(dr, r);
        }
    }

    #[test]
    fn lossless_noise_falls_back_or_splits() {
        let mut x = 12345u32;
        let mut rnd = || {
            x = x.wrapping_mul(1664525).wrapping_add(1013904223);
            ((x >> 8) as i32 & 0xFFFF) - 32768
        };
        let l: Vec<i32> = (0..256).map(|_| rnd()).collect();
        let r: Vec<i32> = (0..256).map(|_| rnd()).collect();
        match ll_encode(&l, &r, 2, 16) {
            Some(e) => {
                let (dl, dr) = ll_decode(&e, 256, 2, 16).unwrap();
                assert_eq!((dl, dr), (l, r));
            }
            None => {} // too big for one datagram: the sender splits it
        }
    }

    #[test]
    fn lossless_rejects_garbage() {
        let mut x = 7u32;
        for len in 0..80usize {
            let d: Vec<u8> = (0..len)
                .map(|_| {
                    x = x.wrapping_mul(1103515245).wrapping_add(12345);
                    (x >> 16) as u8
                })
                .collect();
            for n in [1usize, 16, 240, 256] {
                let _ = ll_decode(&d, n, 2, 16);
                let _ = ll_decode(&d, n, 1, 24);
            }
        }
    }

    #[test]
    fn resampler_keeps_level() {
        let input = tone(48000, 44100.0, 1000.0);
        let mut rs = Resampler::new(44100, 48000);
        let (mut ol, mut or_) = (Vec::new(), Vec::new());
        for c in input.chunks(441) {
            rs.process(c, c, &mut ol, &mut or_);
        }
        assert!(ol.len() > 52000 && ol.len() < 52300, "got {}", ol.len());
        let rms = (ol[4000..40000].iter().map(|x| x * x).sum::<f32>() / 36000.0).sqrt();
        assert!((rms - 0.3536).abs() < 0.01, "rms {rms}");
    }

    #[test]
    fn resampler_down_from_384k() {
        let input = tone(384000, 384000.0, 1000.0);
        let mut rs = Resampler::new(384000, 48000);
        let (mut ol, mut or_) = (Vec::new(), Vec::new());
        for c in input.chunks(3840) {
            rs.process(c, c, &mut ol, &mut or_);
        }
        assert!(ol.len() > 47000 && ol.len() < 48100, "got {}", ol.len());
        let rms = (ol[2000..40000].iter().map(|x| x * x).sum::<f32>() / 38000.0).sqrt();
        assert!((rms - 0.3536).abs() < 0.01, "rms {rms}");
    }

    #[test]
    fn opus_roundtrip() {
        let mut enc = OpusEnc::new(2, false, 128).expect("opus encoder");
        let mut dec = OpusDec::new(2).expect("opus decoder");
        let t = tone(48000, 48000.0, 1000.0);
        let mut out_all = Vec::new();
        let mut buf = vec![0u8; 1400];
        let mut pcm = vec![0f32; 960 * 2];
        let mut o = vec![0f32; OPUS_MAX_FRAME * 2];
        for f in 0..50 {
            for i in 0..480 {
                pcm[i * 2] = t[f * 480 + i];
                pcm[i * 2 + 1] = t[f * 480 + i];
            }
            let n = enc.encode(&pcm, 480, &mut buf).unwrap();
            let got = dec.decode(Some(&buf[..n]), &mut o, 0, false).unwrap();
            assert_eq!(got, 480);
            out_all.extend_from_slice(&o[..got * 2]);
        }
        let rms = (out_all[4000..].iter().map(|x| x * x).sum::<f32>() / (out_all.len() - 4000) as f32).sqrt();
        assert!((rms - 0.3536).abs() < 0.05, "rms {rms}");
        // concealment of a lost frame returns audio of the right length
        let g = dec.decode(None, &mut o, 480, false).unwrap();
        assert_eq!(g, 480);
    }
}
