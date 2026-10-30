# OBAN packet format, version 1

OBAN ("Open VBAN-compatible networking") is VertiMeeter's network audio format. It is not affiliated with or endorsed by VB-Audio.
VBAN is VB-Audio's protocol. Its base (PCM audio, TEXT) is published and may be used "AS IS". OBAN reuses that public header layout with its own magic and adds codecs of its own. It never uses VB-Audio's closed codecs, names or logos.
No code from other VBAN implementations was copied; this was written from the published specification.

## Header (28 bytes, little endian)
| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| 0 | 4 | magic | `VBAN` or `OBAN` |
| 4 | 1 | format_SR | bits 0-4: sample rate index, bits 5-7: sub-protocol (0x00 audio, 0x40 text, 0x60 service) |
| 5 | 1 | format_nbs | samples per channel in this packet minus 1 (Opus: frame length in ms minus 1) |
| 6 | 1 | format_nbc | channels minus 1 |
| 7 | 1 | format_bit | bits 0-2: PCM sample format, bits 4-7: codec |
| 8 | 16 | stream name | ASCII, zero padded |
| 24 | 4 | frame counter | +1 per packet, wraps |

Maximum datagram: 1436 bytes (28 header + at most 1408 payload).

## Sample rate index
0 6000, 1 12000, 2 24000, 3 48000, 4 96000, 5 192000, 6 384000, 7 8000, 8 16000, 9 32000, 10 64000, 11 128000, 12 256000, 13 512000, 14 11025, 15 22050, 16 44100, 17 88200, 18 176400, 19 352800, 20 705600.

## PCM sample format (bits 0-2 of format_bit)
0 unsigned 8-bit, 1 signed 16-bit, 2 signed 24-bit, 3 signed 32-bit, 4 float 32, 5 float 64. Samples are interleaved. Formats 6 and 7 (12 / 10 bit) are not supported.

## Codec (bits 4-7 of format_bit)
| ID | Name | Magic | Sent by VertiMeeter |
| --- | --- | --- | --- |
| 0x00 | PCM | VBAN or OBAN | yes (the two packets are identical except the magic) |
| 0x10 | VB-Audio VBCA | any | never. Recognised and refused |
| 0x20 | VB-Audio VBCV | any | never. Recognised and refused |
| 0xF0 | VBAN USER | any | never. Recognised and refused |
| 0x40 | Opus, music profile | OBAN only | yes |
| 0x50 | Opus, voice profile | OBAN only | yes |
| 0x60 | VFLAC (VERTIFLAC, lossless) | OBAN only | yes |
| 0x70 | mu-law | OBAN only | yes |

Any other codec ID is refused. The own IDs (0x40-0x70) are only valid under the OBAN magic; under the VBAN magic they are "unknown codec".

## Opus (0x40 / 0x50)
- One Opus packet per datagram, 48 kHz (format_SR index 3), 1 or 2 channels, frame length 5, 10, 20 or 40 ms (`nbs = ms - 1`).
- Music profile: libopus application AUDIO, signal MUSIC. Voice profile: application VOIP, signal VOICE, in-band FEC on, expected loss 10%.
- The receiver recovers one lost packet from the next packet's FEC data when there is any, and uses libopus concealment otherwise.

## VFLAC (0x60, low 3 bits = 1 for 16-bit or 2 for 24-bit)
One self-contained frame per datagram, 1 or 2 channels, up to 256 samples per channel (the sender splits a frame that does not fit):
- byte 0: mode. 0 independent channels, 1 mid / side (mid = (L+R)>>1, side = L-R), 2 raw interleaved samples.
- modes 0 and 1, per channel: one byte (`order << 5 | k`), `order` (0-3) warm-up samples as 32-bit little endian, then the residuals of a fixed polynomial predictor (order 0: x, 1: x-x1, 2: x-2*x1+x2, 3: x-3*x1+3*x2-x3), zig-zag mapped and Rice coded with parameter k (unary quotient of zeros ended by a one, then k low bits), padded to a byte.
- mode 2: raw samples, 2 or 3 bytes each.

## mu-law (0x70)
ITU G.711 mu-law, one byte per sample, interleaved if stereo, sample rate from format_SR (8000 typical).

## Sub-protocols
- 0x00 audio: above.
- Channels: `format_nbc` + 1 channels, interleaved. PCM (VBAN or OBAN magic) may carry 1-8 channels (VertiMeeter reads and sends up to 8; more are cut to the first 8). Opus, VFLAC and mu-law are 1-2 channels. Samples of all channels of one frame are adjacent (frame 0 ch 0, ch 1 ... then frame 1).
- 0x40 text (VBAN magic only, stream name `Command1`): UTF-8 commands separated by `;`. VertiMeeter understands `Strip[n].Gain|Mute|Solo|Mono=value` and `Bus[n].Gain|Mute|Mono=value` (Bus[0..7] = A1-A5, B1-B3 like Voicemeeter Potato, 8-10 = A6-A8, 11-15 = B4-B8) and ignores parts starting with `//` or `#`. Off by default; only accepted from the configured sender IP.
- 0x60 service, identification (nbc 0), PING0 request (nbs 0): answered with the reply bit (nbs 0x80), the same frame counter and a 676-byte PING0 structure. Off by default. Never answered when the request is shorter than the reply.
- Other sub-protocols (serial / MIDI, user) are ignored.

## Receiver rules
- One UDP socket per input. The first four bytes pick the path (`VBAN` or `OBAN`); anything else is dropped. Every length is checked before use.
- Lock to the first sender (or only the configured IP) until it has been silent for 3 s. Optional stream-name filter.
- Frame counters are re-ordered inside the jitter buffer window. Late and duplicate packets are dropped. A jump of more than 4000 counts restarts the stream.
- Gaps are filled: Opus FEC then concealment, otherwise silence of the same length so timing stays right. More than 40 missing packets in a row resynchronises instead.
- The receiver never sends anything back for audio packets, whatever codec they use.

## Security
There is no encryption and no authentication in VBAN or OBAN. Run it only on a private network or a VPN such as Tailscale.
