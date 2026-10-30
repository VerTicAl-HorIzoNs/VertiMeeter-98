//! WAV recorder: taps one bus (mixer tap index REC) and writes 32-bit float stereo 48 kHz.
//! Used where the GUI cannot record with an external tool (Windows). The header is fixed up every second, so a
//! crash still leaves a playable file.
use crate::params::*;
use std::fs::File;
use std::io::{Seek, SeekFrom, Write};
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex};

struct Job {
    stop: Arc<AtomicBool>,
    h: std::thread::JoinHandle<u64>,
}
fn job() -> &'static Mutex<Option<Job>> {
    static J: Mutex<Option<Job>> = Mutex::new(None);
    &J
}

fn header(f: &mut File, data_bytes: u64) -> std::io::Result<()> {
    let d = data_bytes.min(u32::MAX as u64 - 64) as u32;
    let mut h = Vec::with_capacity(44);
    h.extend_from_slice(b"RIFF");
    h.extend_from_slice(&(36 + d).to_le_bytes());
    h.extend_from_slice(b"WAVEfmt ");
    h.extend_from_slice(&16u32.to_le_bytes());
    h.extend_from_slice(&3u16.to_le_bytes()); // IEEE float
    h.extend_from_slice(&2u16.to_le_bytes());
    h.extend_from_slice(&48000u32.to_le_bytes());
    h.extend_from_slice(&(48000u32 * 8).to_le_bytes());
    h.extend_from_slice(&8u16.to_le_bytes());
    h.extend_from_slice(&32u16.to_le_bytes());
    h.extend_from_slice(b"data");
    h.extend_from_slice(&d.to_le_bytes());
    f.seek(SeekFrom::Start(0))?;
    f.write_all(&h)
}

pub fn start(p: &Arc<Params>, bus: usize, path: &str) -> Result<(), String> {
    if bus >= MAX_BUS {
        return Err("bad bus".into());
    }
    let mut g = job().lock().unwrap();
    if g.is_some() {
        return Err("already recording".into());
    }
    let mut f = File::create(path).map_err(|e| format!("cannot create {path}: {e}"))?;
    header(&mut f, 0).map_err(|e| e.to_string())?;
    let ring = p.net_tx[REC_RING].clone();
    ring.skip(usize::MAX);
    let stop = Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    let h = std::thread::spawn(move || {
        let (mut l, mut r) = (vec![0.0f32; 4800], vec![0.0f32; 4800]);
        let mut buf: Vec<u8> = Vec::with_capacity(4800 * 8);
        let (mut frames, mut last) = (0u64, std::time::Instant::now());
        loop {
            let done = st.load(Relaxed);
            let n = ring.pop(&mut l, &mut r);
            if n > 0 {
                buf.clear();
                for i in 0..n {
                    buf.extend_from_slice(&l[i].to_le_bytes());
                    buf.extend_from_slice(&r[i].to_le_bytes());
                }
                if f.seek(SeekFrom::End(0)).and_then(|_| f.write_all(&buf)).is_err() {
                    break;
                }
                frames += n as u64;
            } else if done {
                break;
            } else {
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            if last.elapsed().as_secs() >= 1 {
                last = std::time::Instant::now();
                let _ = header(&mut f, frames * 8);
            }
        }
        let _ = header(&mut f, frames * 8);
        frames
    });
    p.net_tx_bus[REC].store(bus as i32, Relaxed);
    *g = Some(Job { stop, h });
    Ok(())
}

pub fn stop(p: &Arc<Params>) -> Option<u64> {
    p.net_tx_bus[REC].store(-1, Relaxed);
    let j = job().lock().unwrap().take()?;
    j.stop.store(true, Relaxed);
    j.h.join().ok()
}
