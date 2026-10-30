//! Software backend: the mixer runs on a thread of its own instead of inside a JACK callback.
//!
//! Ports are plain buffers. Hardware (cpal / WASAPI) talks to the mixer only through `AdRing`s: lock-free stereo rings
//! whose consumer resamples (linear) and trims its speed by the ring fill level, so a device whose clock differs from
//! 48 kHz (or from the other devices) neither starves nor piles up latency.
//!
//! Clock: the first bus that has an output device is the clock. The mixer produces a block whenever that device's ring
//! has room, so the device's own hardware clock paces the whole engine. With no output device a timer paces it.
use crate::mix::Mixer;
use crate::params::*;
use std::cell::UnsafeCell;
use std::marker::PhantomData;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::*};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const RATE: u32 = 48000; // the engine always runs at 48 kHz (devices are resampled)
pub const BLOCK: usize = 240; // 5 ms per mixer block
const RCAP: usize = 1 << 16;
const RMASK: usize = RCAP - 1;

// ---- the six names mix.rs needs -------------------------------------------------------------------------
pub struct AudioIn;
pub struct AudioOut;
pub struct Client;
pub enum Control {
    Continue,
}
pub struct ProcessScope {
    n: u32,
}
impl ProcessScope {
    pub fn n_frames(&self) -> u32 {
        self.n
    }
}
pub trait ProcessHandler {
    fn process(&mut self, c: &Client, ps: &ProcessScope) -> Control;
}

pub struct Buf(UnsafeCell<Vec<f32>>);
unsafe impl Sync for Buf {}
unsafe impl Send for Buf {}
impl Buf {
    fn new() -> Arc<Buf> {
        Arc::new(Buf(UnsafeCell::new(zeros(BLOCK * 4))))
    }
    /// Only the mixer thread touches port buffers, so handing out slices from a shared reference is fine.
    #[allow(clippy::mut_from_ref)]
    unsafe fn all(&self) -> &mut [f32] {
        &mut *self.0.get()
    }
}
pub struct Port<T> {
    b: Arc<Buf>,
    _t: PhantomData<T>,
}
impl<T> Port<T> {
    fn new() -> (Self, Arc<Buf>) {
        let b = Buf::new();
        (Port { b: b.clone(), _t: PhantomData }, b)
    }
}
impl Port<AudioIn> {
    pub fn as_slice(&self, ps: &ProcessScope) -> &[f32] {
        unsafe { &self.b.all()[..ps.n as usize] }
    }
}
impl Port<AudioOut> {
    pub fn as_mut_slice(&mut self, ps: &ProcessScope) -> &mut [f32] {
        unsafe { &mut self.b.all()[..ps.n as usize] }
    }
}

// ---- adaptive ring ----------------------------------------------------------------------------------------
pub struct AdRing {
    l: UnsafeCell<Vec<f32>>,
    r: UnsafeCell<Vec<f32>>,
    head: AtomicUsize,
    tail: AtomicUsize,
    /// set on the ring of the clock device: its consumer must not trim its speed (the mixer follows it instead)
    pub master: AtomicBool,
}
unsafe impl Sync for AdRing {}
unsafe impl Send for AdRing {}

pub struct Rd {
    pos: f64,
    ratio: f64,
    primed: bool,
}
impl Rd {
    pub fn new() -> Self {
        Rd { pos: 0.0, ratio: 0.0, primed: false }
    }
}

impl AdRing {
    pub fn new() -> Arc<Self> {
        Arc::new(AdRing {
            l: UnsafeCell::new(zeros(RCAP)),
            r: UnsafeCell::new(zeros(RCAP)),
            head: AtomicUsize::new(0),
            tail: AtomicUsize::new(0),
            master: AtomicBool::new(false),
        })
    }
    pub fn len(&self) -> usize {
        self.head.load(Acquire).wrapping_sub(self.tail.load(Acquire))
    }
    pub fn tail(&self) -> usize {
        self.tail.load(Relaxed)
    }
    /// producer. Drops what does not fit.
    pub fn push(&self, l: &[f32], r: &[f32]) {
        let h = self.head.load(Relaxed);
        let free = RCAP - h.wrapping_sub(self.tail.load(Acquire));
        let n = l.len().min(r.len()).min(free);
        let (bl, br) = unsafe { (&mut *self.l.get(), &mut *self.r.get()) };
        for i in 0..n {
            bl[(h + i) & RMASK] = l[i];
            br[(h + i) & RMASK] = r[i];
        }
        self.head.store(h.wrapping_add(n), Release);
    }
    /// consumer: n output frames at `nominal` source frames per output frame, trimmed by the fill level.
    /// Returns true when the ring ran dry while it was playing (an underrun).
    pub fn pull(&self, st: &mut Rd, ol: &mut [f32], or_: &mut [f32], nominal: f64, target: usize) -> bool {
        let n = ol.len().min(or_.len());
        let mut t = self.tail.load(Relaxed);
        let mut avail = self.head.load(Acquire).wrapping_sub(t);
        if st.ratio == 0.0 {
            st.ratio = nominal;
        }
        let target = target.max(64);
        if avail > target * 4 + 2048 {
            // far too full (the other clock runs fast, or the stream stalled): jump back to the target, keeps latency bounded
            let drop = avail - target;
            t = t.wrapping_add(drop);
            avail -= drop;
            self.tail.store(t, Release);
        }
        if !st.primed {
            if avail >= target {
                st.primed = true;
                st.pos = 0.0;
            } else {
                ol[..n].fill(0.0);
                or_[..n].fill(0.0);
                return false;
            }
        }
        let want = if self.master.load(Relaxed) {
            nominal
        } else {
            let err = ((avail as f64 - target as f64) / target as f64).clamp(-1.0, 1.0);
            nominal * (1.0 + err * 0.02)
        };
        st.ratio += (want - st.ratio) * 0.2;
        let (bl, br) = unsafe { (&*self.l.get(), &*self.r.get()) };
        let mut pos = st.pos;
        for k in 0..n {
            let i = pos as usize;
            if i + 1 >= avail {
                ol[k..n].fill(0.0);
                or_[k..n].fill(0.0);
                self.tail.store(t.wrapping_add(avail), Release);
                st.primed = false;
                st.pos = 0.0;
                return true;
            }
            let f = (pos - i as f64) as f32;
            let (a, b) = ((t + i) & RMASK, (t + i + 1) & RMASK);
            ol[k] = bl[a] + (bl[b] - bl[a]) * f;
            or_[k] = br[a] + (br[b] - br[a]) * f;
            pos += st.ratio;
        }
        let used = pos as usize;
        self.tail.store(t.wrapping_add(used), Release);
        st.pos = pos - used as f64;
        false
    }
}

// ---- routes between devices and the mixer -----------------------------------------------------------------
pub struct InRoute {
    pub ring: Arc<AdRing>,
    pub nominal: f64, // device rate / 48000
    pub rate: u32,    // device rate in Hz (the buffer target follows the live latency setting)
}
pub struct Io {
    pub inp: Vec<Mutex<Option<InRoute>>>,        // per strip slot
    pub outp: Vec<Mutex<Option<Arc<AdRing>>>>,  // per bus: the output device ring it is summed into
    pub lat_ms: AtomicUsize,
}
impl Io {
    pub fn new() -> Arc<Self> {
        let lat_ms = std::env::var("VERTIMEETER_LAT_MS").ok().and_then(|s| s.parse().ok()).unwrap_or(40usize).clamp(10, 500);
        Arc::new(Io {
            inp: (0..MAX_IN).map(|_| Mutex::new(None)).collect(),
            outp: (0..MAX_BUS).map(|_| Mutex::new(None)).collect(),
            lat_ms: AtomicUsize::new(lat_ms),
        })
    }
    /// frames (at 48 kHz) the output rings are kept filled to
    pub fn out_target(&self) -> usize {
        self.lat_ms.load(Relaxed) * RATE as usize / 1000
    }
    /// frames (at the device rate) a capture ring is kept filled to: a bit less than the playback buffer
    pub fn in_target(&self, rate: u32) -> usize {
        (self.lat_ms.load(Relaxed) * 3 / 4).max(10) * rate as usize / 1000
    }
}

static IO: OnceLock<Arc<Io>> = OnceLock::new();
/// live playback buffer (ms): bigger = safer, smaller = lower latency
pub fn set_latency(ms: usize) {
    if let Some(io) = IO.get() {
        io.lat_ms.store(ms.clamp(10, 500), Relaxed);
    }
}

// ---- OS helpers -------------------------------------------------------------------------------------------
#[cfg(windows)]
mod os {
    #[link(name = "winmm")]
    extern "system" {
        pub fn timeBeginPeriod(u: u32) -> u32;
    }
    #[link(name = "avrt")]
    extern "system" {
        pub fn AvSetMmThreadCharacteristicsW(task: *const u16, idx: *mut u32) -> isize;
    }
    extern "system" {
        pub fn GetCurrentThread() -> isize;
        pub fn SetThreadPriority(h: isize, p: i32) -> i32;
    }
}
pub fn timer_resolution() {
    #[cfg(windows)]
    unsafe {
        os::timeBeginPeriod(1); // 1 ms sleeps instead of ~15 ms
    }
}
pub fn realtime_thread() {
    #[cfg(windows)]
    unsafe {
        let task: Vec<u16> = "Pro Audio\0".encode_utf16().collect();
        let mut idx = 0u32;
        if os::AvSetMmThreadCharacteristicsW(task.as_ptr(), &mut idx) == 0 {
            os::SetThreadPriority(os::GetCurrentThread(), 15); // TIME_CRITICAL when MMCSS is not available
        }
    }
}

// ---- the mixer thread -------------------------------------------------------------------------------------
struct Handles {
    ins: Vec<(Arc<Buf>, Arc<Buf>)>,
    outs: Vec<(Arc<Buf>, Arc<Buf>)>,
    ins_out: Vec<(Arc<Buf>, Arc<Buf>)>,
    ins_in: Vec<(Arc<Buf>, Arc<Buf>)>,
}

fn pair<T>(v: &mut Vec<(Port<T>, Port<T>)>, h: &mut Vec<(Arc<Buf>, Arc<Buf>)>) {
    let (a, ha) = Port::<T>::new();
    let (b, hb) = Port::<T>::new();
    v.push((a, b));
    h.push((ha, hb));
}

fn mixer_thread(p: Arc<Params>, io: Arc<Io>, mut mixer: Mixer, h: Handles) {
    realtime_thread();
    let client = Client;
    let ps = ProcessScope { n: BLOCK as u32 };
    let mut rd: Vec<Rd> = (0..MAX_IN).map(|_| Rd::new()).collect();
    let mut last_in: Vec<Option<Arc<AdRing>>> = (0..MAX_IN).map(|_| None).collect();
    let mut sl = vec![0.0f32; BLOCK];
    let mut sr = vec![0.0f32; BLOCK];
    let mut next = Instant::now();
    let (mut stall_tail, mut stall_since) = (usize::MAX, Instant::now());
    let blk = Duration::from_secs_f64(BLOCK as f64 / RATE as f64);
    loop {
        let target = io.out_target();
        // ---- pace: the clock device's ring has room, or a timer when there is no device / it stopped
        let mut clock: Option<Arc<AdRing>> = None;
        for b in 0..MAX_BUS {
            if let Ok(g) = io.outp[b].lock() {
                if let Some(r) = g.as_ref() {
                    clock = Some(r.clone());
                    break;
                }
            }
        }
        let mut paced_by_ring = false;
        if let Some(c) = &clock {
            c.master.store(true, Relaxed);
            let tl = c.tail();
            if tl != stall_tail {
                stall_tail = tl;
                stall_since = Instant::now();
            }
            if stall_since.elapsed() < Duration::from_millis(250) {
                paced_by_ring = true;
                if c.len() >= target {
                    std::thread::sleep(Duration::from_micros(800));
                    continue;
                }
            }
        }
        if !paced_by_ring {
            let now = Instant::now();
            if now > next + Duration::from_millis(100) {
                next = now;
            }
            if next > now {
                std::thread::sleep(next - now);
            }
            next += blk;
        } else {
            next = Instant::now();
        }

        // ---- inputs: device rings -> strip port buffers
        for i in 0..MAX_IN {
            let route = io.inp[i].lock().ok().and_then(|g| g.as_ref().map(|r| (r.ring.clone(), r.nominal, io.in_target(r.rate))));
            let (l, r) = (unsafe { &mut h.ins[i].0.all()[..BLOCK] }, unsafe { &mut h.ins[i].1.all()[..BLOCK] });
            match route {
                Some((ring, nominal, tgt)) => {
                    if !last_in[i].as_ref().map_or(false, |x| Arc::ptr_eq(x, &ring)) {
                        rd[i] = Rd::new();
                        last_in[i] = Some(ring.clone());
                    }
                    if ring.pull(&mut rd[i], l, r, nominal, tgt) {
                        p.xruns.fetch_add(1, Relaxed);
                    }
                }
                None => {
                    last_in[i] = None;
                    l.fill(0.0);
                    r.fill(0.0);
                }
            }
        }
        // RET inputs have no source here
        mixer.process(&client, &ps);

        // ---- outputs: sum the buses that share a device into that device's ring
        let mut groups: Vec<(Arc<AdRing>, Vec<usize>)> = Vec::new();
        for b in 0..MAX_BUS {
            let r = io.outp[b].lock().ok().and_then(|g| g.clone());
            if let Some(r) = r {
                match groups.iter_mut().find(|(g, _)| Arc::ptr_eq(g, &r)) {
                    Some((_, v)) => v.push(b),
                    None => groups.push((r, vec![b])),
                }
            }
        }
        for (ring, buses) in &groups {
            if clock.as_ref().map_or(true, |c| !Arc::ptr_eq(ring, c)) {
                ring.master.store(false, Relaxed);
            }
            sl.fill(0.0);
            sr.fill(0.0);
            for &b in buses {
                let (l, r) = (unsafe { &h.outs[b].0.all()[..BLOCK] }, unsafe { &h.outs[b].1.all()[..BLOCK] });
                for k in 0..BLOCK {
                    sl[k] += l[k];
                    sr[k] += r[k];
                }
            }
            ring.push(&sl, &sr);
        }
        // insert loops have no external effect on this backend: pass the signal straight back (one block late)
        for k in 0..h.ins_out.len() {
            let (a, b) = unsafe { (&h.ins_out[k].0.all()[..BLOCK], &h.ins_out[k].1.all()[..BLOCK]) };
            unsafe {
                h.ins_in[k].0.all()[..BLOCK].copy_from_slice(a);
                h.ins_in[k].1.all()[..BLOCK].copy_from_slice(b);
            }
        }
    }
}

/// Starts the mixer thread, the device manager (cpal-backend) and the control server. Blocks.
pub fn run(spec: &str) {
    timer_resolution();
    let p = Arc::new(Params::new());
    p.sample_rate.store(RATE, Relaxed);
    p.buffer.store(BLOCK as u32, Relaxed);
    let mut h = Handles { ins: vec![], outs: vec![], ins_out: vec![], ins_in: vec![] };
    let (mut ins, mut outs, mut ext, mut ret, mut ins_out, mut ins_in) = (vec![], vec![], vec![], vec![], vec![], vec![]);
    let mut scrap: Vec<(Arc<Buf>, Arc<Buf>)> = vec![];
    for _ in 0..MAX_IN {
        pair::<AudioIn>(&mut ins, &mut h.ins);
    }
    for _ in 0..MAX_BUS {
        pair::<AudioOut>(&mut outs, &mut h.outs);
    }
    for _ in 0..2 {
        pair::<AudioOut>(&mut ext, &mut scrap);
        pair::<AudioIn>(&mut ret, &mut scrap);
    }
    for _ in 0..N_STRIPS + 1 {
        pair::<AudioOut>(&mut ins_out, &mut h.ins_out);
        pair::<AudioIn>(&mut ins_in, &mut h.ins_in);
    }
    let mixer = Mixer::new(p.clone(), ins, outs, ext, ret, ins_out, ins_in, RATE as f32);
    let io = Io::new();
    let _ = IO.set(io.clone());
    {
        let (p, io) = (p.clone(), io.clone());
        std::thread::Builder::new().name("vm-mixer".into()).spawn(move || mixer_thread(p, io, mixer, h)).expect("mixer thread");
    }
    #[cfg(feature = "cpal-backend")]
    crate::cpal_io::start(p.clone(), io.clone());
    eprintln!("engine up: {} Hz, {} frames, soft backend{}, control {}", RATE, BLOCK,
              if cfg!(feature = "cpal-backend") { " + cpal devices" } else { " (no devices)" }, spec);
    crate::ctl::serve(spec, p);
}
