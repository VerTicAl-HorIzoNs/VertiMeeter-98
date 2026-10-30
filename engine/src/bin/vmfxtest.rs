//! Tiny JACK effect used by the tests and as a template: copies its input to its output with a fixed gain.
//! usage: vmfxtest [gain_db] [client_name]   ports: in_l in_r out_l out_r
use jack::{AudioIn, AudioOut, Client, ClientOptions, ClosureProcessHandler, Control};

fn main() {
    let db: f32 = std::env::args().nth(1).and_then(|a| a.parse().ok()).unwrap_or(-6.0);
    let name = std::env::args().nth(2).unwrap_or_else(|| "VMFXTEST".into());
    std::env::set_var("PIPEWIRE_PROPS", "{ node.autoconnect = false }");
    let (client, _) = Client::new(&name, ClientOptions::NO_START_SERVER).expect("no JACK/PipeWire");
    let g = 10f32.powf(db / 20.0);
    let il = client.register_port("in_l", AudioIn::default()).unwrap();
    let ir = client.register_port("in_r", AudioIn::default()).unwrap();
    let mut ol = client.register_port("out_l", AudioOut::default()).unwrap();
    let mut or_ = client.register_port("out_r", AudioOut::default()).unwrap();
    let h = ClosureProcessHandler::new(move |_c: &Client, ps: &jack::ProcessScope| {
        for (o, i) in ol.as_mut_slice(ps).iter_mut().zip(il.as_slice(ps)) {
            *o = i * g;
        }
        for (o, i) in or_.as_mut_slice(ps).iter_mut().zip(ir.as_slice(ps)) {
            *o = i * g;
        }
        Control::Continue
    });
    let _a = client.activate_async((), h).unwrap();
    eprintln!("vmfxtest up ({name}, {db} dB)");
    loop {
        std::thread::sleep(std::time::Duration::from_secs(3600));
    }
}
