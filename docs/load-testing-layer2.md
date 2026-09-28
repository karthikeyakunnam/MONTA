# Load testing the ingest path (Layer 2)

What to measure, what will break first, and what the numbers should be. Written from the
code as it stands, not from a target we would like to have.

## Why the usual load test is wrong here

A generic HTTP load test — thousands of small JSON requests — tells you almost nothing about
this endpoint. Upload traffic is different in three ways that change what saturates first:

1. **Requests are long and byte-bound.** A 500 MB upload on a 50 Mbit uplink occupies a
   connection for ~80 seconds. Concurrency is limited by *connection-seconds*, not by
   requests per second.
2. **Every accepted file forks a process.** `ffprobe` runs once per upload. That is CPU and
   file-descriptor pressure that no amount of async concurrency removes.
3. **Disk is in the request path.** Bytes are written synchronously (in a thread) before the
   response. Throughput is bounded by the slower of network-in and disk-write.

So the load profile must be built from real file sizes over real connections, with the probe
enabled. A test that posts 1 KB files with ffprobe stubbed out measures the framework, not
the system.

## The four limits, in the order they bite

| # | Limit | Where it is | Symptom when hit | Headroom knob |
|---|---|---|---|---|
| 1 | ffprobe process slots | `MetadataExtractor.extract`, capped by `PROBE_MAX_CONCURRENCY` | Upload latency climbs as probes queue; CPU pinned | Raise `PROBE_MAX_CONCURRENCY` only with cores to match |
| 2 | Disk write throughput | `LocalMediaStorage.save_stream`, 1 MiB chunks via `asyncio.to_thread` | `UPLOAD_DURATION` rises with no CPU increase; `iostat` shows the device at 100% util | Faster storage; separate volume from the database |
| 3 | Event-loop thread pool | `asyncio.to_thread` for every chunk write and every probe wait | Everything slows at once, including `/health`; the default pool is `min(32, cpu+4)` threads | Raise the executor size, or move storage to an object store with an async client |
| 4 | Database write contention | `get_locked` takes a row lock per upload on PostgreSQL | Uploads to the *same project* serialise; different projects are unaffected | Nothing to tune — this is the quota invariant, and it is per project |

Limit 1 is first on any machine with fewer than ~16 cores. Nothing before it is worth tuning.

**A note on limit 4:** the row lock is deliberate. Two parallel uploads to one project must
not both read "19 clips used" and both be accepted into a 20-clip budget. Serialising per
project is the price of the quota being correct, and it does not serialise across projects.

## What is already bounded, and what is not

Verified by tests in `tests/gateway/`:

* **Memory per upload is constant** — `test_streaming_memory_is_bounded_by_the_chunk_size`
  asserts the peak stays within a few 1 MiB buffers over a 12 MiB upload. Memory scales with
  *concurrent uploads*, not with file size: budget ~2–4 MiB per in-flight upload.
* **An oversized body is cut off mid-stream** —
  `test_oversized_stream_is_cut_off_instead_of_being_written_whole` asserts at most one chunk
  past the limit is written. A client lying in `Content-Length` cannot fill the disk.
* **A dropped connection leaves nothing behind** — staged writes plus `os.replace`, so no
  partial file is ever visible and no temp file is orphaned.
* **A hung probe is killed with its process group** — `test_a_hanging_probe_is_killed_at_the_timeout`.

* **Concurrent probes are capped** — `PROBE_MAX_CONCURRENCY` (default: core count, clamped to
  2–8) gates `MetadataExtractor.extract`, asserted by `test_concurrent_probes_are_capped`.
  Fifty simultaneous uploads queue for probe slots instead of forking fifty processes. This
  matters more than it sounds: uncapped, every probe gets slower together and the slowest trip
  `PROBE_TIMEOUT_SECONDS`, so a load spike would produce *rejected* uploads rather than merely
  slow ones. Queued waiting shows up as latency, which is the honest failure mode.

**What is still unbounded:** total in-flight uploads. Nothing stops 500 concurrent requests
from each holding a 1 MiB buffer and a staged temp file; the probe cap limits CPU, not sockets
or disk. That belongs at the edge (a reverse-proxy connection limit), not in application code,
and it is the one thing to configure before opening the endpoint to the internet.

## The load profile to run

Model a real session rather than a flat rate. A creator uploads a burst of clips, waits, then
starts processing.

| Parameter | Value | Why |
|---|---|---|
| File size mix | 60% 20–60 MB (phone 1080p), 30% 100–200 MB (1080p long), 10% 300–500 MB (4K) | Matches what the quota classes are sized for |
| Files per session | 8–20, uploaded 2 at a time | The frontend's `MAX_PARALLEL = 2` |
| Think time between bursts | 30–120 s | Creators review before uploading more |
| Concurrent sessions | Ramp 1 → 50 → 200 | Find the knee, do not assume it |
| Duplicate rate | 10–15% | Re-dragging a folder is common, and dedupe skips the probe — without this the test overstates probe load |
| Rejection rate | ~5% (one `.exe`, one truncated file) | The rejection path writes a row and deletes bytes; it must not be slower than the accept path |

Drive it with a tool that streams from disk rather than buffering in memory — `k6` with
`http.file()`, or `vegeta` with a multipart body — otherwise the load generator hits its own
memory limit before the server does.

## Pass criteria

Measured at the knee, not at one user:

* p95 time-to-verdict ≤ (bytes ÷ available bandwidth) + **2 s**. The 2 s is the server's own
  budget: hash, probe, validate, write the row. If the overhead exceeds that, limit 1 or 2 is
  saturated.
* p99 ffprobe wall time < 5 s, and **zero** `PROBE_DURATION` observations near
  `PROBE_TIMEOUT_SECONDS`.
* Zero 5xx. Rejections are 422 and are expected; a 500 means a code path is unguarded.
* `.incoming` is empty when the run ends, and `du` of the storage root equals the sum of
  accepted file sizes. Anything more is a leak.
* `/health` still answers in < 500 ms under full load — if it does not, the thread pool is
  starved (limit 3) and orchestration will start declaring the API dead.
* Memory flat after the ramp. A rising baseline across bursts means a buffer is being retained.

## Metrics to watch

Already emitted (`shared/observability/catalog.py`), so no instrumentation work is needed:

`UPLOAD_DURATION{outcome}` · `UPLOAD_BYTES` · `UPLOAD_ACCEPTED{resolution_class}` ·
`UPLOAD_REJECTED{reason}` · `UPLOAD_DEDUPE{scope}` · `PROBE_DURATION` ·
`JOBS_SUBMITTED{kind,outcome}` · `JOB_STATE{kind,state}` · `WS_CONNECTIONS`

The two that answer "what broke": `PROBE_DURATION` (limit 1) and `UPLOAD_DURATION` with a flat
`PROBE_DURATION` (limit 2). `UPLOAD_REJECTED{reason}` rising during a load test usually means
the *test* is malformed, not the server.

## Failure injection worth running

Each has a functional test proving the intended behaviour; the load test is where you find
out whether it still holds under pressure.

| Inject | Expected |
|---|---|
| Kill Redis mid-run | Uploads keep succeeding (progress is best-effort); `POST /pipeline` returns 503 with a `submit_failed` job row; websockets close 1011 and clients fall back to polling |
| Kill PostgreSQL mid-run | Uploads fail with 5xx and **no** orphaned files in `.incoming` after recovery |
| Fill the disk | Uploads fail cleanly; no partial file appears under a final key |
| `chmod -w` the storage root | `/health` reports `storage: false` before the next upload is attempted |
| Remove `ffprobe` from `PATH` | Startup logs an error; every upload is rejected with `unreadable_media` rather than accepted unvalidated |
| Drop 30% of client connections mid-upload | No orphaned temp files; no clip rows without bytes |

## Frontend load considerations (Layer 1)

* `MAX_PARALLEL = 2` in `useUploadQueue` is the client-side limit. Raising it makes each bar
  slower without finishing sooner on a typical uplink, and multiplies server-side probe load
  by the same factor.
* One websocket per open dashboard. 200 concurrent creators with two tabs each is 400 sockets,
  each with a 20 s heartbeat — trivial for the server, but `WS_CONNECTIONS` should be watched
  for leaks: a count that only ever rises means sockets are not being reaped.
* The per-project event history is capped (`PROGRESS_HISTORY_LENGTH`, default 50), so a long
  run cannot grow Redis memory without bound.
