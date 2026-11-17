# `video_sticker.py`

[↑ sticker_bot](README.md) · [code](video_sticker.py)

Convert arbitrary GIFs/videos (and already-valid Telegram video stickers,
re-encoded for safety) into Telegram-compliant video stickers.

Telegram's rule for video stickers: WEBM container, VP9 codec, no audio
track, one side exactly 512px (the other <=512px), <=3 seconds, <=30 FPS,
<=256 KB.

Hitting 256 KB is the hard part, and no single quality setting does it for
every clip. This used to walk a fixed ladder of eight increasingly
aggressive CRF values, re-encoding from scratch each time -- up to eight
full VP9 encodes of the same clip, which on a small shared vCPU is the
single most expensive thing this bot family does, and it happened most often
for exactly the busy clips that were slowest to encode.

Instead this now \*measures\*: the first attempt reports how far off it was,
and the next CRF is derived from that ratio rather than from a fixed step.
Two attempts cover almost everything, four is the ceiling, and the encoder
itself is capped to a couple of threads so one user's sticker cannot take
the whole container's CPU away from everyone else's messages.

A clip that cannot fit is no longer cut short or squeezed silently (since
2.1.6): one over MAX_DURATION_S is offered speeds that fit it (speed_choices),
and one that stays over MAX_BYTES raises TooBig, so the person can pick a lower
resolution or frame rate (bot.py: \_offer_video_fit).

Requires ffmpeg (with libvpx-vp9) on PATH.

_At `_FIRST_CRF = 34`:_

VP9's CRF scale is 0 (best) to 63 (worst). 34 lands inside the limit for
most ordinary clips; the loop below moves from there based on what the
encode actually produced.

_At `_TIMEOUT_S = int(os.environ.get("FFMPEG_TIMEOUT_SECONDS", "120"))`:_

Wall-clock ceiling per ffmpeg run. Without one, a pathological input can
leave a process pinning a core until the container is restarted -- and the
user, meanwhile, sees nothing at all.

_At `_THREADS = os.environ.get("FFMPEG_THREADS", "2")`:_

libvpx-vp9 will happily use every core it can see. On a shared host that is
the host's core count, not this container's share, so it buys nothing and
costs everyone else their latency. Two threads plus row-based multi-
threading is the sweet spot for a 512px clip.

### `_scale_to()`

Picks 512 for whichever side is larger and scales the other proportionally
(-2 keeps it even, which libvpx-vp9 requires) -- computed by ffmpeg itself
from the input's actual width/height, so no separate probing step is needed.

### `_filters()`

The -vf chain: faster playback first (setpts), then the detail of a
LOW_DETAIL_SIDE picture if asked, then the 512 px a sticker must be.

### `ConversionError`

Raised when ffmpeg is missing or the clip can't be made to fit.

### `TooBig`

Every attempt came out over MAX_BYTES: the person picks how to make it smaller.

_At `MAX_SPEED = float(os.environ.get("STICKER_MAX_SPEED", "4"))`:_

A clip longer than MAX_DURATION_S is not cut any more: the person is offered
speeds that fit it (or trims it themselves). The speed that fits exactly is
rounded up to a tenth and never above MAX_SPEED -- a 57-second clip is not
offered at 19x.

_At `LOW_DETAIL_SIDE = 256`:_

One side of a video sticker must be exactly 512 px, so "lower resolution"
encodes the detail of a LOW_DETAIL_SIDE picture scaled back up to 512:
softer, and far fewer bytes.

### `clip_duration()`

How long the clip plays, in seconds, or None when ffprobe cannot tell.

### `fit_speed()`

The least speed-up, in tenths, that brings `duration` to MAX_DURATION_S or less.

### `speed_choices()`

The speeds to offer for a clip this long: the one that fits exactly
first, then the presets above it -- every one of them brings the clip to
3 s or less, and none is over MAX_SPEED. Empty when nothing fits.

### `_next_crf()`

Given an attempt that came out `size` bytes at `crf`, guess the CRF
that lands under the limit -- and drop the frame rate once CRF alone has
run out of room.

VP9's bitrate falls off roughly geometrically in CRF: about 6 points of
CRF halves it. So overshooting by 2x asks for +6, by 4x for +12, and a
near miss only nudges. That is the whole trick -- the old ladder spent
its attempts stepping past clips it had already measured.

- At `return _MAX_CRF, 15 if fps <= 24 else 24`:
  Out of quality headroom: keep the worst CRF and thin the frames out
  instead. 24 then 15 fps, both well inside Telegram's "up to 30".

### `_encode()`

- At `"-threads", _THREADS, "-filter_threads", _THREADS, "-filter_complex_threads", _THREADS,`:
  Before -i, where it caps the decoder and the filters; the -threads
  below the input caps the encoder alone. ffmpeg sizes each pool from
  the host's core count -- 48 on Railway, whatever the container is
  allowed -- and every thread holds frames: a 4K clip through this
  command peaked at 528 MB with the cap on the encoder only, 1.3 GB as
  a 48-core host would size it, and 172 MB with this. (ConvertBot found
  the same on 2026-09-29; see convert_utils.FFMPEG_CAP_IN.)
- `"-an",` — video stickers must have no audio track

### `to_video_sticker_webm()`

Takes raw bytes of a GIF, video, or video sticker and returns bytes
of a compliant WEBM/VP9 video sticker. Raises ConversionError on failure,
TooBig when every attempt stayed over MAX_BYTES.

`speed` plays it faster (anything still over MAX_DURATION_S is cut);
`low_detail` and `low_fps` are the person's choices for a clip that would
not fit MAX_BYTES.

Runs synchronously (shells out to ffmpeg) -- call via asyncio.to_thread
from async code so it doesn't block the event loop. `lang` only affects
the text of any ConversionError raised (this function itself has no
other user-facing output).

- At `raise ConversionError(`:
  A failed encode says nothing about size, so there is no
  measurement to steer by -- stop rather than burn the
  remaining attempts on the same error.
- `break` — nowhere left to go; another identical encode is waste
