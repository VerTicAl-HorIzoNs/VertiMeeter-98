//! Which audio backend the mixer talks to. The mixer (mix.rs) only needs these six names.
//!  - default (Linux): JACK / pipewire-jack, the GUI links ports with pw-link
//!  - soft-backend (Windows, tests): software ports + our own mixer thread (soft.rs), devices via cpal (cpal_io.rs)
#[cfg(not(feature = "soft-backend"))]
pub use jack::{AudioIn, AudioOut, Client, Control, Port, ProcessHandler, ProcessScope};
#[cfg(feature = "soft-backend")]
pub use crate::soft::{AudioIn, AudioOut, Client, Control, Port, ProcessHandler, ProcessScope};
