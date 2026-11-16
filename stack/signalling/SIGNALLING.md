# Out-of-band signalling (task S3, #41)

The squish mode is a property of a 2 s segment, so it travels with the segment's metadata, not inside the picture.
A platform re-encode can damage the in-band marker; it cannot reach a playlist tag or a side channel that the player reads.

## Payload (one line of text, ASCII)

    DBPO1;seq=<n>;m=<0|1>;cfg=<FSQ1 code>

| field | meaning |
|---|---|
| `seq` | segment sequence number (HLS `EXT-X-MEDIA-SEQUENCE` of the segment, DASH `$Number$`) |
| `m` | `0` plain, `1` squished. Absent = plain |
| `cfg` | the existing `FSQ1:...` code (`research/foveation/squish_math.py`), required when `m=1`, omitted when `m=0` |

Rules:
- One payload per segment. The mode only changes at a segment boundary, which is also a closed-GOP boundary.
- A payload applies to the segment with that `seq` and to nothing else. No "until further notice" state.
- `cfg` must describe the ladder rung's own frame size; every rung carries its own payload.

## Carriers, in order of preference

1. **HLS playlist.** One `#EXT-X-DBPO:` line directly before the segment's `#EXTINF`, value = the payload without the
   `DBPO1;` prefix as an attribute list: `#EXT-X-DBPO:SEQ=120,M=1,CFG="FSQ1:rx=0.75,..."`. Players ignore unknown tags, so
   old clients are unaffected. Where the packager only passes standard tags, use `#EXT-X-DATERANGE` with
   `X-DBPO="..."` and `START-DATE` equal to the segment's `PROGRAM-DATE-TIME`.
2. **DASH.** An `EventStream` with `schemeIdUri="urn:dbpo:squish:1"`, one `Event` per segment, `presentationTime` = the
   segment start, text content = the payload. In-band alternative: an `emsg` box with the same scheme and payload.
3. **Side channel.** WebSocket (or server-sent events) at a URL the player gets from the page; one message per segment,
   the payload as text, sent before the segment is available. For platforms whose packager cannot be changed. The player
   keeps the last 8 messages keyed by `seq` and matches them to segments by sequence number, or by program date-time when
   the stream has no sequence numbers.
4. **In-band marker** (the 160x16 strip of the demo). Last fallback only; survives only if the transcode is gentle.

## Player behaviour

- Each segment: look up its payload through the carriers in the order above; the first one found wins.
- No payload for a segment: show the picture as plain and count it in a `missing_signal` counter the page can read.
  Never guess the mode from a neighbour segment.
- Payload present but `cfg` fails to parse, or the frame size does not match `cfg`: plain, and count it in `bad_signal`.
- A late message (arrives after the segment started playing): apply from the next segment, count `late_signal`.

## Test vectors

`vectors.json`, checked by `dbpo_signal.py` (`python3 test_signal.py`): encode, parse, HLS tag and DATERANGE round trip,
and the player's decision for present, missing, malformed and mismatched signals.
