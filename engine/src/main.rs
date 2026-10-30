mod backend;
mod codec;
#[cfg(feature = "cpal-backend")]
mod cpal_io;
mod ctl;
mod dsp;
mod feed;
mod fx;
mod mix;
mod net;
mod oban;
mod params;
mod rec;
#[cfg(feature = "soft-backend")]
mod soft;
mod vox;

#[cfg(not(feature = "soft-backend"))]
use jack::{Client, ClientOptions, Control, NotificationHandler};
#[cfg(not(feature = "soft-backend"))]
use params::*;
#[cfg(not(feature = "soft-backend"))]
use std::sync::atomic::Ordering::Relaxed;
#[cfg(not(feature = "soft-backend"))]
use std::sync::Arc;

#[cfg(not(feature = "soft-backend"))]
struct Notes(Arc<Params>);
#[cfg(not(feature = "soft-backend"))]
impl NotificationHandler for Notes {
    fn xrun(&mut self, _: &Client) -> Control {
        self.0.xruns.fetch_add(1, Relaxed);
        Control::Continue
    }
    fn sample_rate(&mut self, _: &Client, srate: jack::Frames) -> Control {
        self.0.sample_rate.store(srate, Relaxed);
        Control::Continue
    }
}

#[cfg(feature = "soft-backend")]
fn main() {
    let spec = std::env::args().nth(1).unwrap_or_else(|| "tcp:vertimeeter-engine.port".into());
    soft::run(&spec);
}

#[cfg(not(feature = "soft-backend"))]
fn main() {
    let sock = std::env::args().nth(1).unwrap_or_else(|| {
        let dir = std::env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
        format!("{dir}/vertimeeter-engine.sock")
    });
    // Do not let this client auto-connect to anything; the GUI owns all links.
    std::env::set_var("PIPEWIRE_PROPS", "{ node.autoconnect = false }");
    let (client, _) = Client::new(&std::env::var("VERTIMEETER_CLIENT").unwrap_or_else(|_| "VertiMeeter".into()), ClientOptions::NO_START_SERVER)
        .expect("cannot open JACK/PipeWire client (is pipewire-jack installed?)");
    std::env::remove_var("PIPEWIRE_PROPS");
    let p = Arc::new(Params::new());
    p.sample_rate.store(client.sample_rate() as u32, Relaxed);
    p.buffer.store(client.buffer_size(), Relaxed);

    let mut ins = Vec::new();
    for i in 0..MAX_IN {
        ins.push((
            client.register_port(&format!("S{}_L", i + 1), jack::AudioIn::default()).unwrap(),
            client.register_port(&format!("S{}_R", i + 1), jack::AudioIn::default()).unwrap(),
        ));
    }
    let mut outs = Vec::new();
    for b in 0..MAX_BUS {
        let n = if b < 8 { format!("A{}", b + 1) } else { format!("B{}", b - 7) };
        outs.push((
            client.register_port(&format!("{n}_L"), jack::AudioOut::default()).unwrap(),
            client.register_port(&format!("{n}_R"), jack::AudioOut::default()).unwrap(),
        ));
    }
    let mut ext = Vec::new();
    let mut ret = Vec::new();
    for k in 0..2 {
        ext.push((
            client.register_port(&format!("EXT{}_L", k + 1), jack::AudioOut::default()).unwrap(),
            client.register_port(&format!("EXT{}_R", k + 1), jack::AudioOut::default()).unwrap(),
        ));
        ret.push((
            client.register_port(&format!("RET{}_L", k + 1), jack::AudioIn::default()).unwrap(),
            client.register_port(&format!("RET{}_R", k + 1), jack::AudioIn::default()).unwrap(),
        ));
    }
    // insert FX loops: any JACK / PipeWire effect (a plugin host, jalv, Carla...) can sit inside a strip
    let mut ins_out = Vec::new();
    let mut ins_in = Vec::new();
    for k in 0..N_STRIPS + 1 {
        ins_out.push((
            client.register_port(&format!("INS{}_OUT_L", k + 1), jack::AudioOut::default()).unwrap(),
            client.register_port(&format!("INS{}_OUT_R", k + 1), jack::AudioOut::default()).unwrap(),
        ));
        ins_in.push((
            client.register_port(&format!("INS{}_IN_L", k + 1), jack::AudioIn::default()).unwrap(),
            client.register_port(&format!("INS{}_IN_R", k + 1), jack::AudioIn::default()).unwrap(),
        ));
    }
    let mixer = mix::Mixer::new(p.clone(), ins, outs, ext, ret, ins_out, ins_in, client.sample_rate() as f32);
    let active = client.activate_async(Notes(p.clone()), mixer).expect("activate failed");
    eprintln!(
        "engine up: {} Hz, {} frames, socket {}",
        p.sample_rate.load(Relaxed),
        p.buffer.load(Relaxed),
        sock
    );
    ctl::serve(&sock, p); // blocks (control thread is not the audio thread)
    drop(active);
}
