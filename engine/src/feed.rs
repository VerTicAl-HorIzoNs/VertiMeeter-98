//! Local PCM feed: one TCP connection (127.0.0.1, token-checked) carries raw f32 little-endian stereo 48 kHz frames into
//! a strip slot. The media player uses it on Windows (ffmpeg decodes, Python sends). The engine answers with the ring
//! fill (u32 LE frames) after every read, and every 5 ms when idle, so the sender can pace itself closed-loop.
use crate::params::*;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

pub const TARGET_FRAMES: usize = 4800;

fn port_cell() -> &'static OnceLock<u16> {
    static P: OnceLock<u16> = OnceLock::new();
    &P
}

fn client(mut s: TcpStream, p: Arc<Params>) {
    let _ = s.set_nodelay(true);
    if let Ok(tok) = std::env::var("VERTIMEETER_TOKEN") {
        let _ = s.set_read_timeout(Some(Duration::from_secs(3)));
        let mut line = Vec::new();
        let mut b = [0u8; 1];
        while line.len() < 200 {
            match s.read(&mut b) {
                Ok(1) if b[0] != b'\n' => line.push(b[0]),
                _ => break,
            }
        }
        if line != format!("AUTH {tok}").as_bytes() {
            let _ = s.shutdown(Shutdown::Both);
            return;
        }
    }
    let _ = s.set_read_timeout(Some(Duration::from_millis(5)));
    let ring = p.net_rx[FEED].clone();
    let mut buf = vec![0u8; 16384];
    let mut carry: Vec<u8> = Vec::new();
    let (mut l, mut r) = (Vec::<f32>::new(), Vec::<f32>::new());
    loop {
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                carry.extend_from_slice(&buf[..n]);
                let frames = carry.len() / 8;
                l.clear();
                r.clear();
                for i in 0..frames {
                    let o = i * 8;
                    l.push(f32::from_le_bytes([carry[o], carry[o + 1], carry[o + 2], carry[o + 3]]));
                    r.push(f32::from_le_bytes([carry[o + 4], carry[o + 5], carry[o + 6], carry[o + 7]]));
                }
                carry.drain(..frames * 8);
                ring.push(&l, &r);
            }
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(_) => break,
        }
        let ack = (ring.len().min(u32::MAX as usize) as u32).to_le_bytes();
        if s.write_all(&ack).is_err() {
            break;
        }
    }
}

/// Starts the listener once and binds the feed to `slot`. Returns the port.
pub fn open(p: &Arc<Params>, slot: usize) -> Result<u16, String> {
    p.net_target[FEED].store(TARGET_FRAMES, Relaxed);
    p.net_rx_slot[FEED].store(slot as i32, Relaxed);
    if let Some(port) = port_cell().get() {
        return Ok(*port);
    }
    let l = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
    let port = l.local_addr().map_err(|e| e.to_string())?.port();
    let _ = port_cell().set(port);
    let p2 = p.clone();
    std::thread::spawn(move || {
        for c in l.incoming().flatten() {
            let p3 = p2.clone();
            std::thread::spawn(move || client(c, p3));
        }
    });
    Ok(port)
}
