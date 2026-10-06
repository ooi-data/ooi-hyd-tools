# Candidate: the same repair upstream, on packets

ooi-hyd-tools repairs the OOI broadband mseed after the fact: the **sample count**, not the
timestamps, decides whether recording is missing, and timestamps only locate a real break
(see [How the repair works](../README.md#how-the-repair-works)). These are notes on what it
would take to apply the same repair at ingest instead, where the mseed is written.

The mseed is written by the Antelope ORB driver in
[`oceanobservatories/mi-instrument`](https://github.com/oceanobservatories/mi-instrument),
`mi/instrument/antelope/orb/ooicore/` - `packet_log.py` and `driver.py`. What it does today:

- **Bins on the 5-min grid.** `_get_bin` computes `int(packet_time / 300) * 300`, and the
  filename is that bin start - which is why our repair can treat the filename as the anchor.
- **One obspy `Trace` per packet**, each carrying that packet's own timestamp. Packets are
  2560 samples (40 ms), hence the ~1200 traces we stitch back together in a single file.
- **Zero tolerance at the bin edge.** `add_packet` raises `GapException` the moment a
  packet's timestamp falls outside `[mintime, maxtime)`; the driver then closes the current
  file wherever it stands and opens a new one.
- **Nothing counts samples.** No comparison of what arrived against what a 5-min bin holds.

Nothing in our algorithm is specific to mseed. It is one rule - **a sample count is evidence,
a timestamp is a claim** - plus one measurement - **an overlap is proof of a false claim, so
it bounds how far the claims can be trusted**. Both survive the move to packets intact.

| in ooi-hyd-tools | at the packet level |
|---|---|
| 5-min mseed file | a window closed on the grid, held briefly for late packets |
| traces within the file | packets accumulated into that window |
| expected = 300 x sr | unchanged - the window defines it |
| largest overlap in the file | rolling estimate over recent packets, continuously updated |
| CI filename as the anchor | a sample counter, re-anchored only at a real break |
| CASE A / B / C at file close | identical, at window close |

The one structural upgrade is the anchor. The ADC does not silently drop or duplicate a
sample, so within a continuous stretch the true time of sample N is `anchor + N/sr`. Upstream
that means per-packet timestamps need not be trusted at all: carry a counter and re-anchor
only when a gap clears both thresholds. We cannot do this downstream, because we see one file
at a time and must re-derive the anchor from its name.

What is genuinely harder upstream is that there is no lookahead. A window must close before
you can know whether a late packet is still coming, so the rule needs a lateness bound and an
out-of-order buffer - neither of which a reader of a finished archive has to care about.
