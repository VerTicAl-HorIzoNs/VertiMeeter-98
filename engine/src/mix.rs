//! Realtime process callback: no allocation, no locks, no syscalls, no logging.
use crate::dsp::{Denoise, DynState, Eq, Limiter, Spatial};
use crate::fx::{MultiTap, Reverb};
use crate::params::*;
use crate::vox::{Voice, C5};
use crate::backend::{AudioIn, AudioOut, Client, Control, Port, ProcessHandler, ProcessScope};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::Arc;
use std::time::Instant;

const CHUNK: usize = 8192;
const BDL: usize = 65536; // per-bus delay line (power of two, ~1.3 s at 48 kHz)

type Pair<T> = (Port<T>, Port<T>);

pub struct Mixer {
    pub p: Arc<Params>,
    pub ins: Vec<Pair<AudioIn>>,
    pub outs: Vec<Pair<AudioOut>>,
    ext_out: Vec<Pair<AudioOut>>,
    ret_in: Vec<Pair<AudioIn>>,
    ins_out: Vec<Pair<AudioOut>>,
    ins_in: Vec<Pair<AudioIn>>,
    voice: Vec<Voice>,
    c5: Vec<C5>,
    tph: [f32; MAX_IN], // tremolo phase
    bf: [[f32; 2]; MAX_BUS], // bus-mode filter state
    acc: Vec<[Vec<f32>; 2]>, // per-bus accumulation scratch (preallocated)
    xacc: [[Vec<f32>; 2]; 2], // EXT1/EXT2 send accumulation
    cur: [[f32; 2]; MAX_IN], // smoothed per-strip L/R gain
    sg: [[f32; MAX_BUS]; MAX_IN], // current per-send gain
    xs: [[f32; 2]; MAX_IN], // current EXT1/EXT2 send level
    pfc: [f32; MAX_IN], // smoothed pre-fader live flag
    klp: [f32; MAX_IN], // karaoke bass keeper
    bus_cur: [f32; MAX_BUS],
    tmp: [Vec<f32>; 2],
    txm: Vec<Vec<f32>>, // channel-matrix scratch for multichannel senders (MAX_NET_CH x CHUNK)
    eq_s: Vec<Eq>,
    dyn_s: Vec<DynState>,
    eq_b: Vec<Eq>,
    spat: Vec<Spatial>,
    dns: Vec<Option<Denoise>>, // strips 0..11 only (media files do not need it)
    lim_s: Vec<Limiter>,
    lim_b: Vec<Limiter>,
    bdl: Vec<[Vec<f32>; 2]>,
    bdi: [usize; MAX_BUS],
    net_ready: [bool; N_RX_ALL],
    reverb: Vec<Reverb>,        // one per bus: a strip's FX only reaches the buses it is routed to
    delay: Vec<MultiTap>,
    fxin: Vec<[Vec<f32>; 4]>,   // per bus: rvb L/R, dly L/R
    fxout: [Vec<f32>; 4],       // scratch for the bus being mixed
    fed: [bool; MAX_BUS],       // a strip with an RVB/DLY send is routed to this bus
    fxd: [[bool; 2]; MAX_BUS],  // reverb/delay state holds audio (cleared once the bus goes idle)
    eft: [[f32; MAX_BUS]; 2],   // EXT1/EXT2 return feed target per bus (routed sends only)
    ef: [[f32; 2]; MAX_BUS],    // current EXT return feed per bus
    fs: [[f32; 2]; MAX_IN],   // current rvb/dly send per strip
    fr: [[f32; 2]; MAX_BUS],  // current rvb/dly return per bus
    init: bool,
}

impl Mixer {
    pub fn new(
        p: Arc<Params>,
        ins: Vec<Pair<AudioIn>>,
        outs: Vec<Pair<AudioOut>>,
        ext_out: Vec<Pair<AudioOut>>,
        ret_in: Vec<Pair<AudioIn>>,
        ins_out: Vec<Pair<AudioOut>>,
        ins_in: Vec<Pair<AudioIn>>,
        sr: f32,
    ) -> Self {
        Mixer {
            p,
            ins,
            outs,
            ext_out,
            ret_in,
            ins_out,
            ins_in,
            voice: (0..N_STRIPS + 1).map(|_| Voice::new()).collect(),
            c5: (0..MAX_BUS).map(|_| C5::new()).collect(),
            tph: [0.0; MAX_IN],
            bf: [[0.0; 2]; MAX_BUS],
            acc: (0..MAX_BUS).map(|_| [crate::params::zeros(CHUNK), crate::params::zeros(CHUNK)]).collect(),
            xacc: std::array::from_fn(|_| [crate::params::zeros(CHUNK), crate::params::zeros(CHUNK)]),
            cur: [[0.0; 2]; MAX_IN],
            sg: [[0.0; MAX_BUS]; MAX_IN],
            xs: [[0.0; 2]; MAX_IN],
            pfc: [0.0; MAX_IN],
            klp: [0.0; MAX_IN],
            bus_cur: [0.0; MAX_BUS],
            tmp: [crate::params::zeros(CHUNK), crate::params::zeros(CHUNK)],
            txm: (0..MAX_NET_CH).map(|_| crate::params::zeros(CHUNK)).collect(),
            eq_s: (0..MAX_IN).map(|_| Eq::new()).collect(),
            dyn_s: (0..MAX_IN).map(|_| DynState::default()).collect(),
            eq_b: (0..MAX_BUS).map(|_| Eq::new()).collect(),
            spat: (0..MAX_IN).map(|_| Spatial::new()).collect(),
            dns: (0..MAX_IN).map(|i| if i < N_STRIPS { Some(Denoise::new()) } else { None }).collect(),
            lim_s: (0..MAX_IN).map(|_| Limiter::new()).collect(),
            lim_b: (0..MAX_BUS).map(|_| Limiter::new()).collect(),
            bdl: (0..MAX_BUS).map(|_| [crate::params::zeros(BDL), crate::params::zeros(BDL)]).collect(),
            bdi: [0; MAX_BUS],
            net_ready: [false; N_RX_ALL],
            reverb: (0..MAX_BUS).map(|_| Reverb::new(sr)).collect(),
            delay: (0..MAX_BUS).map(|_| MultiTap::new(sr)).collect(),
            fxin: (0..MAX_BUS).map(|_| std::array::from_fn(|_| crate::params::zeros(CHUNK))).collect(),
            fxout: std::array::from_fn(|_| crate::params::zeros(CHUNK)),
            fed: [false; MAX_BUS],
            fxd: [[false; 2]; MAX_BUS],
            eft: [[0.0; MAX_BUS]; 2],
            ef: [[0.0; 2]; MAX_BUS],
            fs: [[0.0; 2]; MAX_IN],
            fr: [[0.0; 2]; MAX_BUS],
            init: false,
        }
    }
}

#[inline]
fn pan_gains(pan: f32) -> (f32, f32) {
    // constant-power
    let a = (pan.clamp(-1.0, 1.0) + 1.0) * std::f32::consts::FRAC_PI_4;
    (a.cos(), a.sin())
}

#[cfg(target_arch = "x86_64")]
fn flush_denormals() {
    // FTZ | DAZ: denormals never cost CPU time inside the callback
    #[allow(deprecated)]
    unsafe {
        use std::arch::x86_64::{_mm_getcsr, _mm_setcsr};
        _mm_setcsr(_mm_getcsr() | 0x8040);
    }
}
#[cfg(not(target_arch = "x86_64"))]
fn flush_denormals() {}

impl ProcessHandler for Mixer {
    fn process(&mut self, _c: &Client, ps: &ProcessScope) -> Control {
        let t0 = Instant::now();
        if !self.init {
            flush_denormals();
            self.init = true;
        }
        let n_total = ps.n_frames() as usize;
        let sr = self.p.sample_rate.load(Relaxed) as f32;
        let lim_rel = 1.0 - (-1.0 / (0.1 * sr)).exp();
        let kar_k = 1.0 - (-2.0 * std::f32::consts::PI * 230.0 / sr).exp();
        let any_solo = self.p.strips.iter().any(|s| s.solo.load(Relaxed) && s.active.load(Relaxed));
        let mut off = 0;
        while off < n_total {
            let n = (n_total - off).min(CHUNK);
            for b in 0..MAX_BUS {
                self.acc[b][0][..n].fill(0.0);
                self.acc[b][1][..n].fill(0.0);
            }
            for fi in self.fxin.iter_mut() {
                for v in fi.iter_mut() {
                    v[..n].fill(0.0);
                }
            }
            self.fed = [false; MAX_BUS];
            self.eft = [[0.0; MAX_BUS]; 2];
            for k in 0..self.ins_out.len() {
                self.ins_out[k].0.as_mut_slice(ps)[off..off + n].fill(0.0);
                self.ins_out[k].1.as_mut_slice(ps)[off..off + n].fill(0.0);
            }
            for x in self.xacc.iter_mut() {
                x[0][..n].fill(0.0);
                x[1][..n].fill(0.0);
            }
            for i in 0..MAX_IN {
                let s = &self.p.strips[i];
                let act = s.active.load(Relaxed);
                if !act && self.cur[i] == [0.0, 0.0] && self.sg[i].iter().all(|x| *x == 0.0) {
                    s.peak_l.set(s.peak_l.get() * 0.9);
                    s.peak_r.set(s.peak_r.get() * 0.9);
                    continue; // idle slot: costs nothing
                }
                // input: a VBAN receiver bound to this slot replaces the JACK ports
                let mut from_net = false;
                for k in 0..N_RX_ALL {
                    if self.p.net_rx_slot[k].load(Relaxed) == i as i32 {
                        let ring = &self.p.net_rx[k];
                        let avail = ring.len();
                        let tgt = self.p.net_target[k].load(Relaxed);
                        if avail > (4 * n + 4096).max(tgt * 2 + 4096) {
                            ring.skip(avail - (2 * n).max(tgt)); // keep latency bounded
                        }
                        if !self.net_ready[k] && ring.len() >= 2 * n {
                            self.net_ready[k] = true; // prebuffer before playing
                        }
                        let [tl, tr] = &mut self.tmp;
                        let got = if self.net_ready[k] { ring.pop(&mut tl[..n], &mut tr[..n]) } else { 0 };
                        if got < n {
                            tl[got..n].fill(0.0);
                            tr[got..n].fill(0.0);
                            self.net_ready[k] = false;
                        }
                        from_net = true;
                        break;
                    }
                }
                if !from_net {
                    self.tmp[0][..n].copy_from_slice(&self.ins[i].0.as_slice(ps)[off..off + n]);
                    self.tmp[1][..n].copy_from_slice(&self.ins[i].1.as_slice(ps)[off..off + n]);
                }
                {
                    let [tl, tr] = &mut self.tmp;
                    self.eq_s[i].refresh(&s.eq, s.eq_ver.load(Relaxed), sr);
                    if self.eq_s[i].active() {
                        self.eq_s[i].process(&mut tl[..n], &mut tr[..n]);
                    }
                    let da = s.dns.get();
                    match (&mut self.dns[i], da > 0.001 && sr as u32 == 48000) {
                        (Some(d), true) => d.process(&mut tl[..n], &mut tr[..n], da),
                        (Some(d), false) => d.active = false,
                        _ => {}
                    }
                    if s.gate.on.load(Relaxed) {
                        self.dyn_s[i].gate(&s.gate, &mut tl[..n], &mut tr[..n], sr);
                    }
                    if s.comp.on.load(Relaxed) {
                        self.dyn_s[i].comp(&s.comp, &mut tl[..n], &mut tr[..n], sr);
                        s.gr_db.set(self.dyn_s[i].gr_db);
                    } else {
                        s.gr_db.set(0.0);
                    }
                    if i < N_STRIPS + 1 && s.voice.on.load(Relaxed) {
                        self.voice[i].process(&s.voice, &mut tl[..n], &mut tr[..n], sr);
                    }
                    if i < self.ins_out.len() && s.ins_on.load(Relaxed) {
                        let m = s.ins_mix.get().clamp(0.0, 1.0);
                        self.ins_out[i].0.as_mut_slice(ps)[off..off + n].copy_from_slice(&tl[..n]);
                        self.ins_out[i].1.as_mut_slice(ps)[off..off + n].copy_from_slice(&tr[..n]);
                        let wl = &self.ins_in[i].0.as_slice(ps)[off..off + n];
                        let wr = &self.ins_in[i].1.as_slice(ps)[off..off + n];
                        for f in 0..n {
                            tl[f] = tl[f] * (1.0 - m) + wl[f] * m;
                            tr[f] = tr[f] * (1.0 - m) + wr[f] * m;
                        }
                    }
                    if s.sp_on.load(Relaxed) {
                        self.spat[i].process(&mut tl[..n], &mut tr[..n], sr, s.sp_x.get(), s.sp_y.get());
                    }
                }
                let inl = &self.tmp[0][..n];
                let inr = &self.tmp[1][..n];
                let live = s.active.load(Relaxed)
                    && !s.mute.load(Relaxed)
                    && (!any_solo || s.solo.load(Relaxed));
                let g = if live { s.gain.get() } else { 0.0 };
                let (pl, pr) = pan_gains(if s.sp_on.load(Relaxed) { 0.0 } else { s.pan.get() });
                let mono = s.mono.load(Relaxed);
                let kar = s.kar.load(Relaxed);
                let lim_on = s.lim_on.load(Relaxed);
                let lim_thr = s.lim_thr.get().max(0.001);
                let post = s.ext_post.load(Relaxed);
                let live_f = if live { 1.0f32 } else { 0.0 };
                let tl = g * pl * std::f32::consts::SQRT_2;
                let tr = g * pr * std::f32::consts::SQRT_2;
                let (mut gl, mut gr) = (self.cur[i][0], self.cur[i][1]);
                let k = 1.0 - (-1.0 / (0.005 * sr)).exp(); // ~5 ms smoothing: no zipper clicks
                let (mut mpl, mut mpr) = (0.0f32, 0.0f32);
                // per-send gain, ramped linearly across the block (no clicks, no per-sample state)
                let mut sg = self.sg[i];
                let mut step = [0.0f32; MAX_BUS];
                let mut st = [0.0f32; MAX_BUS]; // target send gain per bus (0 = not routed)
                let mut any_send = false;
                for b in 0..MAX_BUS {
                    let t = if s.send[b].load(Relaxed) { s.send_gain[b].get() } else { 0.0 };
                    st[b] = t;
                    step[b] = (t - sg[b]) / n as f32;
                    any_send |= t > 0.0 || sg[b] > 0.0;
                }
                let (rs_t, ds_t) = (s.rvb_send.get(), s.dly_send.get());
                let (mut rs, mut ds) = (self.fs[i][0], self.fs[i][1]);
                let (rs_step, ds_step) = ((rs_t - rs) / n as f32, (ds_t - ds) / n as f32);
                let (x1_t, x2_t) = (s.ext1.get(), s.ext2.get());
                let (mut x1, mut x2) = (self.xs[i][0], self.xs[i][1]);
                let (x1_step, x2_step) = ((x1_t - x1) / n as f32, (x2_t - x2) / n as f32);
                // FX only reaches the buses this strip is routed to (A1/A2/B1... buttons); no routing, no effect
                let fx_act = rs_t > 0.0 || ds_t > 0.0 || rs > 0.0 || ds > 0.0;
                for b in 0..MAX_BUS {
                    if st[b] > 0.0 || sg[b] > 0.0 {
                        if fx_act {
                            self.fed[b] = true;
                        }
                        let rt = st[b].min(1.0) * live_f;
                        self.eft[0][b] = self.eft[0][b].max(rt * x1_t.min(1.0));
                        self.eft[1][b] = self.eft[1][b].max(rt * x2_t.min(1.0));
                    }
                }
                let (mod_on, mod_step, mod_depth) = (s.mod_on.load(Relaxed), s.mod_rate.get().clamp(0.1, 30.0) / sr, s.mod_depth.get().clamp(0.0, 1.0));
                let mut tph = self.tph[i];
                let mut pf = self.pfc[i];
                let mut klp = self.klp[i];
                let [xa, xb] = &mut self.xacc;
                for f in 0..n {
                    gl += (tl - gl) * k;
                    gr += (tr - gr) * k;
                    pf += (live_f - pf) * k;
                    let (a, c) = if mono {
                        let m = 0.5 * (inl[f] + inr[f]);
                        (m, m)
                    } else {
                        (inl[f], inr[f])
                    };
                    let (a, c) = if mod_on {
                        tph = (tph + mod_step).fract();
                        let tg = 1.0 - mod_depth * (0.5 - 0.5 * (std::f32::consts::TAU * tph).cos());
                        (a * tg, c * tg)
                    } else {
                        (a, c)
                    };
                    let (a, c) = match kar {
                        1 => {
                            let sd = 0.5 * (a - c);
                            (sd, sd)
                        }
                        2 => {
                            let m = 0.5 * (a + c);
                            klp += kar_k * (m - klp);
                            let sd = 0.5 * (a - c) + klp;
                            (sd, sd)
                        }
                        3 => {
                            let m = 0.5 * (a + c);
                            (m, m)
                        }
                        _ => (a, c),
                    };
                    let (mut ol, mut or_) = (a * gl, c * gr);
                    if lim_on {
                        self.lim_s[i].tick(&mut ol, &mut or_, lim_thr, lim_rel);
                    }
                    mpl = mpl.max(ol.abs());
                    mpr = mpr.max(or_.abs());
                    rs += rs_step;
                    ds += ds_step;
                    x1 += x1_step;
                    x2 += x2_step;
                    let (xl, xr) = if post { (ol, or_) } else { (a * pf, c * pf) };
                    xa[0][f] += xl * x1;
                    xa[1][f] += xr * x1;
                    xb[0][f] += xl * x2;
                    xb[1][f] += xr * x2;
                    if any_send {
                        for b in 0..MAX_BUS {
                            sg[b] += step[b];
                            if sg[b] > 0.0 {
                                self.acc[b][0][f] += ol * sg[b];
                                self.acc[b][1][f] += or_ * sg[b];
                                if fx_act {
                                    // the effect send rides on the same route: its own bus, its own reverb/delay
                                    let (fr_, fd_) = (sg[b] * rs, sg[b] * ds);
                                    let fi = &mut self.fxin[b];
                                    fi[0][f] += ol * fr_;
                                    fi[1][f] += or_ * fr_;
                                    fi[2][f] += ol * fd_;
                                    fi[3][f] += or_ * fd_;
                                }
                            }
                        }
                    }
                }
                self.cur[i] = [gl, gr];
                self.fs[i] = [rs_t, ds_t];
                self.xs[i] = [x1_t, x2_t];
                self.pfc[i] = pf;
                self.tph[i] = tph;
                self.klp[i] = klp;
                for b in 0..MAX_BUS {
                    // land exactly on target to avoid drift
                    let t = if s.send[b].load(Relaxed) { s.send_gain[b].get() } else { 0.0 };
                    sg[b] = if step[b] != 0.0 { t } else { sg[b] };
                }
                self.sg[i] = sg;
                let decay = (-(n as f32) / (0.30 * sr)).exp();
                s.peak_l.set(mpl.max(s.peak_l.get() * decay));
                s.peak_r.set(mpr.max(s.peak_r.get() * decay));
            }
            for k in 0..2 {
                let (po_l, po_r) = &mut self.ext_out[k];
                po_l.as_mut_slice(ps)[off..off + n].copy_from_slice(&self.xacc[k][0][..n]);
                po_r.as_mut_slice(ps)[off..off + n].copy_from_slice(&self.xacc[k][1][..n]);
            }
            for b in 0..MAX_BUS {
                let bus = &self.p.buses[b];
                let target = if bus.mute.load(Relaxed) { 0.0 } else { bus.gain.get() };
                let mode = if bus.mono.load(Relaxed) { 1 } else { bus.mode.load(Relaxed) };
                let lim_on = bus.lim_on.load(Relaxed);
                let lim_thr = bus.lim_thr.get().max(0.001);
                let dly = ((bus.dly_ms.get().clamp(0.0, 1000.0) * 0.001 * sr) as usize).min(BDL - 1);
                let k = 1.0 - (-1.0 / (0.005 * sr)).exp();
                let mut g = self.bus_cur[b];
                let (mut ml, mut mr) = (0.0f32, 0.0f32);
                {
                    // this bus's own reverb/delay: fed only by strips routed here, so nothing leaks to other buses
                    let fp = &self.p.fx;
                    let (rt, dt) = (bus.rvb_ret.get(), bus.dly_ret.get());
                    let (rc0, dc0) = (self.fr[b][0], self.fr[b][1]);
                    let fed = self.fed[b];
                    let [il, ir, dl, dr] = &self.fxin[b];
                    let [rl, rr, ql, qr] = &mut self.fxout;
                    if fp.rvb_on.load(Relaxed) && (fed || rt > 0.0 || rc0 > 0.0) {
                        self.reverb[b].process(fp, &il[..n], &ir[..n], &mut rl[..n], &mut rr[..n]);
                        self.fxd[b][0] = true;
                    } else {
                        rl[..n].fill(0.0);
                        rr[..n].fill(0.0);
                        if self.fxd[b][0] {
                            self.reverb[b].clear(); // idle: wipe the tail so it never replays later
                            self.fxd[b][0] = false;
                        }
                    }
                    if fp.dly_on.load(Relaxed) && (fed || dt > 0.0 || dc0 > 0.0) {
                        self.delay[b].process(fp, &dl[..n], &dr[..n], &mut ql[..n], &mut qr[..n]);
                        self.fxd[b][1] = true;
                    } else {
                        ql[..n].fill(0.0);
                        qr[..n].fill(0.0);
                        if self.fxd[b][1] {
                            self.delay[b].clear();
                            self.fxd[b][1] = false;
                        }
                    }
                    // external FX returns (RET1 = reverb side, RET2 = delay side) reach only buses with a routed EXT send
                    for k in 0..2 {
                        let rg = self.p.ret_gain[k].get();
                        let (et, ec) = (self.eft[k][b], self.ef[b][k]);
                        if rg > 0.0 && (et > 0.0 || ec > 0.0) {
                            let a = &self.ret_in[k].0.as_slice(ps)[off..off + n];
                            let c = &self.ret_in[k].1.as_slice(ps)[off..off + n];
                            let (dl_, dr_) = if k == 0 { (&mut *rl, &mut *rr) } else { (&mut *ql, &mut *qr) };
                            let (mut e, es) = (ec, (et - ec) / n as f32);
                            for f in 0..n {
                                e += es;
                                dl_[f] += a[f] * rg * e;
                                dr_[f] += c[f] * rg * e;
                            }
                        }
                        self.ef[b][k] = et;
                    }
                }
                {
                    let (rt, dt) = (bus.rvb_ret.get(), bus.dly_ret.get());
                    let (mut rc, mut dc) = (self.fr[b][0], self.fr[b][1]);
                    let (rst, dst) = ((rt - rc) / n as f32, (dt - dc) / n as f32);
                    let [al, ar] = &mut self.acc[b];
                    for f in 0..n {
                        rc += rst;
                        dc += dst;
                        al[f] += self.fxout[0][f] * rc + self.fxout[2][f] * dc;
                        ar[f] += self.fxout[1][f] * rc + self.fxout[3][f] * dc;
                    }
                    self.fr[b] = [rt, dt];
                }
                {
                    self.eq_b[b].refresh(&bus.eq, bus.eq_ver.load(Relaxed), sr);
                    if self.eq_b[b].active() {
                        let [al, ar] = &mut self.acc[b];
                        self.eq_b[b].process(&mut al[..n], &mut ar[..n]);
                    }
                }
                if bus.c5.on.load(Relaxed) {
                    let [al, ar] = &mut self.acc[b];
                    self.c5[b].process(&bus.c5, &mut al[..n], &mut ar[..n], sr);
                }
                let kf = 1.0 - (-2.0 * std::f32::consts::PI * 120.0 / sr).exp();
                let (po_l, po_r) = &mut self.outs[b];
                let ol = &mut po_l.as_mut_slice(ps)[off..off + n];
                let or_ = &mut po_r.as_mut_slice(ps)[off..off + n];
                let mut wi = self.bdi[b];
                for f in 0..n {
                    g += (target - g) * k;
                    let (mut l, mut r) = (self.acc[b][0][f], self.acc[b][1][f]);
                    match mode {
                        1 => {
                            let m = 0.5 * (l + r);
                            l = m;
                            r = m;
                        }
                        2 => std::mem::swap(&mut l, &mut r),
                        3 => l = -l,
                        4 => r = -r,
                        5 => r = l,
                        6 => l = r,
                        7 => {
                            let sd = 0.5 * (l - r);
                            l = sd;
                            r = -sd;
                        }
                        8 => {
                            l = -l;
                            r = -r;
                        }
                        9 => {
                            let sd = 0.5 * (l - r);
                            l = sd;
                            r = sd;
                        }
                        10 => {
                            let m = 0.5 * (l + r);
                            self.bf[b][0] += kf * (m - self.bf[b][0]);
                            self.bf[b][1] += kf * (self.bf[b][0] - self.bf[b][1]);
                            l = self.bf[b][1];
                            r = l;
                        }
                        11 => {
                            self.bf[b][0] += kf * (l - self.bf[b][0]);
                            self.bf[b][1] += kf * (r - self.bf[b][1]);
                            l -= self.bf[b][0];
                            r -= self.bf[b][1];
                        }
                        12 | 13 => {
                            let (m, sd) = (0.5 * (l + r), 0.5 * (l - r) * if mode == 12 { 1.7 } else { 0.35 });
                            l = m + sd;
                            r = m - sd;
                        }
                        _ => {}
                    }
                    self.bdl[b][0][wi] = l;
                    self.bdl[b][1][wi] = r;
                    if dly > 0 {
                        let ri = (wi + BDL - dly) & (BDL - 1);
                        l = self.bdl[b][0][ri];
                        r = self.bdl[b][1][ri];
                    }
                    wi = (wi + 1) & (BDL - 1);
                    l *= g;
                    r *= g;
                    if lim_on {
                        self.lim_b[b].tick(&mut l, &mut r, lim_thr, lim_rel);
                    }
                    ml = ml.max(l.abs());
                    mr = mr.max(r.abs());
                    ol[f] = l;
                    or_[f] = r;
                }
                self.bdi[b] = wi;
                self.bus_cur[b] = g;
                for k in 0..N_TX_ALL {
                    if self.p.net_tx_bus[k].load(Relaxed) == b as i32 {
                        let ring = if k == REC { REC_RING } else { k * TX_PAIRS };
                        self.p.net_tx[ring].push(&ol[..], &or_[..]);
                    }
                }
                let decay = (-(n as f32) / (0.30 * sr)).exp();
                bus.peak_l.set(ml.max(bus.peak_l.get() * decay));
                bus.peak_r.set(mr.max(bus.peak_r.get() * decay));
            }
            // multichannel network senders: each output channel sums the buses ticked for it
            for k in 0..N_NET_TX {
                let nch = (self.p.net_tx_nch[k].load(Relaxed) as usize).min(MAX_NET_CH);
                if nch == 0 {
                    continue;
                }
                for c in 0..nch {
                    let mask = self.p.net_tx_mask[k][c].load(Relaxed);
                    let src = self.p.net_tx_src[k][c].load(Relaxed);
                    let out = &mut self.txm[c];
                    out[..n].fill(0.0);
                    for b in 0..MAX_BUS {
                        if mask & (1 << b) == 0 {
                            continue;
                        }
                        let (po_l, po_r) = &mut self.outs[b];
                        let (bl, br) = (&po_l.as_mut_slice(ps)[off..off + n], &po_r.as_mut_slice(ps)[off..off + n]);
                        match if src == 0 { 1 + (c & 1) as u32 } else { src } {
                            1 => for f in 0..n { out[f] += bl[f]; },
                            2 => for f in 0..n { out[f] += br[f]; },
                            _ => for f in 0..n { out[f] += 0.5 * (bl[f] + br[f]); },
                        }
                    }
                }
                for pr in 0..nch.div_ceil(2) {
                    let (a, b2) = (2 * pr, (2 * pr + 1).min(nch - 1));
                    self.p.net_tx[k * TX_PAIRS + pr].push(&self.txm[a][..n], &self.txm[b2][..n]);
                }
            }
            // MON: buses flagged for monitoring are also heard on A1
            for b in 1..MAX_BUS {
                if self.p.buses[b].mon.load(Relaxed) {
                    let (first, rest) = self.outs.split_at_mut(b);
                    let src_l = rest[0].0.as_mut_slice(ps);
                    let src_r = rest[0].1.as_mut_slice(ps);
                    let (mut ml, mut mr) = (0.0f32, 0.0f32);
                    let dl = first[0].0.as_mut_slice(ps);
                    for f in 0..n {
                        dl[off + f] += src_l[off + f];
                        ml = ml.max(dl[off + f].abs());
                    }
                    let dr = first[0].1.as_mut_slice(ps);
                    for f in 0..n {
                        dr[off + f] += src_r[off + f];
                        mr = mr.max(dr[off + f].abs());
                    }
                    let a1 = &self.p.buses[0];
                    a1.peak_l.set(a1.peak_l.get().max(ml));
                    a1.peak_r.set(a1.peak_r.get().max(mr));
                }
            }
            off += n;
        }
        let us = t0.elapsed().as_micros() as u64;
        if us > self.p.max_block_us.load(Relaxed) {
            self.p.max_block_us.store(us, Relaxed);
        }
        Control::Continue
    }
}
