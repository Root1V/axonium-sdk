//! Retry policy and the cooldown registry.
//!
//! The gateway already retries against the backend before it returns anything, and runs its own
//! per-backend circuit breaker. Retrying symmetrically on top of that would multiply load on a
//! struggling backend, so this layer is deliberately narrow.
//!
//! **There is no idempotency by default in this API.** A retried chat, embeddings or image request
//! is a genuinely new generation: billable again, not a replay. So the default policy retries only
//! where the platform says *no generation happened* -- a rate limit, or a circuit breaker that
//! fast-failed without ever calling the backend. An `Idempotency-Key` changes that for timeouts,
//! and only for timeouts, because it is the only thing that makes a repeat free.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::error::{ApiError, ErrorKind};

/// Errors where the platform fast-failed without reaching a model, so retrying cannot duplicate a
/// generation or double-bill.
fn no_generation_occurred(kind: ErrorKind) -> bool {
    matches!(
        kind,
        ErrorKind::RateLimitExceeded
            | ErrorKind::BackendUnavailable
            | ErrorKind::RateLimitingUnavailable
            | ErrorKind::UsageStoreUnavailable
            // The first request with this key is still running, so a repeat waits, replays, or is
            // refused again. It cannot start a second generation.
            | ErrorKind::IdempotencyInProgress
    )
}

/// When to retry, and how long to wait.
#[derive(Debug, Clone, Copy)]
pub struct RetryPolicy {
    pub max_attempts: u32,
    /// Base delay when the platform supplies no `Retry-After`. Doubles per attempt.
    pub initial_backoff: Duration,
    /// The longest this SDK blocks inside a single call. Also caps a server-supplied
    /// `Retry-After`: a longer wait is handed back rather than slept through, because blocking a
    /// caller for minutes is worse than telling them.
    pub max_backoff: Duration,
    /// Spread retries so callers recovering from one outage do not resynchronise.
    pub jitter: bool,
    /// Off by default. A `502` means the gateway's own attempts already failed, and unlike the
    /// fast-fail errors the request may have reached a model -- so a retry is a second billable
    /// generation.
    pub retry_upstream_errors: bool,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            initial_backoff: Duration::from_secs(1),
            max_backoff: Duration::from_secs(60),
            jitter: true,
            retry_upstream_errors: false,
        }
    }
}

impl RetryPolicy {
    /// How long to wait before `attempt + 1`, or `None` to give up. `attempt` is 1-based and
    /// counts the request that just failed.
    pub(crate) fn delay_for(&self, error: &ApiError, attempt: u32) -> Option<Duration> {
        if attempt >= self.max_attempts || !error.retryable() {
            return None;
        }
        if error.kind == ErrorKind::UpstreamError {
            // Capped at a single extra attempt regardless of max_attempts.
            if !self.retry_upstream_errors || attempt > 1 {
                return None;
            }
        } else if !no_generation_occurred(error.kind) {
            return None;
        }

        if let Some(seconds) = error.retry_after {
            // Server-supplied and authoritative: for an open circuit breaker it is the real
            // expected recovery time, which no local heuristic can improve on.
            let wait = Duration::from_secs_f64(seconds.max(0.0));
            return (wait <= self.max_backoff).then_some(wait);
        }
        Some(self.backoff(attempt))
    }

    pub(crate) fn backoff(&self, attempt: u32) -> Duration {
        let doubled = self
            .initial_backoff
            .saturating_mul(1u32 << attempt.saturating_sub(1).min(16));
        let capped = doubled.min(self.max_backoff);
        if !self.jitter {
            return capped;
        }
        // Deterministic enough for spreading load without pulling in a random-number crate: the
        // nanosecond clock is the only entropy this needs.
        let spread = Instant::now().elapsed().subsec_nanos() as u64;
        let jitter_ns = capped.as_nanos() as u64 / 2;
        capped - Duration::from_nanos(jitter_ns.saturating_sub(spread % jitter_ns.max(1)))
    }
}

/// Remembers server-supplied waits so a known-open circuit is not hammered.
///
/// Keyed by model, because `503 backend-unavailable` is a per-model condition: it means every
/// instance of *that* model is out, and other models on the same gateway keep serving. Cooling by
/// gateway would refuse requests the platform would have answered.
///
/// Only ever populated from a wait the platform supplied. This never invents one, and never
/// guesses that a backend is unhealthy from local failure counts.
#[derive(Debug, Default)]
pub(crate) struct CooldownRegistry {
    until: Mutex<HashMap<String, Instant>>,
}

impl CooldownRegistry {
    pub(crate) fn remaining(&self, key: &str) -> Duration {
        let mut map = self.until.lock().unwrap();
        match map.get(key) {
            Some(&until) => {
                let left = until.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    map.remove(key);
                }
                left
            }
            None => Duration::ZERO,
        }
    }

    pub(crate) fn note(&self, key: &str, error: &ApiError) {
        if error.kind != ErrorKind::BackendUnavailable {
            return;
        }
        let Some(seconds) = error.retry_after.filter(|s| *s > 0.0) else {
            return;
        };
        self.until.lock().unwrap().insert(
            key.to_string(),
            Instant::now() + Duration::from_secs_f64(seconds),
        );
    }

    pub(crate) fn clear(&self, key: &str) {
        self.until.lock().unwrap().remove(key);
    }
}
