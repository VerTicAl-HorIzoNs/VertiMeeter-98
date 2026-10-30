//! OBAN / VBAN network audio: receiver and sender worker threads.
//!
//! OBAN ("Open VBAN-compatible networking") uses the same 28-byte header as VBAN with the magic "OBAN".
//! PCM is byte-identical to VBAN PCM. Own codec IDs (0x40 Opus music, 0x50 Opus voice, 0x60 lossless,
//! 0x70 mu-law) are only used under the OBAN magic. VB-Audio's closed codecs (0x10, 0x20, 0xF0) are
//! recognised so the status line can say what arrived, but they are never decoded and never sent.
//! Every parser here is bounds-checked: the release build aborts on panic.
//! Addresses and ports only ever come from the control socket (config / user input); nothing is hard-coded.
use crate::codec::*;
use crate::net::{Ring, Stop};
use std::collections::{BTreeMap, VecDeque};
use std::net::{IpAddr, SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering::*};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const HDR: usize = 28;
pub const MAX_PAYLOAD: usize = 1408; // 1436 bytes per datagram minus the header
const PING_STRUCT: usize = 676;

pub const SR_TABLE: [u32; 21] = [
    6000, 12000, 24000, 48000, 96000, 192000, 384000, 8000, 16000, 32000, 64000, 128000, 256000, 512000, 11025, 22050,
    44100, 88200, 176400, 352800, 705600,
];
pub fn sr_index(sr: u32) -> Option<u8> {
    SR_TABLE.iter().position(|&x| x == sr).map(|i| i as u8)
}

/// VBAN-TEXT lines received from the allowed sender; the GUI drains them with the meters poll.
pub fn text_queue() -> &'static Mutex<VecDeque<String>> {
    static Q: OnceLock<Mutex<VecDeque<String>>> = OnceLock::new();
    Q.get_or_init(|| Mutex::new(VecDeque::new()))
}

// ---------------------------------------------------------------- header
#[derive(Clone, Debug)]
pub struct Hdr {
    pub oban: bool,
    pub sub: u8, // 0x00 audio, 0x20 serial, 0x40 text, 0x60 service, 0xE0 user
    pub sr_idx: u8,
    pub nbs: u8,
    pub nbc: u8,
    pub fmt: u8,
    pub name: String,
    pub counter: u32,
}

pub fn parse_hdr(b: &[u8]) -> Option<Hdr> {
    if b.len() < HDR {
        return None;
    }
    let oban = match &b[0..4] {
        b"VBAN" => false,
        b"OBAN" => true,
        _ => return None,
    };
    let end = b[8..24].iter().position(|&c| c == 0).unwrap_or(16);
    Some(Hdr {
        oban,
        sub: b[4] & 0xE0,
        sr_idx: b[4] & 0x1F,
        nbs: b[5],
        nbc: b[6],
        fmt: b[7],
        name: String::from_utf8_lossy(&b[8..8 + end]).to_string(),
        counter: u32::from_le_bytes([b[24], b[25], b[26], b[27]]),
    })
}

pub fn build_hdr(magic: &[u8; 4], sub_sr: u8, nbs: u8, nbc: u8, fmt: u8, name: &str, counter: u32) -> Vec<u8> {
    let mut p = Vec::with_capacity(HDR + MAX_PAYLOAD);
    p.extend_from_slice(magic);
    p.push(sub_sr);
    p.push(nbs);
    p.push(nbc);
    p.push(fmt);
    let mut n = [0u8; 16];
    for (i, b) in name.bytes().take(16).enumerate() {
        n[i] = b;
    }
    p.extend_from_slice(&n);
    p.extend_from_slice(&counter.to_le_bytes());
    p
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Codec {
    Pcm,
    OpusMusic,
    OpusVoice,
    Lossless,
    MuLaw,
    VbCa,
    VbCv,
    UserCodec,
    Unknown(u8),
}

pub fn codec_of(oban: bool, fmt: u8) -> Codec {
    match fmt & 0xF0 {
        0x00 => Codec::Pcm,
        0x10 => Codec::VbCa,
        0x20 => Codec::VbCv,
        0xF0 => Codec::UserCodec,
        0x40 if oban => Codec::OpusMusic,
        0x50 if oban => Codec::OpusVoice,
        0x60 if oban => Codec::Lossless,
        0x70 if oban => Codec::MuLaw,
        x => Codec::Unknown(x),
    }
}

fn codec_label(c: Codec) -> String {
    match c {
        Codec::Pcm => "PCM".into(),
        Codec::OpusMusic => "Opus music".into(),
        Codec::OpusVoice => "Opus voice".into(),
        Codec::Lossless => "VFLAC".into(),
        Codec::MuLaw => "mu-law".into(),
        Codec::VbCa => "VB-Audio VBCA".into(),
        Codec::VbCv => "VB-Audio VBCV".into(),
        Codec::UserCodec => "VBAN USER codec".into(),
        Codec::Unknown(x) => format!("unknown codec 0x{x:02X}"),
    }
}

// ---------------------------------------------------------------- PCM helpers
fn dt_bytes(dt: u8) -> Option<usize> {
    match dt {
        0 => Some(1),
        1 => Some(2),
        2 => Some(3),
        3 | 4 => Some(4),
        5 => Some(8),
        _ => None,
    }
}

/// Decode a PCM payload to two f32 channels (mono is duplicated; with more than two channels the first two are used).
#[allow(dead_code)]
pub fn pcm_decode(dt: u8, nch: usize, payload: &[u8]) -> Option<(Vec<f32>, Vec<f32>)> {
    let bytes = dt_bytes(dt)?;
    if nch == 0 {
        return None;
    }
    let frames = payload.len() / (bytes * nch);
    if frames == 0 {
        return None;
    }
    let (mut l, mut r) = (Vec::with_capacity(frames), Vec::with_capacity(frames));
    for f in 0..frames {
        let mut s = [0.0f32; 2];
        for c in 0..nch.min(2) {
            let o = (f * nch + c) * bytes;
            let b = &payload[o..o + bytes];
            s[c] = match dt {
                0 => (b[0] as f32 - 128.0) / 128.0,
                1 => i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0,
                2 => ((((b[0] as i32) << 8) | ((b[1] as i32) << 16) | ((b[2] as i32) << 24)) >> 8) as f32 / 8388608.0,
                3 => i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32 / 2147483648.0,
                4 => f32::from_le_bytes([b[0], b[1], b[2], b[3]]),
                _ => f64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]) as f32,
            };
            if !s[c].is_finite() {
                s[c] = 0.0;
            }
        }
        if nch == 1 {
            s[1] = s[0];
        }
        l.push(s[0]);
        r.push(s[1]);
    }
    Some((l, r))
}

/// Decode a PCM payload to up to MAX_NET_CH channels (extra channels in the stream are dropped).
pub fn pcm_decode_n(dt: u8, nch: usize, payload: &[u8]) -> Option<Vec<Vec<f32>>> {
    let bytes = dt_bytes(dt)?;
    if nch == 0 {
        return None;
    }
    let frames = payload.len() / (bytes * nch);
    if frames == 0 {
        return None;
    }
    let keep = nch.min(MAX_NET_CH);
    let mut out: Vec<Vec<f32>> = (0..keep).map(|_| Vec::with_capacity(frames)).collect();
    for f in 0..frames {
        for c in 0..keep {
            let o = (f * nch + c) * bytes;
            let b = &payload[o..o + bytes];
            let x = match dt {
                0 => (b[0] as f32 - 128.0) / 128.0,
                1 => i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0,
                2 => ((((b[0] as i32) << 8) | ((b[1] as i32) << 16) | ((b[2] as i32) << 24)) >> 8) as f32 / 8388608.0,
                3 => i32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f32 / 2147483648.0,
                4 => f32::from_le_bytes([b[0], b[1], b[2], b[3]]),
                _ => f64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]]) as f32,
            };
            out[c].push(if x.is_finite() { x } else { 0.0 });
        }
    }
    Some(out)
}

/// Interleave any number of channels as PCM. Returns how many samples were beyond full scale (they are clamped).
pub fn pcm_pack_n(dt: u8, chans: &[&[f32]], out: &mut Vec<u8>) -> u64 {
    let frames = chans.iter().map(|c| c.len()).min().unwrap_or(0);
    let mut clips = 0u64;
    for i in 0..frames {
        for c in chans {
            let x = if c[i].is_finite() { c[i] } else { 0.0 };
            if x.abs() > 1.0 {
                clips += 1;
            }
            match dt {
                0 => out.push(((x.clamp(-1.0, 1.0) * 127.0) + 128.0).round() as u8),
                1 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) * 32767.0).round() as i16).to_le_bytes()),
                2 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) * 8388607.0).round() as i32).to_le_bytes()[..3]),
                3 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) as f64 * 2147483647.0) as i32).to_le_bytes()),
                4 => out.extend_from_slice(&x.to_le_bytes()),
                _ => out.extend_from_slice(&(x as f64).to_le_bytes()),
            }
        }
    }
    clips
}

pub fn pcm_pack(dt: u8, ch: usize, l: &[f32], r: &[f32], out: &mut Vec<u8>) {
    for i in 0..l.len().min(r.len()) {
        for c in 0..ch {
            let x = if ch == 1 { (l[i] + r[i]) * 0.5 } else if c == 0 { l[i] } else { r[i] };
            let x = if x.is_finite() { x } else { 0.0 };
            match dt {
                0 => out.push(((x.clamp(-1.0, 1.0) * 127.0) + 128.0).round() as u8),
                1 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) * 32767.0).round() as i16).to_le_bytes()),
                2 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) * 8388607.0).round() as i32).to_le_bytes()[..3]),
                3 => out.extend_from_slice(&((x.clamp(-1.0, 1.0) as f64 * 2147483647.0) as i32).to_le_bytes()),
                4 => out.extend_from_slice(&x.to_le_bytes()),
                _ => out.extend_from_slice(&(x as f64).to_le_bytes()),
            }
        }
    }
}

// ---------------------------------------------------------------- PING reply (VBAN SERVICE / IDENTIFICATION)
pub fn ping_reply(req: &Hdr) -> Vec<u8> {
    let magic = b"VBAN";
    let mut p = build_hdr(magic, 0x60, 0x80, 0, 0, &req.name, req.counter); // function PING0 | REPLY
    let mut s = vec![0u8; PING_STRUCT];
    let put32 = |s: &mut Vec<u8>, o: usize, v: u32| s[o..o + 4].copy_from_slice(&v.to_le_bytes());
    put32(&mut s, 0, 0x01 | 0x02 | 0x20); // receptor | transmitter | virtual mixer
    put32(&mut s, 4, 0x0000_0001 | 0x0001_0000); // audio | text
    put32(&mut s, 12, 48000);
    put32(&mut s, 16, 6000);
    put32(&mut s, 20, 705600);
    put32(&mut s, 24, 0x007F_00FF);
    s[28..32].copy_from_slice(&[0, 10, 0, 0]);
    let text = |s: &mut Vec<u8>, o: usize, len: usize, t: &str| {
        for (i, b) in t.bytes().take(len - 1).enumerate() {
            s[o + i] = b;
        }
    };
    text(&mut s, 164, 64, "VertiMeeter");
    text(&mut s, 292, 64, "VertiMeeter '98");
    text(&mut s, 548, 128, "OBAN / VBAN compatible mixer");
    p.extend_from_slice(&s);
    p
}

use crate::params::{Params, MAX_NET_CH, RX_FEEDS};

// ---------------------------------------------------------------- receiver
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum RxMode {
    Auto,
    PcmOnly,
    CodedOnly,
}

pub struct RxCfg {
    pub port: u16,
    pub stream: String,        // empty = accept any stream name
    pub allow: Option<IpAddr>, // None = lock to the first sender
    pub engine_sr: u32,
    pub accept_oban: bool, // false = classic VBAN strip: OBAN packets are ignored
    pub mode: RxMode,
    pub buf_ms: u32, // jitter / reorder buffer
    pub ping: bool,  // answer VBAN device scans
    pub text: bool,  // forward VBAN-TEXT commands (needs an allowed sender IP)
}

struct Pending {
    h: Hdr,
    data: Vec<u8>,
    at: Instant,
}

#[derive(Default)]
struct St {
    pkts: u64,
    lost: u64,
    late: u64,
    fec: u64,
    plc: u64,
    foreign: u64,
    bad: u64,
    slips: u64,
    underruns: u64,
    unsup: u64,
    texts: u64,
}

struct Rx {
    cfg: RxCfg,
    p: Arc<Params>,
    k: usize,
    stats: Arc<Mutex<String>>,
    locked: Option<IpAddr>,
    last_rx: Instant,
    key: Option<(Codec, u32, usize, u8)>,
    name: String,
    started: bool,
    base_c: u32,
    ext: i64,
    pending: BTreeMap<i64, Pending>,
    rs: Vec<Resampler>,
    rs_sr: u32,
    odec: Option<OpusDec>,
    obuf: Vec<f32>,
    last_len: usize,
    hold: Vec<(Vec<f32>, Vec<f32>)>,
    primed: bool,
    target: usize,
    st: St,
    jitter: f64,
    last_arr: Option<(Instant, f64)>,
    media_t: f64,
    bytes_win: u64,
    win_t: Instant,
    kbps: f32,
    note: Option<(String, Instant)>,
    last_status: Instant,
    last_ping: Instant,
    ever: bool,
}

impl Rx {
    fn new(cfg: RxCfg, p: Arc<Params>, k: usize, stats: Arc<Mutex<String>>) -> Self {
        let target = (cfg.buf_ms.min(500) as usize * cfg.engine_sr as usize) / 1000;
        for i in 0..RX_FEEDS {
            p.net_target[k * RX_FEEDS + i].store(target, Relaxed);
        }
        p.net_rx_ch[k].store(0, Relaxed);
        Rx {
            cfg,
            p,
            k,
            stats,
            locked: None,
            last_rx: Instant::now(),
            key: None,
            name: String::new(),
            started: false,
            base_c: 0,
            ext: 0,
            pending: BTreeMap::new(),
            rs: Vec::new(),
            rs_sr: 0,
            odec: None,
            obuf: vec![0.0; OPUS_MAX_FRAME * 2],
            last_len: 480,
            hold: Vec::new(),
            primed: target == 0,
            target,
            st: St::default(),
            jitter: 0.0,
            last_arr: None,
            media_t: 0.0,
            bytes_win: 0,
            win_t: Instant::now(),
            kbps: 0.0,
            note: None,
            last_status: Instant::now() - Duration::from_secs(5),
            last_ping: Instant::now() - Duration::from_secs(5),
            ever: false,
        }
    }

    fn note(&mut self, s: String) {
        self.note = Some((s, Instant::now()));
    }

    fn ring(&self, feed: usize) -> &Arc<Ring> {
        &self.p.net_rx[self.k * RX_FEEDS + feed]
    }

    fn bound(&self, feed: usize) -> bool {
        self.p.net_rx_slot[self.k * RX_FEEDS + feed].load(Relaxed) >= 0
    }

    /// (left, right) channel of the stream this feed carries.
    fn feed_ch(&self, feed: usize) -> (usize, usize) {
        let v = self.p.net_rx_feed[self.k * RX_FEEDS + feed].load(Relaxed);
        ((v & 0xff) as usize, ((v >> 8) & 0xff) as usize)
    }

    /// The feed whose ring decides priming / drift: the first one a strip is actually reading.
    fn refp(&self) -> usize {
        (0..RX_FEEDS).find(|&i| self.bound(i)).unwrap_or(0)
    }

    fn reset_stream(&mut self) {
        self.rs.clear();
        self.odec = None;
        self.pending.clear();
        self.started = false;
        self.hold.clear();
        self.primed = self.target == 0;
        self.last_arr = None;
    }

    fn relock(&mut self, ip: IpAddr) {
        self.reset_stream();
        self.key = None;
        self.locked = Some(ip);
        self.st = St::default();
        self.jitter = 0.0;
    }

    fn packet(&mut self, buf: &[u8], from: SocketAddr, sock: &UdpSocket) {
        let Some(h) = parse_hdr(buf) else { return };
        if h.oban && !self.cfg.accept_oban {
            self.note(format!("ignored OBAN packet from {}: this input is VBAN only", from.ip()));
            return;
        }
        match h.sub {
            0x00 => self.audio(h, buf, from),
            0x40 => self.text(&h, buf, from),
            0x60 => self.service(&h, buf, from, sock),
            _ => {}
        }
    }

    fn text(&mut self, h: &Hdr, buf: &[u8], from: SocketAddr) {
        if !self.cfg.text || h.name != "Command1" {
            return;
        }
        let Some(a) = self.cfg.allow else {
            self.note("text control needs a sender IP filter".into());
            return;
        };
        if from.ip() != a {
            return;
        }
        let s = String::from_utf8_lossy(&buf[HDR..]).trim_matches('\0').to_string();
        let mut q = text_queue().lock().unwrap();
        if q.len() < 64 {
            q.push_back(s);
        }
        self.st.texts += 1;
    }

    fn service(&mut self, h: &Hdr, buf: &[u8], from: SocketAddr, sock: &UdpSocket) {
        // identification service, PING0 request (no reply bit). Replies are never larger than the request.
        if !self.cfg.ping || h.oban || h.nbc != 0 || h.nbs != 0 || buf.len() < HDR + PING_STRUCT {
            return;
        }
        if let Some(a) = self.cfg.allow {
            if from.ip() != a {
                return;
            }
        }
        if self.last_ping.elapsed() < Duration::from_millis(500) {
            return;
        }
        self.last_ping = Instant::now();
        let _ = sock.send_to(&ping_reply(h), from);
    }

    fn audio(&mut self, h: Hdr, buf: &[u8], from: SocketAddr) {
        let ip = from.ip();
        if let Some(a) = self.cfg.allow {
            if ip != a {
                self.st.foreign += 1;
                self.note(format!("ignored {ip}: not the allowed sender"));
                return;
            }
            if self.locked != Some(ip) {
                self.relock(ip);
            }
        } else {
            match self.locked {
                Some(l) if l != ip => {
                    if self.last_rx.elapsed() > Duration::from_secs(3) {
                        self.relock(ip);
                    } else {
                        self.st.foreign += 1;
                        self.note(format!("ignored {ip}: locked to {l}"));
                        return;
                    }
                }
                None => self.relock(ip),
                _ => {}
            }
        }
        if !self.cfg.stream.is_empty() && h.name != self.cfg.stream {
            self.note(format!("ignored stream '{}' from {ip} (expecting '{}')", h.name, self.cfg.stream));
            return;
        }
        let codec = codec_of(h.oban, h.fmt);
        match codec {
            Codec::VbCa | Codec::VbCv | Codec::UserCodec | Codec::Unknown(_) => {
                self.st.unsup += 1;
                self.note(format!("from {ip}: {} is not supported - ask them to switch the sender to PCM", codec_label(codec)));
                return;
            }
            _ => {}
        }
        match (self.cfg.mode, codec) {
            (RxMode::PcmOnly, c) if c != Codec::Pcm => {
                self.note(format!("ignored {} from {ip}: this input accepts PCM only", codec_label(c)));
                return;
            }
            (RxMode::CodedOnly, Codec::Pcm) => {
                self.note(format!("ignored PCM from {ip}: this input accepts coded streams only"));
                return;
            }
            _ => {}
        }
        if buf.len() <= HDR {
            return;
        }
        let payload = &buf[HDR..];
        let nch = h.nbc as usize + 1;
        let opus = matches!(codec, Codec::OpusMusic | Codec::OpusVoice);
        let sr = if opus {
            48000
        } else {
            match SR_TABLE.get(h.sr_idx as usize) {
                Some(&s) => s,
                None => {
                    self.st.bad += 1;
                    return;
                }
            }
        };
        let dur_ms;
        match codec {
            Codec::Pcm => {
                let Some(b) = dt_bytes(h.fmt & 7) else {
                    self.note(format!("from {ip}: 12/10-bit PCM is not supported - ask them to use 8/16/24/32-bit or float"));
                    return;
                };
                dur_ms = (payload.len() / (b * nch)) as f64 * 1000.0 / sr as f64;
            }
            Codec::Lossless => {
                if nch > 2 || !matches!(h.fmt & 7, 1 | 2) {
                    self.st.bad += 1;
                    return;
                }
                dur_ms = (h.nbs as f64 + 1.0) * 1000.0 / sr as f64;
            }
            Codec::MuLaw => {
                if nch > 2 {
                    self.st.bad += 1;
                    return;
                }
                dur_ms = (h.nbs as f64 + 1.0) * 1000.0 / sr as f64;
            }
            _ => dur_ms = h.nbs as f64 + 1.0,
        }
        let key = (codec, sr, nch.min(if opus { 2 } else { MAX_NET_CH }), h.fmt);
        if self.key != Some(key) {
            self.reset_stream();
            self.key = Some(key);
            self.name = h.name.clone();
            self.p.net_rx_ch[self.k].store(key.2, Relaxed);
        }
        let now = Instant::now();
        self.last_rx = now;
        self.ever = true;
        self.st.pkts += 1;
        self.bytes_win += buf.len() as u64;
        // inter-arrival jitter (RFC 3550 style), only meaningful for consecutive packets
        if let Some((t0, m0)) = self.last_arr {
            let d = (now.duration_since(t0).as_secs_f64() * 1000.0 - (self.media_t - m0)).abs();
            self.jitter += (d.min(500.0) - self.jitter) / 16.0;
        }
        self.last_arr = Some((now, self.media_t));
        self.media_t += dur_ms;
        // ordering by frame counter
        if !self.started {
            self.started = true;
            self.base_c = h.counter;
            self.ext = 0;
        }
        let mut diff = h.counter.wrapping_sub(self.base_c.wrapping_add(self.ext as u32)) as i32;
        if diff.abs() > 4000 {
            self.reset_stream();
            self.started = true;
            self.base_c = h.counter;
            self.ext = 0;
            diff = 0;
        }
        if diff < 0 {
            self.st.late += 1;
            return;
        }
        let k = self.ext + diff as i64;
        if self.pending.contains_key(&k) {
            self.st.late += 1;
            return;
        }
        self.pending.insert(k, Pending { h, data: buf.to_vec(), at: now });
        self.flush(false);
    }

    /// Deliver everything that is in order; give up on a missing packet once it has waited long enough.
    fn flush(&mut self, force: bool) {
        let wait = Duration::from_millis((self.cfg.buf_ms as u64 / 2).clamp(8, 250));
        loop {
            if let Some(p) = self.pending.remove(&self.ext) {
                self.deliver(&p);
                self.ext += 1;
                continue;
            }
            let Some((&first, _)) = self.pending.iter().next() else { return };
            let oldest = self.pending.values().map(|p| p.at).min().unwrap_or_else(Instant::now);
            if force || self.pending.len() >= 24 || oldest.elapsed() > wait {
                let missing = (first - self.ext).max(0) as usize;
                if let Some(p) = self.pending.remove(&first) {
                    self.conceal(missing, &p);
                    self.deliver(&p);
                }
                self.ext = first + 1;
                continue;
            }
            return;
        }
    }

    fn conceal(&mut self, missing: usize, next: &Pending) {
        if missing == 0 {
            return;
        }
        if missing > 40 {
            return; // far too many: resync instead of inventing seconds of audio
        }
        self.st.lost += missing as u64;
        let Some(key) = self.key else { return };
        let ch = key.2.min(2);
        if matches!(key.0, Codec::OpusMusic | Codec::OpusVoice) {
            let next_opus = matches!(codec_of(next.h.oban, next.h.fmt), Codec::OpusMusic | Codec::OpusVoice);
            for i in 0..missing {
                let last = i + 1 == missing;
                let use_fec = last && next_opus;
                let frame = self.last_len.max(120);
                let r = match self.odec.as_mut() {
                    Some(d) => {
                        if use_fec {
                            d.decode(Some(&next.data[HDR..]), &mut self.obuf, frame, true)
                        } else {
                            d.decode(None, &mut self.obuf, frame, false)
                        }
                    }
                    None => None,
                };
                if let Some(n) = r {
                    let (l, rr) = deinterleave(&self.obuf[..n * ch], ch);
                    self.emit2(&l, &rr, 48000);
                    if use_fec {
                        self.st.fec += 1;
                    } else {
                        self.st.plc += 1;
                    }
                }
            }
        } else {
            let chs = vec![vec![0.0f32; self.last_len.max(1) * missing]; key.2.max(1)];
            self.emit(&chs, key.1);
            self.st.plc += missing as u64;
        }
    }

    fn deliver(&mut self, p: &Pending) {
        let h = &p.h;
        let payload = &p.data[HDR..];
        let nch = h.nbc as usize + 1;
        let sr = SR_TABLE.get(h.sr_idx as usize).copied().unwrap_or(48000);
        match codec_of(h.oban, h.fmt) {
            Codec::Pcm => match pcm_decode_n(h.fmt & 7, nch, payload) {
                Some(chs) => {
                    self.last_len = chs[0].len();
                    self.emit(&chs, sr);
                }
                None => self.st.bad += 1,
            },
            Codec::OpusMusic | Codec::OpusVoice => {
                let ch = nch.min(2);
                if self.odec.as_ref().map_or(true, |d| d.ch != ch) {
                    self.odec = OpusDec::new(ch);
                }
                let n = match self.odec.as_mut() {
                    Some(d) => d.decode(Some(payload), &mut self.obuf, 0, false),
                    None => None,
                };
                match n {
                    Some(n) => {
                        self.last_len = n;
                        let (l, r) = deinterleave(&self.obuf[..n * ch], ch);
                        self.emit2(&l, &r, 48000);
                    }
                    None => self.st.bad += 1,
                }
            }
            Codec::Lossless => {
                let depth = if h.fmt & 7 == 2 { 24 } else { 16 };
                match ll_decode(payload, h.nbs as usize + 1, nch, depth) {
                    Some((l, r)) => {
                        let sc = 1.0 / (1u64 << (depth - 1)) as f32;
                        let lf: Vec<f32> = l.iter().map(|&v| v as f32 * sc).collect();
                        let rf: Vec<f32> = r.iter().map(|&v| v as f32 * sc).collect();
                        self.last_len = lf.len();
                        self.emit2(&lf, &rf, sr);
                    }
                    None => self.st.bad += 1,
                }
            }
            Codec::MuLaw => {
                let n = (h.nbs as usize + 1).min(payload.len() / nch.max(1));
                if n == 0 || nch > 2 {
                    self.st.bad += 1;
                    return;
                }
                let (mut l, mut r) = (Vec::with_capacity(n), Vec::with_capacity(n));
                for i in 0..n {
                    let a = mulaw_dec(payload[i * nch]) as f32 / 32768.0;
                    let b = if nch == 2 { mulaw_dec(payload[i * nch + 1]) as f32 / 32768.0 } else { a };
                    l.push(a);
                    r.push(b);
                }
                self.last_len = n;
                self.emit2(&l, &r, sr);
            }
            _ => {}
        }
    }

    fn emit2(&mut self, l: &[f32], r: &[f32], sr: u32) {
        self.emit(&[l.to_vec(), r.to_vec()], sr);
    }

    /// Channels in, one resampled stereo pair per two channels out. An odd last channel is copied to both sides of its pair.
    fn emit(&mut self, chs: &[Vec<f32>], sr: u32) {
        if chs.is_empty() {
            return;
        }
        let npair = chs.len().div_ceil(2).clamp(1, MAX_NET_CH / 2);
        if self.rs.len() != npair || self.rs_sr != sr {
            self.rs = (0..npair).map(|_| Resampler::new(sr, self.cfg.engine_sr)).collect();
            self.rs_sr = sr;
        }
        let mut pairs = Vec::with_capacity(npair);
        for i in 0..npair {
            let a = &chs[2 * i];
            let b = chs.get(2 * i + 1).unwrap_or(a);
            let (mut ol, mut or_) = (Vec::with_capacity(a.len() + 16), Vec::with_capacity(a.len() + 16));
            self.rs[i].process(a, b, &mut ol, &mut or_);
            pairs.push((ol, or_));
        }
        self.to_ring(pairs);
    }

    /// Resampled pairs in; every bound feed picks its two channels out of them and gets its own ring.
    fn to_ring(&mut self, pairs: Vec<(Vec<f32>, Vec<f32>)>) {
        if pairs.is_empty() || pairs[0].0.is_empty() {
            return;
        }
        let n0 = pairs[0].0.len();
        let chan = |c: usize| -> Option<&Vec<f32>> {
            let p = pairs.get(c / 2)?;
            Some(if c % 2 == 0 { &p.0 } else { &p.1 })
        };
        let mut feeds: Vec<(usize, Vec<f32>, Vec<f32>)> = Vec::new();
        for f in 0..RX_FEEDS {
            if !self.bound(f) {
                continue;
            }
            let (a, b) = self.feed_ch(f);
            let zero = vec![0.0f32; n0];
            let l = chan(a).cloned().unwrap_or_else(|| zero.clone());
            let r = chan(b).cloned().unwrap_or(zero);
            feeds.push((f, l, r));
        }
        if feeds.is_empty() {
            return;
        }
        let rp = self.refp();
        let len = self.ring(rp).len();
        if self.primed && len == 0 && self.target > 0 {
            self.primed = false; // the mixer ran dry: rebuild the jitter buffer
            self.st.underruns += 1;
        }
        if !self.primed {
            if self.hold.len() != RX_FEEDS {
                self.hold = vec![(Vec::new(), Vec::new()); RX_FEEDS];
            }
            for (f, l, r) in &feeds {
                self.hold[*f].0.extend_from_slice(l);
                self.hold[*f].1.extend_from_slice(r);
            }
            if self.hold[rp].0.len() >= self.target {
                for f in 0..RX_FEEDS {
                    if self.bound(f) {
                        self.ring(f).push(&self.hold[f].0, &self.hold[f].1);
                    }
                    self.hold[f].0.clear();
                    self.hold[f].1.clear();
                }
                self.primed = true;
            }
            return;
        }
        // clock drift: slip a single frame now and then to keep the fill level near the target
        let band = (self.target / 2).max(480);
        let action = if self.target > 0 && n0 > 16 {
            if len > self.target + band {
                1
            } else if len < self.target.saturating_sub(band) {
                2
            } else {
                0
            }
        } else {
            0
        };
        if action != 0 {
            self.st.slips += 1;
        }
        for (f, mut l, mut r) in feeds {
            if action != 0 {
                let m = l.len() / 2;
                if action == 1 {
                    l.remove(m);
                    r.remove(m);
                } else {
                    let (a, b) = (l[m], r[m]);
                    l.insert(m, a);
                    r.insert(m, b);
                }
            }
            self.ring(f).push(&l, &r);
        }
    }

    fn tick(&mut self) {
        self.flush(false);
        if self.win_t.elapsed() >= Duration::from_secs(1) {
            let s = self.win_t.elapsed().as_secs_f32();
            self.kbps = self.bytes_win as f32 * 8.0 / 1000.0 / s;
            self.bytes_win = 0;
            self.win_t = Instant::now();
        }
        if self.last_status.elapsed() >= Duration::from_millis(400) {
            self.last_status = Instant::now();
            let s = self.status();
            *self.stats.lock().unwrap() = s;
        }
    }

    fn status(&self) -> String {
        let note = match &self.note {
            Some((n, t)) if t.elapsed() < Duration::from_secs(6) => Some(n.as_str()),
            _ => None,
        };
        let main = if !self.ever {
            "waiting for packets".to_string()
        } else if self.last_rx.elapsed() > Duration::from_secs(2) {
            format!("silent for {} s (last from {})", self.last_rx.elapsed().as_secs(), self.locked.map(|i| i.to_string()).unwrap_or_default())
        } else {
            let k = self.key;
            let (cl, sr, ch) = k.map(|k| (codec_label(k.0), k.1, k.2)).unwrap_or_default();
            let total = self.st.pkts + self.st.lost;
            let loss = if total > 0 { self.st.lost as f64 * 100.0 / total as f64 } else { 0.0 };
            let buf_ms = self.ring(self.refp()).len() as f64 * 1000.0 / self.cfg.engine_sr.max(1) as f64;
            format!(
                "receiving '{}' from {} | {} {} Hz {} ch | {:.0} kbps | loss {:.1}% | late {} | jitter {:.1} ms | buffer {:.0} ms",
                self.name,
                self.locked.map(|i| i.to_string()).unwrap_or_default(),
                cl,
                sr,
                ch,
                self.kbps,
                loss,
                self.st.late,
                self.jitter,
                buf_ms
            )
        };
        match note {
            Some(n) => format!("{n}  |  {main}"),
            None => main,
        }
    }
}

fn deinterleave(x: &[f32], ch: usize) -> (Vec<f32>, Vec<f32>) {
    if ch == 2 {
        let n = x.len() / 2;
        ((0..n).map(|i| x[i * 2]).collect(), (0..n).map(|i| x[i * 2 + 1]).collect())
    } else {
        (x.to_vec(), x.to_vec())
    }
}

/// Start receiver `k`. Its rings (one per stereo pair) and strip bindings live in `p`; the jitter-buffer size is published there too.
pub fn start_rx(cfg: RxCfg, p: Arc<Params>, k: usize, stats: Arc<Mutex<String>>) -> Option<Stop> {
    let sock = match UdpSocket::bind(("0.0.0.0", cfg.port)) {
        Ok(s) => s,
        Err(e) => {
            *stats.lock().unwrap() = format!("cannot bind UDP {}: {e}", cfg.port);
            return None;
        }
    };
    let _ = sock.set_read_timeout(Some(Duration::from_millis(5)));
    let stop = Arc::new(AtomicBool::new(false));
    let st2 = stop.clone();
    let h = std::thread::spawn(move || {
        let mut rx = Rx::new(cfg, p.clone(), k, stats);
        let mut buf = vec![0u8; 65536];
        while !st2.load(Relaxed) {
            if let Ok((n, from)) = sock.recv_from(&mut buf) {
                rx.packet(&buf[..n], from, &sock);
            }
            rx.tick();
        }
        for i in 0..RX_FEEDS {
            p.net_target[k * RX_FEEDS + i].store(0, Relaxed);
        }
    });
    Some(Stop::new(stop, h))
}

// ---------------------------------------------------------------- sender
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum TxCodec {
    VbanPcm,
    ObanPcm,
    OpusMusic,
    OpusVoice,
    Lossless,
    MuLaw,
}
impl TxCodec {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "" | "vban_pcm" => TxCodec::VbanPcm,
            "oban_pcm" => TxCodec::ObanPcm,
            "opus_music" => TxCodec::OpusMusic,
            "opus_voice" => TxCodec::OpusVoice,
            "lossless" => TxCodec::Lossless,
            "mulaw" => TxCodec::MuLaw,
            _ => return None,
        })
    }
    fn label(self) -> &'static str {
        match self {
            TxCodec::VbanPcm => "VBAN PCM",
            TxCodec::ObanPcm => "OBAN PCM",
            TxCodec::OpusMusic => "Opus music",
            TxCodec::OpusVoice => "Opus voice",
            TxCodec::Lossless => "lossless",
            TxCodec::MuLaw => "mu-law",
        }
    }
}

pub struct TxCfg {
    pub addr: SocketAddr,
    pub stream: String,
    pub codec: TxCodec,
    pub sr: u32,  // PCM / lossless / mu-law sample rate (Opus is always 48000)
    pub ch: usize, // 1 or 2
    pub dt: u8,   // PCM sample format (VBAN data type 0..5); lossless uses 1 (16-bit) or 2 (24-bit)
    pub kbps: u32,
    pub ms: u32, // Opus frame length
    pub engine_sr: u32,
}

struct Tx {
    sock: UdpSocket,
    cfg: TxCfg,
    counter: u32,
    bytes: u64,
    pkts: u64,
    errs: u64,
    clips: u64,
}

impl Tx {
    fn magic(&self) -> &'static [u8; 4] {
        if self.cfg.codec == TxCodec::VbanPcm {
            b"VBAN"
        } else {
            b"OBAN"
        }
    }
    fn out(&mut self, mut p: Vec<u8>) {
        if p.len() > HDR + MAX_PAYLOAD {
            p.truncate(HDR + MAX_PAYLOAD);
        }
        match self.sock.send_to(&p, self.cfg.addr) {
            Ok(n) => {
                self.bytes += n as u64;
                self.pkts += 1;
            }
            Err(_) => self.errs += 1,
        }
        self.counter = self.counter.wrapping_add(1);
    }
    fn send_pcm(&mut self, l: &[f32], r: &[f32]) {
        let sri = sr_index(self.cfg.sr).unwrap_or(3);
        let mut p = build_hdr(self.magic(), sri, (l.len() - 1) as u8, (self.cfg.ch - 1) as u8, self.cfg.dt & 7, &self.cfg.stream, self.counter);
        pcm_pack(self.cfg.dt & 7, self.cfg.ch, l, r, &mut p);
        self.out(p);
    }
    /// PCM with any channel count (the multichannel path).
    fn send_pcm_n(&mut self, chans: &[&[f32]]) {
        let sri = sr_index(self.cfg.sr).unwrap_or(3);
        let n = chans.iter().map(|c| c.len()).min().unwrap_or(0);
        if n == 0 {
            return;
        }
        let mut p = build_hdr(self.magic(), sri, (n - 1) as u8, (chans.len() - 1) as u8, self.cfg.dt & 7, &self.cfg.stream, self.counter);
        self.clips += pcm_pack_n(self.cfg.dt & 7, chans, &mut p);
        self.out(p);
    }
    fn send_mulaw(&mut self, l: &[f32], r: &[f32]) {
        let sri = sr_index(self.cfg.sr).unwrap_or(7);
        let mut p = build_hdr(self.magic(), sri, (l.len() - 1) as u8, (self.cfg.ch - 1) as u8, 0x70, &self.cfg.stream, self.counter);
        for i in 0..l.len() {
            let a = if self.cfg.ch == 1 { (l[i] + r[i]) * 0.5 } else { l[i] };
            p.push(mulaw_enc((a.clamp(-1.0, 1.0) * 32767.0) as i16));
            if self.cfg.ch == 2 {
                p.push(mulaw_enc((r[i].clamp(-1.0, 1.0) * 32767.0) as i16));
            }
        }
        self.out(p);
    }
    /// One lossless frame per datagram; frames that do not fit are split in halves.
    fn send_lossless(&mut self, l: &[f32], r: &[f32]) {
        let depth: u8 = if self.cfg.dt == 2 { 24 } else { 16 };
        let sc = (1i64 << (depth - 1)) as f32 - 1.0;
        let q = |x: &[f32]| -> Vec<i32> { x.iter().map(|&v| (if v.is_finite() { v } else { 0.0 }).clamp(-1.0, 1.0)).map(|v| (v * sc).round() as i32).collect() };
        let (li, ri) = (q(l), q(r));
        let (li, ri) = if self.cfg.ch == 1 {
            let m: Vec<i32> = li.iter().zip(&ri).map(|(a, b)| (a + b) / 2).collect();
            (m, Vec::new())
        } else {
            (li, ri)
        };
        match ll_encode(&li, &ri, self.cfg.ch, depth) {
            Some(body) => {
                let sri = sr_index(self.cfg.sr).unwrap_or(3);
                let mut p = build_hdr(self.magic(), sri, (l.len() - 1) as u8, (self.cfg.ch - 1) as u8, 0x60 | self.cfg.dt, &self.cfg.stream, self.counter);
                p.extend_from_slice(&body);
                self.out(p);
            }
            None if l.len() > 8 => {
                let h = l.len() / 2;
                self.send_lossless(&l[..h], &r[..h]);
                self.send_lossless(&l[h..], &r[h..]);
            }
            None => {}
        }
    }
}

pub fn start_tx(cfg: TxCfg, rings: Vec<Arc<Ring>>, stats: Arc<Mutex<String>>) -> Option<Stop> {
    let voice = cfg.codec == TxCodec::OpusVoice;
    let is_opus = matches!(cfg.codec, TxCodec::OpusMusic | TxCodec::OpusVoice);
    let tx_sr = if is_opus { 48000 } else { cfg.sr };
    let pcm_codec = matches!(cfg.codec, TxCodec::VbanPcm | TxCodec::ObanPcm);
    if cfg.ch < 1 || cfg.ch > if pcm_codec { MAX_NET_CH } else { 2 } || rings.len() < cfg.ch.div_ceil(2) {
        *stats.lock().unwrap() = "channels must be 1-8 for PCM (1-2 for the other codecs)".into();
        return None;
    }
    if !is_opus && sr_index(tx_sr).is_none() {
        *stats.lock().unwrap() = format!("{tx_sr} Hz is not a VBAN sample rate");
        return None;
    }
    if matches!(cfg.codec, TxCodec::VbanPcm | TxCodec::ObanPcm) && dt_bytes(cfg.dt).is_none() {
        *stats.lock().unwrap() = "unsupported sample format".into();
        return None;
    }
    let mut enc = if is_opus {
        match OpusEnc::new(cfg.ch, voice, cfg.kbps) {
            Some(e) => Some(e),
            None => {
                *stats.lock().unwrap() = "cannot create the Opus encoder".into();
                return None;
            }
        }
    } else {
        None
    };
    let sock = UdpSocket::bind(if cfg.addr.is_ipv6() { "[::]:0" } else { "0.0.0.0:0" }).ok()?;
    let stop = Arc::new(AtomicBool::new(false));
    let st2 = stop.clone();
    let h = std::thread::spawn(move || {
        let ms = match cfg.ms {
            5 | 10 | 20 | 40 => cfg.ms as usize,
            _ => 20,
        };
        let frame = match cfg.codec {
            TxCodec::VbanPcm | TxCodec::ObanPcm => {
                let b = dt_bytes(cfg.dt & 7).unwrap_or(2);
                (MAX_PAYLOAD / (b * cfg.ch)).min(256)
            }
            TxCodec::OpusMusic | TxCodec::OpusVoice => ms * 48,
            TxCodec::Lossless => (tx_sr as usize / 200).clamp(32, 256),
            TxCodec::MuLaw => (tx_sr as usize / 50).clamp(32, 256),
        };
        let name = format!("{} {}{}", cfg.codec.label(), if is_opus { format!("{} kbps", cfg.kbps) } else { String::new() }, if cfg.ch > 2 { format!("{} ch", cfg.ch) } else { String::new() });
        let ring = rings[0].clone();
        let (npair, multi, esr) = (cfg.ch.div_ceil(2), cfg.ch > 2, cfg.engine_sr);
        let (addr, stream, kbps_cfg) = (cfg.addr, cfg.stream.clone(), cfg.kbps);
        let mut rs = Resampler::new(cfg.engine_sr, tx_sr);
        let mut tx = Tx { sock, cfg, counter: 0, bytes: 0, pkts: 0, errs: 0, clips: 0 };
        let (mut cl, mut cr) = (vec![0.0f32; 1024], vec![0.0f32; 1024]);
        let mut mrs: Vec<Resampler> = (0..npair).map(|_| Resampler::new(esr, tx_sr)).collect();
        let (mut mal, mut mar): (Vec<Vec<f32>>, Vec<Vec<f32>>) = (vec![Vec::new(); npair], vec![Vec::new(); npair]);
        let (mut al, mut ar): (Vec<f32>, Vec<f32>) = (Vec::new(), Vec::new());
        let mut obuf = vec![0u8; MAX_PAYLOAD];
        let mut inter = vec![0.0f32; OPUS_MAX_FRAME * 2];
        let (mut win_t, mut win_b, mut kbps) = (Instant::now(), 0u64, 0.0f32);
        let mut last_status = Instant::now() - Duration::from_secs(5);
        while !st2.load(Relaxed) {
            if multi {
                let avail = rings.iter().take(npair).map(|r| r.len()).min().unwrap_or(0);
                if avail < 64 {
                    std::thread::sleep(Duration::from_millis(2));
                } else {
                    let take = avail.min(1024);
                    for (pi, r) in rings.iter().take(npair).enumerate() {
                        let got = r.pop(&mut cl[..take], &mut cr[..take]);
                        mrs[pi].process(&cl[..got], &cr[..got], &mut mal[pi], &mut mar[pi]);
                    }
                    while mal[0].len() >= frame {
                        {
                            let chans: Vec<&[f32]> = (0..tx.cfg.ch).map(|c| if c % 2 == 0 { &mal[c / 2][..frame] } else { &mar[c / 2][..frame] }).collect();
                            tx.send_pcm_n(&chans);
                        }
                        for pi in 0..npair {
                            mal[pi].drain(..frame);
                            mar[pi].drain(..frame);
                        }
                    }
                }
            } else if ring.len() < 64 {
                std::thread::sleep(Duration::from_millis(2));
            } else {
                let got = ring.pop(&mut cl, &mut cr);
                rs.process(&cl[..got], &cr[..got], &mut al, &mut ar);
                while al.len() >= frame && ar.len() >= frame {
                    let (l, r) = (&al[..frame], &ar[..frame]);
                    match tx.cfg.codec {
                        TxCodec::VbanPcm | TxCodec::ObanPcm => tx.send_pcm(l, r),
                        TxCodec::MuLaw => tx.send_mulaw(l, r),
                        TxCodec::Lossless => tx.send_lossless(l, r),
                        TxCodec::OpusMusic | TxCodec::OpusVoice => {
                            let ch = tx.cfg.ch;
                            for i in 0..frame {
                                if ch == 2 {
                                    inter[i * 2] = l[i];
                                    inter[i * 2 + 1] = r[i];
                                } else {
                                    inter[i] = (l[i] + r[i]) * 0.5;
                                }
                            }
                            if let Some(e) = enc.as_mut() {
                                if let Some(n) = e.encode(&inter[..frame * ch], frame, &mut obuf) {
                                    let fmt = if voice { 0x50 } else { 0x40 };
                                    let mut p = build_hdr(b"OBAN", 3, (ms - 1) as u8, (ch - 1) as u8, fmt, &tx.cfg.stream, tx.counter);
                                    p.extend_from_slice(&obuf[..n]);
                                    tx.out(p);
                                }
                            }
                        }
                    }
                    al.drain(..frame);
                    ar.drain(..frame);
                }
            }
            if win_t.elapsed() >= Duration::from_secs(1) {
                kbps = (tx.bytes - win_b) as f32 * 8.0 / 1000.0 / win_t.elapsed().as_secs_f32();
                win_b = tx.bytes;
                win_t = Instant::now();
            }
            if last_status.elapsed() >= Duration::from_millis(400) {
                last_status = Instant::now();
                let _ = kbps_cfg;
                *stats.lock().unwrap() = format!(
                    "sending '{}' to {} | {} | {:.0} kbps | {} packets{}",
                    stream,
                    addr,
                    name.trim(),
                    kbps,
                    tx.pkts,
                    format!("{}{}", if tx.errs > 0 { format!(" | {} send errors", tx.errs) } else { String::new() }, if tx.clips > 0 { format!(" | summed channels clipped {} samples", tx.clips) } else { String::new() })
                );
            }
        }
    });
    Some(Stop::new(stop, h))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn header_roundtrip() {
        let mut p = build_hdr(b"OBAN", 3, 239, 1, 0x40, "Stream1", 77);
        p.extend_from_slice(&[1, 2, 3]);
        let h = parse_hdr(&p).unwrap();
        assert!(h.oban);
        assert_eq!((h.sub, h.sr_idx, h.nbs, h.nbc, h.fmt, h.counter), (0, 3, 239, 1, 0x40, 77));
        assert_eq!(h.name, "Stream1");
        assert!(parse_hdr(&p[..20]).is_none());
        assert!(parse_hdr(b"XXXXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").is_none());
    }

    #[test]
    fn vban_and_oban_pcm_differ_only_in_magic() {
        let l = [0.1f32, -0.2, 0.3];
        let mut a = build_hdr(b"VBAN", 3, 2, 1, 1, "S", 5);
        let mut b = build_hdr(b"OBAN", 3, 2, 1, 1, "S", 5);
        pcm_pack(1, 2, &l, &l, &mut a);
        pcm_pack(1, 2, &l, &l, &mut b);
        assert_eq!(&a[4..], &b[4..]);
        assert_eq!(&a[0..4], b"VBAN");
    }

    #[test]
    fn closed_codecs_are_recognised_not_decoded() {
        assert_eq!(codec_of(false, 0x10), Codec::VbCa);
        assert_eq!(codec_of(true, 0x20), Codec::VbCv);
        assert_eq!(codec_of(true, 0xF0), Codec::UserCodec);
        assert_eq!(codec_of(false, 0x40), Codec::Unknown(0x40)); // our IDs only exist under the OBAN magic
        assert_eq!(codec_of(true, 0x40), Codec::OpusMusic);
    }

    #[test]
    fn pcm_formats_roundtrip() {
        let l: Vec<f32> = (0..64).map(|i| (i as f32 / 64.0 - 0.5) * 0.9).collect();
        for dt in [1u8, 2, 3, 4, 5] {
            let mut out = Vec::new();
            pcm_pack(dt, 2, &l, &l, &mut out);
            let (a, _) = pcm_decode(dt, 2, &out).unwrap();
            for i in 0..64 {
                assert!((a[i] - l[i]).abs() < 1e-4, "dt {dt} i {i}");
            }
        }
        assert!(pcm_decode(6, 2, &[0u8; 16]).is_none());
        assert!(pcm_decode(1, 0, &[0u8; 16]).is_none());
        assert!(pcm_decode(1, 2, &[0u8; 3]).is_none());
    }

    #[test]
    fn ping_reply_is_not_larger_than_a_real_request() {
        let req = parse_hdr(&build_hdr(b"VBAN", 0x60, 0, 0, 0, "VBAN SPOT PING", 9)).unwrap();
        let r = ping_reply(&req);
        assert_eq!(r.len(), HDR + 676);
        assert_eq!(r[5], 0x80);
        assert_eq!(&r[24..28], &9u32.to_le_bytes());
    }

    #[test]
    fn parser_survives_garbage() {
        let mut x = 99u32;
        for len in 0..200usize {
            let mut d: Vec<u8> = (0..len)
                .map(|_| {
                    x = x.wrapping_mul(1664525).wrapping_add(1013904223);
                    (x >> 24) as u8
                })
                .collect();
            if len > 4 {
                d[..4].copy_from_slice(if len % 2 == 0 { b"VBAN" } else { b"OBAN" });
            }
            if let Some(h) = parse_hdr(&d) {
                if h.sub == 0 && len > HDR {
                    let _ = pcm_decode(h.fmt & 7, h.nbc as usize + 1, &d[HDR..]);
                    let _ = ll_decode(&d[HDR..], h.nbs as usize + 1, h.nbc as usize + 1, 16);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests_multichannel {
    use super::*;

    #[test]
    fn n_channel_roundtrip_keeps_every_channel() {
        for nch in [1usize, 2, 3, 6, 8] {
            for dt in [1u8, 2, 3, 4] {
                let chans: Vec<Vec<f32>> = (0..nch).map(|c| (0..32).map(|i| ((c + 1) as f32 * 0.1) * if i % 2 == 0 { 1.0 } else { -1.0 }).collect()).collect();
                let refs: Vec<&[f32]> = chans.iter().map(|c| c.as_slice()).collect();
                let mut out = Vec::new();
                assert_eq!(pcm_pack_n(dt, &refs, &mut out), 0);
                let back = pcm_decode_n(dt, nch, &out).unwrap();
                assert_eq!(back.len(), nch);
                for c in 0..nch {
                    for i in 0..32 {
                        assert!((back[c][i] - chans[c][i]).abs() < 1e-3, "nch {nch} dt {dt} ch {c}");
                    }
                }
            }
        }
    }

    #[test]
    fn more_than_eight_channels_keep_the_first_eight_and_clips_are_counted() {
        let payload = vec![0u8; 2 * 10 * 4];
        assert_eq!(pcm_decode_n(1, 10, &payload).unwrap().len(), MAX_NET_CH);
        let hot = [1.5f32, -2.0];
        let mut out = Vec::new();
        assert_eq!(pcm_pack_n(1, &[&hot, &hot], &mut out), 4);
        assert!(pcm_decode_n(6, 2, &[0u8; 16]).is_none());
    }
}
