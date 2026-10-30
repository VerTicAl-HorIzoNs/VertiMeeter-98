//! Real audio devices for the soft backend (Windows: WASAPI through cpal).
//!
//! A manager thread owns every cpal `Stream` (they are not `Send` on every platform) and reconciles the wanted
//! routing (strip slot -> capture device, bus -> playback device) with the streams that are open, every 2 s or when
//! the control socket changes something. A stream that died (device unplugged) is reopened the same way.
//! Windows only: "Loopback: <playback device>" records what that device plays (WASAPI loopback).
use crate::params::*;
use crate::soft::*;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, Sample};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering::*};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::Duration;

const LOOP: &str = "Loopback: ";
/// WASAPI can record what a playback device plays; other hosts cannot
const LOOPBACK: bool = cfg!(windows);
const DEFAULT: &str = "@default";

struct Want {
    inp: Vec<String>,
    out: Vec<String>,
}
struct Shared {
    want: Mutex<Want>,
    wake: Condvar,
    dirty: AtomicBool,
    status: Mutex<HashMap<String, String>>, // device name -> last error ("" = open)
    names: Mutex<(Vec<String>, Vec<String>, String, String)>, // inputs, outputs, default in, default out
}
fn shared() -> &'static Shared {
    static S: OnceLock<Shared> = OnceLock::new();
    S.get_or_init(|| Shared {
        want: Mutex::new(Want { inp: vec![String::new(); MAX_IN], out: vec![String::new(); MAX_BUS] }),
        wake: Condvar::new(),
        dirty: AtomicBool::new(false),
        status: Mutex::new(HashMap::new()),
        names: Mutex::new((vec![], vec![], String::new(), String::new())),
    })
}

struct Dev {
    name: String,
    dev: cpal::Device,
    looped: bool,
}

fn unique(names: &mut Vec<String>, n: String) -> String {
    let mut cand = n.clone();
    let mut k = 2;
    while names.contains(&cand) {
        cand = format!("{n} ({k})");
        k += 1;
    }
    names.push(cand.clone());
    cand
}

fn enumerate() -> (Vec<Dev>, Vec<Dev>) {
    let host = cpal::default_host();
    let (mut ins, mut outs) = (vec![], vec![]);
    let mut seen = vec![];
    if let Ok(it) = host.input_devices() {
        for d in it {
            if let Ok(n) = d.name() {
                let name = unique(&mut seen, n);
                ins.push(Dev { name, dev: d, looped: false });
            }
        }
    }
    let mut seen = vec![];
    if let Ok(it) = host.output_devices() {
        for d in it {
            if let Ok(n) = d.name() {
                let name = unique(&mut seen, n);
                if LOOPBACK {
                    ins.push(Dev { name: format!("{LOOP}{name}"), dev: d.clone(), looped: true });
                }
                outs.push(Dev { name, dev: d, looped: false });
            }
        }
    }
    (ins, outs)
}

// ---- streams ------------------------------------------------------------------------------------------------
type Subs = Arc<Mutex<Vec<Arc<AdRing>>>>;

struct InStream {
    _s: cpal::Stream,
    rate: u32,
    subs: Subs,
    dead: Arc<AtomicBool>,
}
struct OutStream {
    _s: cpal::Stream,
    ring: Arc<AdRing>,
    dead: Arc<AtomicBool>,
}

fn push_in<T: Sample>(d: &[T], ch: usize, subs: &Subs, l: &mut Vec<f32>, r: &mut Vec<f32>)
where
    f32: FromSample<T>,
{
    let frames = d.len() / ch.max(1);
    l.clear();
    r.clear();
    for f in 0..frames {
        let a = f32::from_sample(d[f * ch]);
        let b = if ch > 1 { f32::from_sample(d[f * ch + 1]) } else { a };
        l.push(a);
        r.push(b);
    }
    if let Ok(g) = subs.try_lock() {
        for ring in g.iter() {
            ring.push(l, r);
        }
    }
}

fn build_in(dev: &cpal::Device, looped: bool, subs: Subs, dead: Arc<AtomicBool>) -> Result<(cpal::Stream, u32), String> {
    let cfg = if looped { dev.default_output_config() } else { dev.default_input_config() }.map_err(|e| e.to_string())?;
    let ch = cfg.channels() as usize;
    let rate = cfg.sample_rate().0;
    let sc: cpal::StreamConfig = cfg.config();
    let err = {
        let dead = dead.clone();
        move |e: cpal::StreamError| {
            eprintln!("capture stream error: {e}");
            dead.store(true, Relaxed);
        }
    };
    macro_rules! mk {
        ($t:ty) => {{
            let (mut l, mut r) = (Vec::with_capacity(8192), Vec::with_capacity(8192));
            let subs = subs.clone();
            dev.build_input_stream(&sc, move |d: &[$t], _: &cpal::InputCallbackInfo| push_in(d, ch, &subs, &mut l, &mut r), err, None)
        }};
    }
    let s = match cfg.sample_format() {
        cpal::SampleFormat::F32 => mk!(f32),
        cpal::SampleFormat::I16 => mk!(i16),
        cpal::SampleFormat::U16 => mk!(u16),
        cpal::SampleFormat::I32 => mk!(i32),
        f => return Err(format!("unsupported sample format {f:?}")),
    }
    .map_err(|e| e.to_string())?;
    s.play().map_err(|e| e.to_string())?;
    Ok((s, rate))
}

fn pull_out<T: Sample + FromSample<f32>>(
    data: &mut [T], ch: usize, ring: &AdRing, rd: &mut Rd, nominal: f64, io: &Io, l: &mut [f32], r: &mut [f32], p: &Params,
) {
    let target = io.out_target();
    let frames = data.len() / ch.max(1);
    let mut done = 0;
    while done < frames {
        let n = (frames - done).min(l.len());
        if ring.pull(rd, &mut l[..n], &mut r[..n], nominal, target) {
            p.xruns.fetch_add(1, Relaxed);
        }
        for i in 0..n {
            let o = (done + i) * ch;
            data[o] = T::from_sample(l[i]);
            if ch > 1 {
                data[o + 1] = T::from_sample(r[i]);
                for c in 2..ch {
                    data[o + c] = T::EQUILIBRIUM;
                }
            }
        }
        done += n;
    }
}

fn build_out(dev: &cpal::Device, ring: Arc<AdRing>, dead: Arc<AtomicBool>, p: Arc<Params>, io: Arc<Io>) -> Result<cpal::Stream, String> {
    let cfg = dev.default_output_config().map_err(|e| e.to_string())?;
    let ch = cfg.channels() as usize;
    let nominal = RATE as f64 / cfg.sample_rate().0 as f64;
    let sc: cpal::StreamConfig = cfg.config();
    let err = {
        let dead = dead.clone();
        move |e: cpal::StreamError| {
            eprintln!("playback stream error: {e}");
            dead.store(true, Relaxed);
        }
    };
    macro_rules! mk {
        ($t:ty) => {{
            let (mut l, mut r) = (vec![0.0f32; 2048], vec![0.0f32; 2048]);
            let (ring, p, io) = (ring.clone(), p.clone(), io.clone());
            let mut rd = Rd::new();
            dev.build_output_stream(
                &sc,
                move |d: &mut [$t], _: &cpal::OutputCallbackInfo| pull_out(d, ch, &ring, &mut rd, nominal, &io, &mut l, &mut r, &p),
                err,
                None,
            )
        }};
    }
    let s = match cfg.sample_format() {
        cpal::SampleFormat::F32 => mk!(f32),
        cpal::SampleFormat::I16 => mk!(i16),
        cpal::SampleFormat::U16 => mk!(u16),
        cpal::SampleFormat::I32 => mk!(i32),
        f => return Err(format!("unsupported sample format {f:?}")),
    }
    .map_err(|e| e.to_string())?;
    s.play().map_err(|e| e.to_string())?;
    Ok(s)
}

// ---- manager -------------------------------------------------------------------------------------------------
fn resolve(want: &str, list: &[Dev], default: &str) -> Option<usize> {
    let w = if want == DEFAULT { default } else { want };
    list.iter().position(|d| d.name == w)
}

fn manager(p: Arc<Params>, io: Arc<Io>) {
    let sh = shared();
    let mut open_in: HashMap<String, InStream> = HashMap::new();
    let mut open_out: HashMap<String, OutStream> = HashMap::new();
    let mut slot_sub: Vec<Option<(String, Arc<AdRing>)>> = (0..MAX_IN).map(|_| None).collect();
    let mut bus_sub: Vec<Option<String>> = vec![None; MAX_BUS];
    let host = cpal::default_host();
    loop {
        sh.dirty.store(false, SeqCst);
        let (ins, outs) = enumerate();
        let def_in = host.default_input_device().and_then(|d| d.name().ok()).unwrap_or_default();
        let def_out = host.default_output_device().and_then(|d| d.name().ok()).unwrap_or_default();
        *sh.names.lock().unwrap() = (
            ins.iter().map(|d| d.name.clone()).collect(),
            outs.iter().map(|d| d.name.clone()).collect(),
            def_in.clone(),
            def_out.clone(),
        );
        let (want_in, want_out) = {
            let w = sh.want.lock().unwrap();
            (w.inp.clone(), w.out.clone())
        };
        let mut status = HashMap::new();
        // drop dead streams so they get reopened
        open_in.retain(|_, s| !s.dead.load(Relaxed));
        open_out.retain(|_, s| !s.dead.load(Relaxed));
        // ---- capture
        for slot in 0..MAX_IN {
            let name = match resolve(&want_in[slot], &ins, &def_in) {
                Some(i) => ins[i].name.clone(),
                None => {
                    if !want_in[slot].is_empty() {
                        status.insert(want_in[slot].clone(), "missing".to_string());
                    }
                    String::new()
                }
            };
            let cur = slot_sub[slot].as_ref().map(|(n, r)| (n.clone(), r.clone()));
            let keep = matches!(&cur, Some((n, _)) if *n == name && open_in.contains_key(n));
            if keep {
                continue;
            }
            if let Some((n, r)) = cur {
                if let Some(s) = open_in.get(&n) {
                    s.subs.lock().unwrap().retain(|x| !Arc::ptr_eq(x, &r));
                }
            }
            slot_sub[slot] = None;
            *io.inp[slot].lock().unwrap() = None;
            if name.is_empty() {
                continue;
            }
            if !open_in.contains_key(&name) {
                let d = &ins[ins.iter().position(|d| d.name == name).unwrap()];
                let (subs, dead): (Subs, _) = (Arc::new(Mutex::new(vec![])), Arc::new(AtomicBool::new(false)));
                match build_in(&d.dev, d.looped, subs.clone(), dead.clone()) {
                    Ok((s, rate)) => {
                        open_in.insert(name.clone(), InStream { _s: s, rate, subs, dead });
                    }
                    Err(e) => {
                        status.insert(name.clone(), e);
                        continue;
                    }
                }
            }
            let st = open_in.get(&name).unwrap();
            let ring = AdRing::new();
            st.subs.lock().unwrap().push(ring.clone());
            *io.inp[slot].lock().unwrap() = Some(InRoute { ring: ring.clone(), nominal: st.rate as f64 / RATE as f64, rate: st.rate });
            slot_sub[slot] = Some((name.clone(), ring));
            status.entry(name).or_insert_with(String::new);
        }
        // streams nobody uses any more
        let used: Vec<String> = slot_sub.iter().flatten().map(|(n, _)| n.clone()).collect();
        open_in.retain(|n, _| used.contains(n));
        // ---- playback
        for b in 0..MAX_BUS {
            let name = match resolve(&want_out[b], &outs, &def_out) {
                Some(i) => outs[i].name.clone(),
                None => {
                    if !want_out[b].is_empty() {
                        status.insert(want_out[b].clone(), "missing".to_string());
                    }
                    String::new()
                }
            };
            if bus_sub[b].as_deref() == Some(name.as_str()) && (name.is_empty() || open_out.contains_key(&name)) {
                continue;
            }
            bus_sub[b] = None;
            *io.outp[b].lock().unwrap() = None;
            if name.is_empty() {
                continue;
            }
            if !open_out.contains_key(&name) {
                let d = &outs[outs.iter().position(|d| d.name == name).unwrap()];
                let (ring, dead) = (AdRing::new(), Arc::new(AtomicBool::new(false)));
                match build_out(&d.dev, ring.clone(), dead.clone(), p.clone(), io.clone()) {
                    Ok(s) => {
                        open_out.insert(name.clone(), OutStream { _s: s, ring, dead });
                    }
                    Err(e) => {
                        status.insert(name.clone(), e);
                        continue;
                    }
                }
            }
            *io.outp[b].lock().unwrap() = Some(open_out.get(&name).unwrap().ring.clone());
            bus_sub[b] = Some(name.clone());
            status.entry(name).or_insert_with(String::new);
        }
        let used: Vec<String> = bus_sub.iter().flatten().cloned().collect();
        open_out.retain(|n, _| used.contains(n));
        *sh.status.lock().unwrap() = status;
        // sleep until something changes (or re-check the hardware in 2 s)
        let g = sh.want.lock().unwrap();
        if !sh.dirty.load(SeqCst) {
            let _ = sh.wake.wait_timeout(g, Duration::from_secs(2));
        }
    }
}

pub fn start(p: Arc<Params>, io: Arc<Io>) {
    shared();
    std::thread::Builder::new().name("vm-devices".into()).spawn(move || manager(p, io)).expect("device thread");
}

/// dev_list / dev_in / dev_out / dev_status
pub fn handle(c: &str, v: &Value) -> Value {
    let sh = shared();
    let idx = v.get("i").and_then(|x| x.as_u64()).unwrap_or(u64::MAX) as usize;
    let name = v.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
    match c {
        "dev_list" => {
            let n = sh.names.lock().unwrap();
            json!({"ok":true,"backend":"cpal","inputs":n.0,"outputs":n.1,"default_in":n.2,"default_out":n.3})
        }
        "dev_in" if idx < MAX_IN => {
            sh.want.lock().unwrap().inp[idx] = name;
            sh.dirty.store(true, SeqCst);
            sh.wake.notify_all();
            json!({"ok":true})
        }
        "dev_out" if idx < MAX_BUS => {
            sh.want.lock().unwrap().out[idx] = name;
            sh.dirty.store(true, SeqCst);
            sh.wake.notify_all();
            json!({"ok":true})
        }
        "dev_status" => {
            let s = sh.status.lock().unwrap();
            json!({"ok":true,"status":*s})
        }
        _ => json!({"ok":false,"err":"bad device command or index"}),
    }
}
