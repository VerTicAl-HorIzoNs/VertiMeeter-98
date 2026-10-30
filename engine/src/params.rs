//! Lock-free shared parameters. The control thread writes, the audio callback reads.
//! Nothing here allocates or locks after construction.
use crate::fx::FxParams;
use crate::dsp::{default_cells, BqParams, CompParams, GateParams, CELLS};
use crate::net::Ring;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::Arc;

pub const MAX_IN: usize = 24; // stereo input slots: 0..15 strips, 16..23 media players
pub const MAX_BUS: usize = 16; // output buses (A1..A8 = 0..7, B1..B8 = 8..15)
pub const N_STRIPS: usize = 16; // user strips (8 per input pane); the media player sits at slot 16
pub const N_NET_RX: usize = 8; // VBAN / OBAN receivers (each feeds one strip slot)
pub const N_NET_TX: usize = 4; // VBAN / OBAN senders (each taps one bus)
pub const RX_FEEDS: usize = 8; // a receiver can feed up to 8 strips; each feed takes any two channels of the stream (same channel twice = mono)
pub const TX_PAIRS: usize = 4; // a sender carries up to 8 channels = 4 stereo pairs
pub const MAX_NET_CH: usize = 8;
pub const N_RX_ALL: usize = N_NET_RX * RX_FEEDS + 1; // rx ring r = receiver r / RX_FEEDS, feed r % RX_FEEDS; + the local PCM feed (last): the media player on Windows
pub const N_TX_ALL: usize = N_NET_TX + 1; // single-bus taps: sender 0..N_NET_TX, + the WAV recorder tap (index N_NET_TX)
pub const N_TX_RINGS: usize = N_NET_TX * TX_PAIRS + 1; // tx ring = sender * TX_PAIRS + pair, + the recorder ring (last)
pub const FEED: usize = N_NET_RX * RX_FEEDS;
pub const REC: usize = N_NET_TX;
pub const REC_RING: usize = N_NET_TX * TX_PAIRS;

pub struct AF(AtomicU32);
impl AF {
    pub const fn new(v: f32) -> Self {
        AF(AtomicU32::new(v.to_bits()))
    }
    #[inline]
    pub fn get(&self) -> f32 {
        f32::from_bits(self.0.load(Relaxed))
    }
    #[inline]
    pub fn set(&self, v: f32) {
        self.0.store(v.to_bits(), Relaxed)
    }
}

pub struct Strip {
    pub active: AtomicBool,
    pub gain: AF, // linear
    pub pan: AF,  // -1 (L) .. +1 (R)
    pub mute: AtomicBool,
    pub solo: AtomicBool,
    pub mono: AtomicBool,
    pub send: [AtomicBool; MAX_BUS],
    pub send_gain: [AF; MAX_BUS], // linear, applied when the send is on
    pub eq: [BqParams; CELLS],
    pub eq_ver: AtomicU32,
    pub comp: CompParams,
    pub gate: GateParams,
    pub gr_db: AF, // compressor gain reduction readout
    pub rvb_send: AF, // 0..1
    pub dns: AF, // denoiser amount 0..1 (RNNoise)
    pub sp_on: AtomicBool, // 3D positioner
    pub sp_x: AF,          // -1 left .. +1 right
    pub sp_y: AF,          // -1 behind .. +1 front
    pub dly_send: AF,
    pub lim_on: AtomicBool,
    pub lim_thr: AF, // linear
    pub ext1: AF,
    pub ext2: AF,
    pub ext_post: AtomicBool,
    pub voice: crate::vox::VoiceParams,
    pub mod_on: AtomicBool,   // modulation pad: tremolo
    pub mod_rate: AF,         // Hz
    pub mod_depth: AF,        // 0..1
    pub ins_on: AtomicBool,   // insert FX loop (INSn_OUT -> external JACK effect -> INSn_IN)
    pub ins_mix: AF,          // wet amount 0..1
    pub kar: AtomicU32, // 0 off, 1 K1 (vocal cut), 2 K2 (vocal cut keep bass), 3 M.C. (center only)
    pub peak_l: AF,
    pub peak_r: AF,
}

pub struct Bus {
    pub rvb_ret: AF,
    pub dly_ret: AF,
    pub eq: [BqParams; CELLS],
    pub eq_ver: AtomicU32,
    pub gain: AF,
    pub mute: AtomicBool,
    pub mono: AtomicBool,
    pub mode: AtomicU32, // 0 normal 1 mono 2 swap 3 inv L 4 inv R 5 L only 6 R only 7 side 8 phase flip 9 side (mono) 10 LFE 11 high-pass 12 widen 13 narrow
    pub dly_ms: AF,
    pub lim_on: AtomicBool,
    pub lim_thr: AF,
    pub mon: AtomicBool, // also heard on A1
    pub c5: crate::vox::C5Params,
    pub peak_l: AF,
    pub peak_r: AF,
}

pub struct Params {
    pub strips: [Strip; MAX_IN],
    pub buses: [Bus; MAX_BUS],
    pub fx: FxParams,
    pub ret_gain: [AF; 2], // external FX returns (RET1 -> reverb return, RET2 -> delay return)
    pub net_rx: [Arc<Ring>; N_RX_ALL],
    pub net_rx_slot: [AtomicI32; N_RX_ALL],
    pub net_target: [Arc<AtomicUsize>; N_RX_ALL], // jitter-buffer size (frames) the mixer keeps per receiver
    pub net_tx: [Arc<Ring>; N_TX_RINGS],
    pub net_tx_bus: [AtomicI32; N_TX_ALL],
    pub net_rx_feed: [AtomicU32; N_RX_ALL],  // per rx ring: left source channel | right source channel << 8 (the same channel twice = mono)
    pub net_rx_ch: [AtomicUsize; N_NET_RX],  // channels the receiver currently sees in the incoming stream (0 = none yet)
    pub net_tx_nch: [AtomicU32; N_NET_TX],   // 0 = legacy single-bus tap (net_tx_bus), else channel matrix mode with this many channels
    pub net_tx_mask: [[AtomicU32; MAX_NET_CH]; N_NET_TX], // per output channel: bit b set = bus b feeds it (buses sum)
    pub net_tx_src: [[AtomicU32; MAX_NET_CH]; N_NET_TX],  // per output channel: 0 = pair position (even ch = L, odd = R), 1 = L, 2 = R, 3 = (L+R)/2
    pub xruns: AtomicU64,
    pub max_block_us: AtomicU64,
    pub sample_rate: AtomicU32,
    pub buffer: AtomicU32,
}

impl Params {
    pub fn new() -> Self {
        Params {
            strips: std::array::from_fn(|_| Strip {
                active: AtomicBool::new(false),
                gain: AF::new(1.0),
                pan: AF::new(0.0),
                mute: AtomicBool::new(false),
                solo: AtomicBool::new(false),
                mono: AtomicBool::new(false),
                send: std::array::from_fn(|_| AtomicBool::new(false)),
                send_gain: std::array::from_fn(|_| AF::new(1.0)),
                eq: default_cells(),
                eq_ver: AtomicU32::new(0),
                comp: CompParams::new(),
                gate: GateParams::new(),
                gr_db: AF::new(0.0),
                rvb_send: AF::new(0.0),
                dns: AF::new(0.0),
                sp_on: AtomicBool::new(false),
                sp_x: AF::new(0.0),
                sp_y: AF::new(1.0),
                dly_send: AF::new(0.0),
                lim_on: AtomicBool::new(false),
                lim_thr: AF::new(1.0),
                ext1: AF::new(0.0),
                ext2: AF::new(0.0),
                ext_post: AtomicBool::new(false),
                voice: crate::vox::VoiceParams::new(),
                mod_on: AtomicBool::new(false),
                mod_rate: AF::new(4.0),
                mod_depth: AF::new(0.5),
                ins_on: AtomicBool::new(false),
                ins_mix: AF::new(1.0),
                kar: AtomicU32::new(0),
                peak_l: AF::new(0.0),
                peak_r: AF::new(0.0),
            }),
            buses: std::array::from_fn(|_| Bus {
                rvb_ret: AF::new(1.0),
                dly_ret: AF::new(1.0),
                eq: default_cells(),
                eq_ver: AtomicU32::new(0),
                gain: AF::new(1.0),
                mute: AtomicBool::new(false),
                mono: AtomicBool::new(false),
                mode: AtomicU32::new(0),
                dly_ms: AF::new(0.0),
                lim_on: AtomicBool::new(false),
                lim_thr: AF::new(1.0),
                mon: AtomicBool::new(false),
                c5: crate::vox::C5Params::new(),
                peak_l: AF::new(0.0),
                peak_r: AF::new(0.0),
            }),
            fx: FxParams::new(),
            ret_gain: [AF::new(1.0), AF::new(1.0)],
            net_rx: std::array::from_fn(|_| Arc::new(Ring::new())),
            net_rx_slot: std::array::from_fn(|_| AtomicI32::new(-1)),
            net_target: std::array::from_fn(|_| Arc::new(AtomicUsize::new(0))),
            net_tx: std::array::from_fn(|_| Arc::new(Ring::new())),
            net_tx_bus: std::array::from_fn(|_| AtomicI32::new(-1)),
            net_rx_feed: std::array::from_fn(|i| AtomicU32::new({ let f = (i % RX_FEEDS) as u32; (2 * f % 8) | ((2 * f + 1) % 8) << 8 })),
            net_rx_ch: std::array::from_fn(|_| AtomicUsize::new(0)),
            net_tx_nch: std::array::from_fn(|_| AtomicU32::new(0)),
            net_tx_mask: std::array::from_fn(|_| std::array::from_fn(|_| AtomicU32::new(0))),
            net_tx_src: std::array::from_fn(|_| std::array::from_fn(|_| AtomicU32::new(0))),
            xruns: AtomicU64::new(0),
            max_block_us: AtomicU64::new(0),
            sample_rate: AtomicU32::new(48000),
            buffer: AtomicU32::new(0),
        }
    }
}

pub fn db_to_lin(db: f32) -> f32 {
    if db <= -90.0 {
        0.0
    } else {
        10f32.powf(db / 20.0)
    }
}

/// Zeroed buffer whose pages are already touched (no page faults later in the realtime callback).
pub fn zeros(n: usize) -> Vec<f32> {
    let mut v = Vec::with_capacity(n);
    v.resize(n, 0.0);
    v
}
