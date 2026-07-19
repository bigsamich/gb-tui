use std::collections::VecDeque;
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

pub const SAMPLE_RATE: u32 = 44100;
pub const CHANNELS: u16 = 2;

pub struct AudioRing {
    buf: Mutex<VecDeque<i16>>,
    space: Condvar,
    capacity: usize,
}

impl AudioRing {
    pub fn new(capacity: usize) -> Arc<Self> {
        Arc::new(Self {
            buf: Mutex::new(VecDeque::with_capacity(capacity)),
            space: Condvar::new(),
            capacity,
        })
    }

    /// Blocks while the ring is full — this back-pressure is the emulation clock.
    pub fn push_blocking(&self, samples: &[i16], timeout: Duration) -> bool {
        let mut buf = self.buf.lock().unwrap();
        let mut remaining = samples;
        let deadline = std::time::Instant::now() + timeout;
        while !remaining.is_empty() {
            let free = self.capacity.saturating_sub(buf.len());
            if free == 0 {
                let now = std::time::Instant::now();
                if now >= deadline {
                    return false;
                }
                let (guard, res) = self.space.wait_timeout(buf, deadline - now).unwrap();
                buf = guard;
                if res.timed_out() && self.capacity.saturating_sub(buf.len()) == 0 {
                    return false;
                }
                continue;
            }
            let n = free.min(remaining.len());
            buf.extend(&remaining[..n]);
            remaining = &remaining[n..];
        }
        true
    }

    /// Called from the cpal callback: fill `out`, zero-padding on underrun.
    pub fn pop_into_f32(&self, out: &mut [f32]) {
        let mut buf = self.buf.lock().unwrap();
        for slot in out.iter_mut() {
            *slot = match buf.pop_front() {
                Some(s) => s as f32 / 32768.0,
                None => 0.0,
            };
        }
        drop(buf);
        self.space.notify_one();
    }

    pub fn clear(&self) {
        self.buf.lock().unwrap().clear();
        self.space.notify_one();
    }

    pub fn len(&self) -> usize {
        self.buf.lock().unwrap().len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Real audio output. Keep on the main thread: `cpal::Stream` is `!Send`.
pub struct AudioOutput {
    _stream: cpal::Stream,
    pub ring: Arc<AudioRing>,
}

impl AudioOutput {
    /// None if no device / no 44.1kHz stereo f32 config — caller falls back to muted.
    pub fn try_new() -> Option<Self> {
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
        let device = cpal::default_host().default_output_device()?;
        let config = device
            .supported_output_configs()
            .ok()?
            .find(|c| {
                c.channels() == CHANNELS
                    && c.sample_format() == cpal::SampleFormat::F32
                    && c.min_sample_rate() <= SAMPLE_RATE
                    && c.max_sample_rate() >= SAMPLE_RATE
            })?
            .with_sample_rate(SAMPLE_RATE);
        // ~100ms of stereo audio
        let ring = AudioRing::new((SAMPLE_RATE as usize / 10) * CHANNELS as usize);
        let cb_ring = Arc::clone(&ring);
        let stream = device
            .build_output_stream(
                config.config(),
                move |out: &mut [f32], _| cb_ring.pop_into_f32(out),
                |err| eprintln!("audio stream error: {err}"),
                None,
            )
            .ok()?;
        stream.play().ok()?;
        Some(Self {
            _stream: stream,
            ring,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn push_pop_round_trip_converts_to_f32() {
        let ring = AudioRing::new(8);
        assert!(ring.push_blocking(&[0, 16384, -16384, 32767], Duration::from_millis(10)));
        let mut out = [9.0f32; 4];
        ring.pop_into_f32(&mut out);
        assert_eq!(out[0], 0.0);
        assert!((out[1] - 0.5).abs() < 1e-3);
        assert!((out[2] + 0.5).abs() < 1e-3);
        assert!(ring.is_empty());
    }

    #[test]
    fn underrun_pads_with_silence() {
        let ring = AudioRing::new(8);
        ring.push_blocking(&[100, 200], Duration::from_millis(10));
        let mut out = [9.0f32; 4];
        ring.pop_into_f32(&mut out);
        assert_eq!(out[2], 0.0);
        assert_eq!(out[3], 0.0);
    }

    #[test]
    fn push_blocks_until_consumer_drains() {
        let ring = AudioRing::new(4);
        assert!(ring.push_blocking(&[1, 2, 3, 4], Duration::from_millis(10)));
        // full: times out
        assert!(!ring.push_blocking(&[5, 6], Duration::from_millis(50)));
        // consumer thread drains after 30ms; push waits and then succeeds
        let r2 = std::sync::Arc::clone(&ring);
        let t = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(30));
            let mut out = [0.0f32; 4];
            r2.pop_into_f32(&mut out);
        });
        assert!(ring.push_blocking(&[5, 6], Duration::from_secs(2)));
        t.join().unwrap();
    }

    #[test]
    fn clear_empties_buffer() {
        let ring = AudioRing::new(8);
        ring.push_blocking(&[1, 2, 3], Duration::from_millis(10));
        ring.clear();
        assert!(ring.is_empty());
    }
}
