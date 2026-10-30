//! Line-delimited JSON control over a unix socket (Linux) or an authenticated loopback TCP port (Windows). One reply line per request.
use crate::params::*;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::{Arc, Mutex, OnceLock};
use std::net::{IpAddr, ToSocketAddrs};

/// VBAN worker handles (control thread only; the audio thread never touches these)
struct NetState {
    rx: [Option<crate::net::Stop>; N_NET_RX],
    tx: [Option<crate::net::Stop>; N_NET_TX],
    rx_stat: [Arc<Mutex<String>>; N_NET_RX],
    tx_stat: [Arc<Mutex<String>>; N_NET_TX],
}
fn net() -> &'static Mutex<NetState> {
    static N: OnceLock<Mutex<NetState>> = OnceLock::new();
    N.get_or_init(|| {
        Mutex::new(NetState {
            rx: std::array::from_fn(|_| None),
            tx: std::array::from_fn(|_| None),
            rx_stat: std::array::from_fn(|_| Arc::new(Mutex::new(String::new()))),
            tx_stat: std::array::from_fn(|_| Arc::new(Mutex::new(String::new()))),
        })
    })
}
fn text<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(|x| x.as_str()).unwrap_or("")
}

/// Bind a receiver's feeds to strip slots (-1 = unused). feeds[i] = [left channel, right channel] of the stream (same twice = mono).
fn apply_rx_map(p: &Arc<Params>, k: usize, slots: &[i64], feeds: &[[u64; 2]]) {
    for i in 0..RX_FEEDS {
        let r = k * RX_FEEDS + i;
        let s = slots.get(i).copied().unwrap_or(-1);
        let s = if s >= 0 && (s as usize) < MAX_IN { s as i32 } else { -1 };
        let [a, b] = feeds.get(i).copied().unwrap_or([(2 * i as u64) % 8, (2 * i as u64 + 1) % 8]);
        p.net_rx_feed[r].store((a.min(7) as u32) | ((b.min(7) as u32) << 8), Relaxed);
        if s >= 0 && p.net_rx_slot[r].load(Relaxed) < 0 {
            p.net_rx[r].skip(usize::MAX); // stale audio from before the pair was bound
        }
        p.net_rx_slot[r].store(s, Relaxed);
    }
}

fn feeds_of(v: &Value) -> Vec<[u64; 2]> {
    v.get("feeds")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().map(|f| [f.get(0).and_then(|x| x.as_u64()).unwrap_or(0), f.get(1).and_then(|x| x.as_u64()).unwrap_or(1)]).collect())
        .unwrap_or_default()
}

fn u64s(v: &Value, key: &str) -> Vec<u64> {
    v.get(key).and_then(|x| x.as_array()).map(|a| a.iter().map(|x| x.as_u64().unwrap_or(0)).collect()).unwrap_or_default()
}

/// Output channel matrix of a multichannel sender: per channel a bus bitmask and a source mode.
fn apply_tx_map(p: &Arc<Params>, k: usize, mx: &[u64], src: &[u64]) {
    for c in 0..MAX_NET_CH {
        p.net_tx_mask[k][c].store((mx.get(c).copied().unwrap_or(0) & ((1u64 << MAX_BUS) - 1)) as u32, Relaxed);
        p.net_tx_src[k][c].store(src.get(c).copied().unwrap_or(0).min(3) as u32, Relaxed);
    }
}

fn num(v: &Value, k: &str) -> Option<f32> {
    v.get(k).and_then(|x| x.as_f64()).map(|x| x as f32)
}
fn flag(v: &Value, k: &str) -> Option<bool> {
    v.get(k).and_then(|x| x.as_bool())
}

fn handle(p: &Arc<Params>, v: &Value) -> Value {
    let c = v.get("c").and_then(|x| x.as_str()).unwrap_or("");
    let idx = v.get("i").and_then(|x| x.as_u64()).unwrap_or(0) as usize;
    match c {
        "ping" => json!({"ok":true}),
        "strip" if idx < MAX_IN => {
            let s = &p.strips[idx];
            if let Some(x) = num(v, "gain_db") { s.gain.set(db_to_lin(x)); }
            if let Some(x) = num(v, "pan") { s.pan.set(x.clamp(-1.0, 1.0)); }
            if let Some(x) = flag(v, "mute") { s.mute.store(x, Relaxed); }
            if let Some(x) = flag(v, "solo") { s.solo.store(x, Relaxed); }
            if let Some(x) = flag(v, "mono") { s.mono.store(x, Relaxed); }
            if let Some(x) = flag(v, "active") { s.active.store(x, Relaxed); }
            if let Some(x) = num(v, "dns") { s.dns.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = flag(v, "sp_on") { s.sp_on.store(x, Relaxed); }
            if let Some(x) = num(v, "sp_x") { s.sp_x.set(x.clamp(-1.0, 1.0)); }
            if let Some(x) = num(v, "sp_y") { s.sp_y.set(x.clamp(-1.0, 1.0)); }
            if let Some(x) = num(v, "rvb") { s.rvb_send.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = num(v, "dly") { s.dly_send.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = flag(v, "lim_on") { s.lim_on.store(x, Relaxed); }
            if let Some(x) = num(v, "lim_db") { s.lim_thr.set(db_to_lin(x.clamp(-40.0, 0.0))); }
            if let Some(x) = num(v, "ext1") { s.ext1.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = num(v, "ext2") { s.ext2.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = flag(v, "ext_post") { s.ext_post.store(x, Relaxed); }
            if let Some(x) = flag(v, "vm_on") { s.voice.on.store(x, Relaxed); }
            if let Some(x) = num(v, "vm_pitch") { s.voice.pitch.set(x.clamp(-12.0, 12.0)); }
            if let Some(x) = num(v, "vm_mix") { s.voice.mix.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = num(v, "vm_ring") { s.voice.ring.set(x.clamp(0.0, 2000.0)); }
            if let Some(x) = flag(v, "mod_on") { s.mod_on.store(x, Relaxed); }
            if let Some(x) = num(v, "mod_rate") { s.mod_rate.set(x.clamp(0.1, 30.0)); }
            if let Some(x) = num(v, "mod_depth") { s.mod_depth.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = flag(v, "ins_on") { s.ins_on.store(x, Relaxed); }
            if let Some(x) = num(v, "ins_mix") { s.ins_mix.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = v.get("kar").and_then(|x| x.as_u64()) { s.kar.store(x.min(3) as u32, Relaxed); }
            if let Some(arr) = v.get("sends").and_then(|x| x.as_array()) {
                for (b, on) in arr.iter().enumerate().take(MAX_BUS) {
                    s.send[b].store(on.as_bool().unwrap_or(false), Relaxed);
                }
            }
            if let Some(arr) = v.get("send_db").and_then(|x| x.as_array()) {
                for (b, g) in arr.iter().enumerate().take(MAX_BUS) {
                    s.send_gain[b].set(db_to_lin(g.as_f64().unwrap_or(0.0) as f32));
                }
            }
            json!({"ok":true})
        }
        "bus" if idx < MAX_BUS => {
            let b = &p.buses[idx];
            if let Some(x) = num(v, "gain_db") { b.gain.set(db_to_lin(x)); }
            if let Some(x) = flag(v, "mute") { b.mute.store(x, Relaxed); }
            if let Some(x) = flag(v, "mono") { b.mono.store(x, Relaxed); }
            if let Some(x) = num(v, "rvb_ret") { b.rvb_ret.set(x.clamp(0.0, 2.0)); }
            if let Some(x) = num(v, "dly_ret") { b.dly_ret.set(x.clamp(0.0, 2.0)); }
            if let Some(x) = num(v, "dly_ms") { b.dly_ms.set(x.clamp(0.0, 500.0)); }
            if let Some(x) = flag(v, "lim_on") { b.lim_on.store(x, Relaxed); }
            if let Some(x) = num(v, "lim_db") { b.lim_thr.set(db_to_lin(x.clamp(-40.0, 0.0))); }
            if let Some(x) = v.get("mode").and_then(|x| x.as_u64()) { b.mode.store(x.min(13) as u32, Relaxed); }
            if let Some(x) = flag(v, "c5_on") { b.c5.on.store(x, Relaxed); }
            if let Some(a) = v.get("c5_xo").and_then(|x| x.as_array()) {
                let mut f: Vec<f32> = a.iter().filter_map(|x| x.as_f64()).map(|x| (x as f32).clamp(40.0, 16000.0)).collect();
                f.sort_by(|p, q| p.partial_cmp(q).unwrap());
                for (k, x) in f.iter().enumerate().take(4) { b.c5.xo[k].set(*x); }
                b.c5.ver.fetch_add(1, Relaxed);
            }
            for (key, arr, lo, hi) in [("c5_thr", 0usize, -60.0f32, 0.0f32), ("c5_ratio", 1, 1.0, 20.0), ("c5_gain", 2, -12.0, 12.0)] {
                if let Some(a) = v.get(key).and_then(|x| x.as_array()) {
                    for (k, x) in a.iter().enumerate().take(5) {
                        let x = (x.as_f64().unwrap_or(0.0) as f32).clamp(lo, hi);
                        match arr { 0 => b.c5.thr[k].set(x), 1 => b.c5.ratio[k].set(x), _ => b.c5.gain[k].set(x) }
                    }
                }
            }
            if let Some(x) = flag(v, "mon") { b.mon.store(x, Relaxed); }
            json!({"ok":true})
        }
        "ret" => {
            let k = v.get("k").and_then(|x| x.as_u64()).unwrap_or(9) as usize;
            if k > 1 {
                return json!({"ok":false,"err":"bad index"});
            }
            if let Some(x) = num(v, "gain") { p.ret_gain[k].set(x.clamp(0.0, 2.0)); }
            json!({"ok":true})
        }
        "vban_rx" | "oban_rx" => {
            // {"c":"oban_rx","k":0,"on":true,"slot":9,"port":6990,"stream":"","allow":"<ip or empty>",
            //  "mode":"auto|pcm|coded","buf_ms":40,"ping":false,"text":false}   (vban_rx = classic VBAN only)
            let k = v.get("k").and_then(|x| x.as_u64()).unwrap_or(99) as usize;
            if k >= N_NET_RX {
                return json!({"ok":false,"err":"bad index"});
            }
            let mut st = net().lock().unwrap();
            apply_rx_map(p, k, &[], &[]);
            st.rx[k] = None; // stops the previous receiver
            p.net_rx_ch[k].store(0, Relaxed);
            if flag(v, "on").unwrap_or(false) {
                let allow = match text(v, "allow") {
                    "" => None,
                    a => match a.parse::<IpAddr>() {
                        Ok(ip) => Some(ip),
                        Err(_) => return json!({"ok":false,"err":"allow must be an IP address or empty"}),
                    },
                };
                let port = match v.get("port").and_then(|x| x.as_u64()).and_then(|x| u16::try_from(x).ok()) {
                    Some(pt) if pt > 0 => pt,
                    _ => return json!({"ok":false,"err":"port must be 1-65535"}),
                };
                let slots: Vec<i64> = match v.get("slots").and_then(|x| x.as_array()) {
                    Some(a) => a.iter().map(|x| x.as_i64().unwrap_or(-1)).collect(),
                    None => vec![v.get("slot").and_then(|x| x.as_i64()).unwrap_or(-1)],
                };
                let feeds = feeds_of(v);
                let mode = match text(v, "mode") {
                    "pcm" => crate::oban::RxMode::PcmOnly,
                    "coded" => crate::oban::RxMode::CodedOnly,
                    _ => crate::oban::RxMode::Auto,
                };
                let cfg = crate::oban::RxCfg {
                    port,
                    stream: text(v, "stream").to_string(),
                    allow,
                    engine_sr: p.sample_rate.load(Relaxed),
                    accept_oban: c == "oban_rx",
                    mode,
                    buf_ms: v.get("buf_ms").and_then(|x| x.as_u64()).unwrap_or(40).min(500) as u32,
                    ping: flag(v, "ping").unwrap_or(false),
                    text: flag(v, "text").unwrap_or(false),
                };
                *st.rx_stat[k].lock().unwrap() = "waiting for packets".into();
                for i in 0..RX_FEEDS {
                    p.net_rx[k * RX_FEEDS + i].skip(usize::MAX);
                }
                match crate::oban::start_rx(cfg, p.clone(), k, st.rx_stat[k].clone()) {
                    Some(h) => {
                        st.rx[k] = Some(h);
                        apply_rx_map(p, k, &slots, &feeds);
                    }
                    None => {
                        let e = st.rx_stat[k].lock().unwrap().clone();
                        return json!({"ok":false,"err":e});
                    }
                }
            } else {
                *st.rx_stat[k].lock().unwrap() = String::new();
            }
            json!({"ok":true})
        }
        "rx_map" => {
            // {"c":"rx_map","k":0,"slots":[9,10,-1,...],"feeds":[[0,1],[2,3],[4,4],...]}  rebind a receiver's feeds without restarting it
            let k = v.get("k").and_then(|x| x.as_u64()).unwrap_or(99) as usize;
            if k >= N_NET_RX {
                return json!({"ok":false,"err":"bad index"});
            }
            let slots: Vec<i64> = v.get("slots").and_then(|x| x.as_array()).map(|a| a.iter().map(|x| x.as_i64().unwrap_or(-1)).collect()).unwrap_or_default();
            apply_rx_map(p, k, &slots, &feeds_of(v));
            json!({"ok":true})
        }
        "tx_map" => {
            // {"c":"tx_map","k":0,"mx":[bus bitmask per output channel],"src":[0..3 per output channel]}
            let k = v.get("k").and_then(|x| x.as_u64()).unwrap_or(99) as usize;
            if k >= N_NET_TX {
                return json!({"ok":false,"err":"bad index"});
            }
            apply_tx_map(p, k, &u64s(v, "mx"), &u64s(v, "src"));
            json!({"ok":true})
        }
        "vban_tx" | "oban_tx" => {
            // {"c":"oban_tx","k":0,"on":true,"i":<bus>,"addr":"<host>","port":6990,"stream":"Stream1",
            //  "codec":"vban_pcm|oban_pcm|opus_music|opus_voice|lossless|mulaw","sr":48000,"ch":2,"dt":1,"kbps":128,"ms":20}
            let k = v.get("k").and_then(|x| x.as_u64()).unwrap_or(99) as usize;
            if k >= N_NET_TX {
                return json!({"ok":false,"err":"bad index"});
            }
            let mut st = net().lock().unwrap();
            p.net_tx_bus[k].store(-1, Relaxed);
            p.net_tx_nch[k].store(0, Relaxed);
            st.tx[k] = None;
            *st.tx_stat[k].lock().unwrap() = String::new();
            if flag(v, "on").unwrap_or(false) {
                let host = text(v, "addr");
                let port = v.get("port").and_then(|x| x.as_u64()).and_then(|x| u16::try_from(x).ok()).unwrap_or(0);
                if host.is_empty() || port == 0 {
                    return json!({"ok":false,"err":"address and port required"});
                }
                let addr = match format!("{host}:{port}").to_socket_addrs().ok().and_then(|a| {
                    let all: Vec<_> = a.collect();
                    all.iter().find(|x| x.is_ipv4()).copied().or_else(|| all.first().copied())
                }) {
                    Some(a) => a,
                    None => return json!({"ok":false,"err":"cannot resolve address"}),
                };
                let Some(codec) = crate::oban::TxCodec::parse(text(v, "codec")) else {
                    return json!({"ok":false,"err":"unknown codec"});
                };
                let u = |key: &str, d: u64| v.get(key).and_then(|x| x.as_u64()).unwrap_or(d);
                let esr = p.sample_rate.load(Relaxed);
                let cfg = crate::oban::TxCfg {
                    addr,
                    stream: text(v, "stream").to_string(),
                    codec,
                    sr: u("sr", esr as u64) as u32,
                    ch: u("ch", 2) as usize,
                    dt: u("dt", if flag(v, "f32").unwrap_or(false) { 4 } else { 1 }) as u8,
                    kbps: u("kbps", 128) as u32,
                    ms: u("ms", 20) as u32,
                    engine_sr: esr,
                };
                let matrix = v.get("mx").is_some();
                let nch = cfg.ch;
                if nch > 2 && !matrix {
                    return json!({"ok":false,"err":"more than 2 channels need a channel map (mx)"});
                }
                for i in 0..TX_PAIRS {
                    p.net_tx[k * TX_PAIRS + i].skip(usize::MAX);
                }
                let rings: Vec<_> = (0..TX_PAIRS).map(|i| p.net_tx[k * TX_PAIRS + i].clone()).collect();
                match crate::oban::start_tx(cfg, rings, st.tx_stat[k].clone()) {
                    Some(h) => {
                        st.tx[k] = Some(h);
                        if matrix {
                            apply_tx_map(p, k, &u64s(v, "mx"), &u64s(v, "src"));
                            p.net_tx_nch[k].store(nch.min(MAX_NET_CH) as u32, Relaxed);
                        } else {
                            p.net_tx_bus[k].store(idx as i32, Relaxed);
                        }
                    }
                    None => {
                        let e = st.tx_stat[k].lock().unwrap().clone();
                        return json!({"ok":false,"err":if e.is_empty() { "cannot open UDP socket".to_string() } else { e }});
                    }
                }
            }
            json!({"ok":true})
        }
        "fx" => {
            let f = &p.fx;
            if let Some(x) = flag(v, "rvb_on") { f.rvb_on.store(x, Relaxed); }
            if let Some(x) = num(v, "room") { f.room.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = num(v, "damp") { f.damp.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = num(v, "width") { f.width.set(x.clamp(0.0, 1.0)); }
            if let Some(x) = flag(v, "dly_on") { f.dly_on.store(x, Relaxed); }
            if let Some(x) = num(v, "fb") { f.fb.set(x.clamp(0.0, 0.95)); }
            if let Some(t) = v.get("tap").and_then(|x| x.as_u64()) {
                let t = t as usize;
                if t < crate::fx::TAPS {
                    if let Some(x) = flag(v, "on") { f.tap_on[t].store(x, Relaxed); }
                    if let Some(x) = num(v, "ms") { f.tap_ms[t].set(x.clamp(1.0, 1990.0)); }
                    if let Some(x) = num(v, "gain_db") { f.tap_gain[t].set(db_to_lin(x)); }
                    if let Some(x) = num(v, "pan") { f.tap_pan[t].set(x); }
                }
            }
            json!({"ok":true})
        }
        "eq" => {
            // {"c":"eq","t":"strip"|"bus","i":0,"cell":0..5,"on":bool,"kind":0..6,"f":Hz,"g":dB,"q":Q}
            let cell = v.get("cell").and_then(|x| x.as_u64()).unwrap_or(99) as usize;
            let is_bus = v.get("t").and_then(|x| x.as_str()) == Some("bus");
            if cell >= crate::dsp::CELLS || (is_bus && idx >= MAX_BUS) || (!is_bus && idx >= MAX_IN) {
                return json!({"ok":false,"err":"bad index"});
            }
            let (cp, ver) = if is_bus {
                (&p.buses[idx].eq[cell], &p.buses[idx].eq_ver)
            } else {
                (&p.strips[idx].eq[cell], &p.strips[idx].eq_ver)
            };
            if let Some(x) = flag(v, "on") { cp.on.store(x, Relaxed); }
            if let Some(x) = v.get("kind").and_then(|x| x.as_u64()) { cp.kind.store(x as u32, Relaxed); }
            if let Some(x) = num(v, "f") { cp.freq.set(x); }
            if let Some(x) = num(v, "g") { cp.gain.set(x); }
            if let Some(x) = num(v, "q") { cp.q.set(x); }
            ver.fetch_add(1, Relaxed);
            json!({"ok":true})
        }
        "comp" if idx < MAX_IN => {
            let c = &p.strips[idx].comp;
            if let Some(x) = flag(v, "on") { c.on.store(x, Relaxed); }
            if let Some(x) = num(v, "thr") { c.thr.set(x); }
            if let Some(x) = num(v, "ratio") { c.ratio.set(x); }
            if let Some(x) = num(v, "att") { c.att.set(x); }
            if let Some(x) = num(v, "rel") { c.rel.set(x); }
            if let Some(x) = num(v, "makeup") { c.makeup.set(x); }
            json!({"ok":true})
        }
        "gate" if idx < MAX_IN => {
            let g = &p.strips[idx].gate;
            if let Some(x) = flag(v, "on") { g.on.store(x, Relaxed); }
            if let Some(x) = num(v, "thr") { g.thr.set(x); }
            if let Some(x) = num(v, "att") { g.att.set(x); }
            if let Some(x) = num(v, "rel") { g.rel.set(x); }
            json!({"ok":true})
        }
        c if c.starts_with("dev_") => dev_command(c, v),
        "feed_open" => match crate::feed::open(p, v.get("slot").and_then(|x| x.as_u64()).unwrap_or(MAX_IN as u64) as usize) {
            Ok(port) if idx < usize::MAX => json!({"ok":true,"port":port}),
            Ok(_) => json!({"ok":false}),
            Err(e) => json!({"ok":false,"err":e}),
        },
        "latency" => {
            #[cfg(feature = "soft-backend")]
            crate::soft::set_latency(v.get("ms").and_then(|x| x.as_u64()).unwrap_or(40) as usize);
            json!({"ok":cfg!(feature = "soft-backend")})
        }
        "rec" => match crate::rec::start(p, idx, text(v, "path")) {
            Ok(()) => json!({"ok":true}),
            Err(e) => json!({"ok":false,"err":e}),
        },
        "rec_stop" => match crate::rec::stop(p) {
            Some(n) => json!({"ok":true,"frames":n}),
            None => json!({"ok":false,"err":"not recording"}),
        },
        "meters" => {
            let st: Vec<[f32; 2]> = p.strips.iter().map(|s| [s.peak_l.get(), s.peak_r.get()]).collect();
            let bs: Vec<[f32; 2]> = p.buses.iter().map(|b| [b.peak_l.get(), b.peak_r.get()]).collect();
            let gr: Vec<f32> = p.strips.iter().map(|s| s.gr_db.get()).collect();
            let (vb, vt): (Vec<String>, Vec<String>) = {
                let n = net().lock().unwrap();
                (n.rx_stat.iter().map(|s| s.lock().unwrap().clone()).collect(), n.tx_stat.iter().map(|s| s.lock().unwrap().clone()).collect())
            };
            let txt: Vec<String> = crate::oban::text_queue().lock().unwrap().drain(..).collect();
            let vbch: Vec<usize> = p.net_rx_ch.iter().map(|c| c.load(Relaxed)).collect();
            json!({"strips":st,"buses":bs,"gr":gr,"vban":vb,"vbch":vbch,"vbtx":vt,"text":txt,"xruns":p.xruns.load(Relaxed),
                   "max_us":p.max_block_us.load(Relaxed),"sr":p.sample_rate.load(Relaxed),
                   "buf":p.buffer.load(Relaxed)})
        }
        "reset_stats" => {
            p.max_block_us.store(0, Relaxed);
            p.xruns.store(0, Relaxed);
            json!({"ok":true})
        }
        _ => json!({"ok":false,"err":"bad command or index"}),
    }
}

#[cfg(feature = "cpal-backend")]
fn dev_command(c: &str, v: &Value) -> Value {
    crate::cpal_io::handle(c, v)
}
#[cfg(not(feature = "cpal-backend"))]
fn dev_command(_c: &str, _v: &Value) -> Value {
    json!({"ok":false,"err":"this backend has no device routing (Linux: PipeWire links, tests: none)"})
}

fn conn_loop<R: std::io::Read, W: Write>(r: R, mut w: W, p: Arc<Params>, need_token: bool) {
    let mut lines = BufReader::new(r).lines();
    if need_token {
        let tok = std::env::var("VERTIMEETER_TOKEN").unwrap_or_default();
        match lines.next() {
            Some(Ok(l)) if !tok.is_empty() && l == format!("AUTH {tok}") => {
                let _ = writeln!(w, "{}", json!({"ok":true}));
            }
            _ => return,
        }
    }
    for line in lines.map_while(Result::ok) {
        let r = match serde_json::from_str::<Value>(&line) {
            Ok(v) => handle(&p, &v),
            Err(e) => json!({"ok":false,"err":e.to_string()}),
        };
        if writeln!(w, "{r}").is_err() {
            break;
        }
    }
}

/// `spec` is a unix socket path (Linux), or `tcp:<port file>` (Windows): the engine binds 127.0.0.1 on a free port,
/// writes the port number to that file and requires `AUTH <VERTIMEETER_TOKEN>` as the first line of every connection.
pub fn serve(spec: &str, p: Arc<Params>) {
    if let Some(portfile) = spec.strip_prefix("tcp:") {
        let l = std::net::TcpListener::bind(("127.0.0.1", 0)).expect("bind control port");
        let port = l.local_addr().unwrap().port();
        let tmp = format!("{portfile}.tmp");
        std::fs::write(&tmp, port.to_string()).expect("write port file");
        std::fs::rename(&tmp, portfile).expect("publish port file");
        for conn in l.incoming().flatten() {
            let p = p.clone();
            let _ = conn.set_nodelay(true);
            std::thread::spawn(move || {
                let w = conn.try_clone().unwrap();
                conn_loop(conn, w, p, true);
            });
        }
        return;
    }
    #[cfg(unix)]
    {
        let _ = std::fs::remove_file(spec);
        let l = std::os::unix::net::UnixListener::bind(spec).expect("bind control socket");
        for conn in l.incoming().flatten() {
            let p = p.clone();
            std::thread::spawn(move || {
                let w = conn.try_clone().unwrap();
                conn_loop(conn, w, p, false);
            });
        }
    }
    #[cfg(not(unix))]
    panic!("control socket '{spec}': unix sockets are not available here, use tcp:<port file>");
}
