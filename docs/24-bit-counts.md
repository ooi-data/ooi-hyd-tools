# 24 bits in a 32-bit word

RCA broadband hydrophones - technical note

Two libraries both say `int32` and mean opposite things. That mismatch silently truncated
every sample we wrote, and it is the reason our calibration files had to carry a +128.9 dB
offset. This note follows one sample end to end, shows what the bug did to it, and explains
what the one-line fix untangles.

The fix itself is `INT24_SHIFT` in
[`ooi_hyd_tools/mseed_to_audio.py`](../ooi_hyd_tools/mseed_to_audio.py).

## The life of one sample

`ocean -> hydrophone -> mseed -> memory -> flac -> pbp`

Follow a single median-amplitude sample from a real 2026 HYDBBA102 file all the way through.
Watch stages 2 and 10 - the whole point of the fix is that they now match.

| # | stage | domain | value |
|---|---|---|---|
| 1 | pressure in the water | acoustic | 0.0721 Pa = 97.16 dB re 1 µPa |
| 2 | ceramic element + preamp | analog | **202.06 µV** |
| 3 | 24-bit ADC | counts | **565** |
| 4 | CI packetises to mseed | counts | 565 |
| 5 | obspy reads to numpy int32 | counts | 565, right-justified |
| 6 | **we shift left by 8** | counts | 144,640 |
| 7 | `sf_writef_int` shifts back down | counts | 565 |
| 8 | libFLAC encodes | counts | 565, lossless |
| 9 | mbari-pbp reads as float | normalised | 0.000067353 |
| 10 | × `VOLTAGE_MULTIPLIER` = 3 | analog | **202.06 µV** |
| 11 | apply the sensitivity | acoustic | 97.16 dB re 1 µPa |
| 12 | Welch PSD -> millidecade bands | product | one spectrogram column |

### 1 · A pressure fluctuation in the water
`acoustic · pascals` - **0.0721 Pa** = 72,107 µPa = 97.16 dB re 1 µPa

Ambient ocean noise arriving at the instrument, 2,900 m down on the Slope Base seafloor. This
is the physical quantity everything downstream is trying to preserve.

### 2 · The ceramic element and preamp
`analog · volts` - **202.06 µV** = -73.89 dB re 1 V

The transducer converts pressure to voltage at roughly -171 dB re 1 V/µPa (frequency-dependent,
and the sheet figure already includes the 36 dB preamp gain). **Remember this number.**

### 3 · The 24-bit ADC
`digital · counts` - **565 counts**, LSB = 357.6 nV, full scale ±3 V, 64 kHz

Voltage becomes an integer: 202.06 µV ÷ 357.6 nV = 565. From here to stage 9 the sample is
*only* an integer - it carries no units and no scale of its own, which is exactly why a
convention mismatch can corrupt it silently.

### 4 · CI packetises to mseed
`digital · miniseed` - 565, Steim-compressed, 5-minute files

Sample values are preserved exactly. What is *not* reliable is the timestamp attached to each
burst - that is the separate jitter problem the gap repair handles.

### 5 · obspy reads it into memory
`digital · numpy int32` - 565, right-justified: bits 0-23, top byte is sign

Gap repair happens here - traces are concatenated or split at real breaks - but sample values
are never touched, resampled or interpolated.

### 6 · We shift left by 8
`digital · the fix` - `565 << 8 = 144,640`

Moves the 24 real bits from the bottom of the word to the top, where libsndfile expects to
find them.

### 7 · `sf_writef_int` shifts back down
`digital · libsndfile` - `144,640 >> 8 = 565`, stored as a 24-bit sample

libsndfile treats int32 as full-range ±2^31 and rescales to the file's 24 bits. Our shift and
its shift cancel exactly. **Without stage 6 this step stored 2 instead of 565.**

### 8 · libFLAC encodes it
`digital · flac on disk` - 565, lossless, bit-identical on decode

FLAC compresses about 2.6:1 on this data - 57.6 MB of raw 24-bit samples becomes a ~21.9 MB
file - and gives every value back unchanged. Verified sample-for-sample against the raw
archive on 27 instrument-days.

### 9 · mbari-pbp reads it as float
`digital · normalised float` - 0.000067353 = 565 / 2^23, where 1.0 is full scale

Stock pbp asks for float, so libsndfile normalises by 2^23 rather than shifting. Scale-free
again, but now anchored to full scale rather than to a bit position.

### 10 · × `VOLTAGE_MULTIPLIER` = 3
`analog · volts` - **202.06 µV**, identical to stage 2

Because 1.0 is full scale is 2^23 counts is 3 V, one multiplication returns the voltage at the
ADC input. The loop closes.

### 11 · Apply the sensitivity
`acoustic · pascals` - 97.16 dB re 1 µPa, identical to stage 1

The sheet value in dB re 1 V/µPa applies directly to volts - no offset, no correction cals. We
are back to the pressure in the water.

### 12 · Welch PSD -> hybrid millidecade bands -> netCDF
`acoustic · product`

Millions of calibrated samples become one spectrogram column, then a day of columns becomes
the delivered product.

### Why this is the whole argument

Stage 2 says 202.06 µV. Stage 10 says 202.06 µV. Everything between them is bookkeeping, and
the bookkeeping is only correct because stage 6 cancels stage 7. Get that wrong and the number
arriving at stage 11 is not the sound that was in the water.

## The same sample before the fix

Without stage 6, libsndfile stored **2** instead of 565 - and what happened next depended
entirely on how the file was read:

| Read path | Recovered | Error on this sample |
|---|---|---|
| forked pbp, `dtype="int32"` | 512 counts | -0.86 dB |
| stock pbp, float | 0.7153 µV | **-49.0 dB** |

The int32 read multiplied by 256 on the way out and so hid the damage, leaving only a
quantisation error. The float read did not - which is why the fork was load-bearing, and why
removing it required fixing the write first.

## The disagreement

The hydrophone ADC produces **24-bit signed integers**, full scale ±2^23 = ±8,388,608 counts,
corresponding to ±3 V. Both obspy and libsndfile will hand you those counts in a 32-bit
container, but they disagree about *which end* of the container they belong in.

- **obspy is right-justified.** The 24 bits sit in the low end, bits 0-23. The top byte is only
  sign extension. A count of 1000 is the integer 1000.
- **libsndfile is left-justified.** Its int32 API treats full scale as ±2^31, so when writing
  `PCM_24` it keeps the *top* 24 bits - bits 8-31 - and discards the low byte.

Two conventions, off by exactly eight bits: 32 minus 24.

## Where libsndfile comes in

Our code never mentions libsndfile. It calls `soundfile`, which is a thin CFFI wrapper that
does no signal processing of its own - it builds a C function name from the dtype of your
array and calls straight through:

```
ooi_hyd_tools          import soundfile as sf
  soundfile 0.12.1     python wrapper - chooses the C entry point
    libsndfile 1.2.0   does the justification, owns the subtype rules
      libFLAC          encodes the 24-bit samples
```

So the numpy dtype selects which conversion runs, and each C function carries its own
definition of full scale:

| You ask for | C function | libsndfile's rule for PCM_24 |
|---|---|---|
| `dtype="int32"` | `sf_readf_int` / `sf_writef_int` | full scale ±2^31 -> **shifts 8 bits** |
| `dtype="float64"` | `sf_readf_double` | full scale ±1.0 -> **normalises by 2^23** |

That is the whole mechanism: same file, same library, different answers depending on one
argument. The `<< 8` in our writer exists purely to cancel a conversion happening inside a C
library we never call directly.

Two related consequences are also libsndfile's doing, not ours: FLAC accepts only `PCM_S8`,
`PCM_16` and `PCM_24`, which is why FLAC output is always 24-bit and why `FLOAT` raises an
error; and obspy is entirely uninvolved, since it reads miniSEED into a numpy array and stops.
The justification question only arises at the soundfile boundary.

## What that did to every sample

Count 1000, before the fix. We handed libsndfile right-justified counts; it kept bits 31-8 and
threw the rest away.

```
                   31-24     23-16     15-8      7-0
obspy int32      [ 00000000  00000000  00000011  11101000 ]  = 1000 counts
                   \________ kept by PCM_24 _______/\_______/
                                                    discarded
stored in FLAC   [ 00000000  00000000  00000011 ]            = 3
read back        [ 00000000  00000000  00000011  00000000 ]  = 768   (lost 232)
```

The value survived, but only in coarse steps - everything below the eighth bit is gone, so the
number that came back is snapped to a multiple of 256.

### With the shift

Shifting left by 8 first puts the count where libsndfile expects it, so all 24 bits are
preserved and `>> 8` recovers the original exactly.

```
                   31-24     23-16     15-8      7-0
count << 8       [ 00000000  00000011  11101000  00000000 ]  = 256,000
                   \________ kept by PCM_24 _______/\_______/
                                                    padding zeros
stored in FLAC   [ 00000000  00000011  11101000 ]            = 1000
read, then >> 8  [ 00000000  00000000  00000011  11101000 ]  = 1000   exact
```

The shift is exactly lossless, and that is not luck - 24 + 8 = 32. Both rails fit:
`8,388,607 << 8 = 2,147,483,392` sits just inside int32's maximum, and
`-8,388,608 << 8 = -2,147,483,648` lands exactly on its minimum.

### Where a real sample lands

Real figures from a 2026 HYDBBA102 file, showing what each would have lost before the fix.
Note how little of the 32-bit word a typical ocean-noise sample occupies.

| count | | stored pre-fix | read back | lost | error |
|---|---|---|---|---|---|
| 565 | median sample | 2 | 512 | 53 | 9.4% |
| 1,194 | RMS | 4 | 1,024 | 170 | 14.2% |
| 2,635 | p99 | 10 | 2,560 | 75 | 2.8% |
| 32,371 | loudest in file | 126 | 32,256 | 115 | 0.4% |
| 8,388,607 | full scale | 32,767 | 8,388,352 | 255 | 0.003% |

The error is worst for the quietest samples, which is the opposite of what a fixed 48 dB
noise floor would suggest, and it is the reason the next section matters.

## Why it mattered more than it looks

If ocean noise filled the 24-bit range, dropping eight bits would leave a noise floor 48 dB
down and nobody would care. It does not. Measured across one 5-minute file, 19.2 million
samples:

| Measure | Counts | Relative |
|---|---|---|
| Full scale | 8,388,608 | 0 dBFS |
| Loudest sample in the file | 32,371 | -48.3 dBFS |
| RMS | 1,194 | -76.9 dBFS |
| Median \|sample\| | 565 | -83.4 dBFS |
| Truncation error RMS | 73.9 | **-24.2 dB re signal** |

Ambient noise sits about **77 dB below full scale**, so the signal occupies roughly **11 of
the 24 bits**. Everything above bit 15 is empty almost all the time. The signal lives in the
low bits - precisely the ones being discarded.

**The real cost:** not 48 dB down and harmless, but a **24.2 dB signal-to-truncation ratio**
on the broadband waveform, with **12.5% of all samples driven to exactly zero**.

It stayed hidden because truncation error is roughly white - spread flat across 0-32 kHz -
while ocean noise is steeply concentrated at low frequency. Per hertz, the low-frequency
signal still towered over the error. At high frequencies, where the true signal is weak, the
flat error floor dominated.

| Band | Measured change after the fix |
|---|---|
| below 10 kHz | -0.03 to -0.07 dB |
| 10-20 kHz | -0.05 to -2.5 dB |
| 20-27 kHz | -0.15 to -3.5 dB |
| 27-30 kHz (anti-alias skirt) | **-3.6 to -20.6 dB** |

Median difference across eight instrument-days, 1,440 aligned time steps each. A severe
broadband defect that was nearly invisible in the band carrying most of the energy.

## And this is where the offset came from

Here is the part that explains the calibration files. The two ways of reading a FLAC behave
completely differently on a truncated file:

| Reading a pre-fix FLAC | count 1000 comes back as | Level error |
|---|---|---|
| `dtype="int32"` - left-justified, ×256 on the way out | 768 | ≈ 0 dB |
| `dtype="float"` - normalised to the 24-bit word | 3 | **-48.2 dB** |

Reading as int32 **put the factor of 256 back**. It restored the magnitude while leaving the
resolution destroyed. Reading as float did not, and would have produced products 48 dB low.

So the forked pbp with its explicit `dtype="int32"` was, without anyone intending it,
**compensating for the truncation**. That fork is why levels ever looked right.

But it has a consequence. Once pbp is handed raw integer *counts* with
`voltage_multiplier = 1`, the calibration has to do the counts-to-volts conversion itself.
That conversion is the offset:

```
2^23 counts = 3 V  ->  2**23 / 3 = 2,796,202.67 counts per volt
20 * log10(2,796,202.67) = 128.931373 dB
```

That is the number baked into `rca_correction_cals`. It was never a physical correction - it
was a unit conversion, made necessary by staying in counts, which was made necessary by the
int32 read, which was masking the justification bug.

## What the fix untangles

Writing `tr.data << 8` stores true 24-bit counts. The float path becomes exact, so stock
mbari-pbp works, and the unit conversion moves into a plain multiplier:

```
before   int32 counts       ->  cals carrying +128.9 dB   ->  spectrogram
after    float x 3 = volts  ->  plain sheet sensitivities ->  spectrogram
```

Identical arithmetic, moved to where it belongs. A normalised float of 1.0 is full scale is
2^23 counts is 3 V, so multiplying by 3 yields volts directly and the sensitivity sheets in
dB re 1 V/µPa apply unmodified. No correction cals, no fork.

Confirmed on production data: below 1 kHz the old and new paths agree to **within 0.05 dB** on
every instrument-day tested - the two routes really are the same calculation.

---

`INT24_SHIFT` in [`ooi_hyd_tools/mseed_to_audio.py`](../ooi_hyd_tools/mseed_to_audio.py) ·
`VOLTAGE_MULTIPLIER` in [`ooi_hyd_tools/audio_to_spec.py`](../ooi_hyd_tools/audio_to_spec.py)

Sample statistics from `RS01SLBS-LJ01A-09-HYDBBA102`, 2026-08-19T00:00:00Z, 19,200,000 samples.
