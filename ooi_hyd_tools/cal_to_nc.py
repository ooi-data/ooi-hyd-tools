import click
import yaml
import xarray as xr
import numpy as np
import matplotlib.pyplot as plt

from pathlib import Path
from datetime import datetime, date
from typing import List, Optional, Dict
from pydantic import BaseModel, constr, model_validator

"""
Build the netCDF cal files the pipeline uses from a reviewable YAML spec:

    metadata/cals/{refdes}_{deployment}.nc   manufacturer values in dB re 1 V/uPa

Every file carries a `sensitivity` variable (the 0/90 average for directional cals) - the
only variable pbp reads. Directional files keep sensitivity_0/sensitivity_90 as the record
of the sheet. No volts-to-counts offset is applied here; audio_to_spec.py handles that with
VOLTAGE_MULTIPLIER.

One spec per instrument holds every deployment, so drift between deployments is visible in one
diff. The PDF -> YAML transcription stays a human step: a hallucinated sensitivity silently biases
every spectrogram downstream and nothing at run time would catch it. Replaces
notebooks/03_PARSE_CAL_TO_NC.ipynb.

    cal-to-nc template > metadata/cal_specs/RS03AXPS-PC03A-08-HYDBBA303.yaml
    cal-to-nc build metadata/cal_specs/*.yaml --plot
    cal-to-nc check metadata/cal_specs/*.yaml     # CI: committed .nc still match their specs
    cal-to-nc from-nc RS03AXPS-PC03A-08-HYDBBA303 # backfill a spec from existing .nc files
"""

SENS_RANGE = (-220.0, -120.0)  # plausible dB re 1 V/uPa, catches transcription slips
DRIFT_DB = 6.0  # mean sensitivity jump between deployments worth a second look
# Below the tabulated band the icListen HF rolls off as a first-order high-pass. 10 Hz is the
# spec-sheet LF cutoff, interim until ONC HydroCal curves exist for our units: HydroCal puts
# two icListen HF at roughly 7 and 13 Hz, and anchoring at 26 Hz keeps either within ~1.5 dB
LF_CORNER_HZ = 10.0
LF_POINTS_PER_DECADE = 20  # dense enough that pbp's linear interp follows the model
SPEC_DIR = Path("./metadata/cal_specs")
META_DIR = Path("./metadata")

TEMPLATE = """\
# Hydrophone calibrations, transcribed from the manufacturer cal PDFs.
# One file per instrument; add a block under `deployments` for each new deployment.
# Provide EITHER sens (single curve) OR both sens0/sens90 (directional).
refdes: RS03AXPS-PC03A-08-HYDBBA303   # 27 chars
deployments:
  "11":
    asset_id: ATAPL-58324-00009       # 17 chars
    model: SB35-ETH
    sn: 1362
    cal_date: 2023-03-08
    source_pdf: null                  # filename of the cal PDF, for provenance
    placeholder: null                 # reason string if this is a stand-in cal
    freq_units: kHz                   # kHz (as printed on most cal sheets) or Hz
    frequencies: [10.0, 20.1]         # the table as printed; build adds the 0 Hz anchor
    sens0: [-171.2, -172.9]
    sens90: [-171.2, -172.6]
    lf_sens: -170.9                   # "Sensitivity @ 26 Hz", printed beside the table
"""


class HydCal(BaseModel):
    """one deployment's calibration"""

    frequencies: List[float]
    freq_units: str = "kHz"
    sens: Optional[List[float]] = None
    sens0: Optional[List[float]] = None
    sens90: Optional[List[float]] = None
    # metadata
    asset_id: Optional[constr(min_length=17, max_length=17)] = None
    model: Optional[str] = None
    sn: Optional[int] = None
    cal_date: Optional[datetime] = None
    source_pdf: Optional[str] = None
    placeholder: Optional[str] = None
    # "Sensitivity @ 26 Hz",  Without it the curve below the first tabulated point is a flat copy of that point
    lf_sens: Optional[float] = None
    lf_freq_hz: float = 26.0
    lf_corner_hz: float = LF_CORNER_HZ

    @model_validator(mode="after")
    def check_cal(self):
        if self.freq_units.lower() == "khz":
            self.frequencies = [f * 1000 for f in self.frequencies]
            self.freq_units = "Hz"

        if self.sens is None and not (self.sens0 and self.sens90):
            raise ValueError("provide either sens, or both sens0 and sens90")

        for name in ("sens", "sens0", "sens90"):
            v = getattr(self, name)
            if v is None:
                continue
            if len(v) != len(self.frequencies):
                raise ValueError(
                    f"{name} has {len(v)} values but frequencies has {len(self.frequencies)}"
                )
            bad = [x for x in v if not SENS_RANGE[0] <= x <= SENS_RANGE[1]]
            if bad:
                raise ValueError(f"{name} values outside {SENS_RANGE} dB: {bad}")

        if sorted(self.frequencies) != self.frequencies:
            raise ValueError("frequencies must be ascending - check for a transcription slip")
        if self.frequencies[0] <= 0:
            raise ValueError("frequencies start at the first tabulated point; build adds 0 Hz")

        if self.lf_sens is not None:
            if not SENS_RANGE[0] <= self.lf_sens <= SENS_RANGE[1]:
                raise ValueError(f"lf_sens {self.lf_sens} outside {SENS_RANGE} dB")
            if not 0 < self.lf_freq_hz < self.frequencies[0]:
                raise ValueError("lf_freq_hz must be below the first tabulated point")
            if not 0 < self.lf_corner_hz < self.lf_freq_hz * 10:
                raise ValueError(f"lf_corner_hz {self.lf_corner_hz} is implausible")
        return self

    def highpass(self, f):
        """first-order high-pass roll-off in dB at lf_corner_hz"""
        x = np.asarray(f, dtype=float) / self.lf_corner_hz
        return 20 * np.log10(x / np.sqrt(1 + x**2))

    def curves(self):
        """the frequency grid and curves pbp sees. The spec holds only the printed table; pbp
        interpolates linearly between points and returns NaN outside them, so this adds the
        band below the table, down to a 0 Hz anchor.

        Without lf_sens everything below the first tabulated point is a flat copy of it. With
        lf_sens that band is modelled and sampled log-spaced: a first-order high-pass at
        lf_corner_hz on a plateau placed so the curve passes through lf_sens at lf_freq_hz,
        blended in log frequency up to the first tabulated point. lf_sens is one
        omnidirectional value, so directional cals share it and each blends to its own table"""
        freqs = list(self.frequencies)
        curves = {k: list(getattr(self, k)) for k in ("sens", "sens0", "sens90") if getattr(self, k)}
        if self.lf_sens is None:
            return [0.0, *freqs], {k: [v[0], *v] for k, v in curves.items()}

        f1 = freqs[0]
        n = int(np.log10(f1) * LF_POINTS_PER_DECADE)
        grid = np.unique(np.append(np.logspace(0, np.log10(f1), n, endpoint=False), self.lf_freq_hz))
        plateau = self.lf_sens - self.highpass(self.lf_freq_hz)
        t = np.clip(np.log(grid / self.lf_freq_hz) / np.log(f1 / self.lf_freq_hz), 0, 1)
        for k, v in curves.items():
            top = v[0] - self.highpass(f1)  # so the curve meets the tabulated point exactly
            model = plateau + t * (top - plateau) + self.highpass(grid)
            curves[k] = [float(model[0]), *map(float, model), *v]
        return [0.0, *map(float, grid), *freqs], curves

    def _attrs(self, spec_path):
        attrs = {
            "asset_id": self.asset_id,
            "model": self.model,
            "serial_number": self.sn,
            "calibration_date": self.cal_date.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if self.cal_date
            else None,
            "source_spec": str(spec_path),
            "source_pdf": self.source_pdf,
            "placeholder": self.placeholder,
            "lf_sensitivity": self.lf_sens,
            "lf_frequency_hz": self.lf_freq_hz if self.lf_sens is not None else None,
            "lf_corner_hz": self.lf_corner_hz if self.lf_sens is not None else None,
        }
        return {k: v for k, v in attrs.items() if v is not None}

    def to_dataset(self, spec_path) -> xr.Dataset:
        """cal values as printed on the PDF, plus the `sensitivity` variable pbp reads
        (the sheet curve directly, or the 0/90 average for directional cals)"""
        freqs, c = self.curves()
        if self.sens is not None:
            vars = {"sensitivity": (["frequency"], c["sens"])}
        else:
            avg = [(a + b) / 2 for a, b in zip(c["sens0"], c["sens90"])]
            vars = {
                "sensitivity_0": (["frequency"], c["sens0"]),
                "sensitivity_90": (["frequency"], c["sens90"]),
                "sensitivity": (["frequency"], avg),
            }
        ds = xr.Dataset(
            data_vars=vars,
            coords={"frequency": freqs},
            attrs={**self._attrs(spec_path), "sensitivity_units": "dB re 1 V/uPa"},
        )
        if self.sens is None:
            ds.sensitivity.attrs["note"] = "average of sensitivity_0 and sensitivity_90"
        ds.frequency.attrs["units"] = "Hz"
        return ds

    def mean_sens(self):
        curve = self.sens if self.sens is not None else self.sens0
        return float(np.mean(curve))


class InstrumentCal(BaseModel):
    refdes: constr(min_length=27, max_length=27)
    deployments: Dict[str, HydCal]

    def drift(self):
        """mean-sensitivity jumps between consecutive deployments, a transcription-error smell"""
        items = sorted(self.deployments.items(), key=lambda kv: int(kv[0]))
        return [
            (a, b, cal_b.mean_sens() - cal_a.mean_sens())
            for (a, cal_a), (b, cal_b) in zip(items, items[1:])
            if abs(cal_b.mean_sens() - cal_a.mean_sens()) > DRIFT_DB
        ]


def load_spec(path: Path) -> InstrumentCal:
    return InstrumentCal(**yaml.safe_load(path.read_text()))


def dataset_for(spec: InstrumentCal, deployment: str, spec_path: Path):
    return spec.deployments[deployment].to_dataset(Path(spec_path).as_posix())


def compare(a: xr.Dataset, b: xr.Dataset):
    """differences between a committed dataset and a freshly built one"""
    diffs = []
    if sorted(a.data_vars) != sorted(b.data_vars):
        diffs.append(f"vars {sorted(a.data_vars)} != {sorted(b.data_vars)}")
    if a.frequency.size != b.frequency.size:
        diffs.append(f"frequency grid {a.frequency.size} points != {b.frequency.size}")
    elif not np.array_equal(a.frequency.values, b.frequency.values):
        diffs.append("frequency values differ")
    for v in set(a.data_vars) & set(b.data_vars):
        if a[v].shape != b[v].shape:
            diffs.append(f"{v} shape {a[v].shape} != {b[v].shape}")
        elif not np.allclose(a[v].values, b[v].values, atol=1e-9):
            worst = float(np.abs(a[v].values - b[v].values).max())
            diffs.append(f"{v} differs by up to {worst:.4f} dB")
    for k in set(a.attrs) | set(b.attrs):
        if str(a.attrs.get(k)) != str(b.attrs.get(k)):
            diffs.append(f"attr {k}: {a.attrs.get(k)!r} != {b.attrs.get(k)!r}")
    return diffs


def plot_cal(ds, fpath, title):
    khz = ds.assign_coords(frequency=ds.frequency / 1000)
    fig, ax = plt.subplots(figsize=(7, 4))
    for var in ds.data_vars:
        style = {"color": "k"} if var == "sensitivity" else {"alpha": 0.6}
        khz[var].plot(ax=ax, label=var, linewidth=1, **style)
    ax.set_title("sensitivity (dB re 1 V/$\\mu$Pa); pbp reads `sensitivity`", fontsize=9)
    ax.legend(fontsize=8)
    ax.set_xlabel("frequency (kHz)")
    ax.set_ylabel("sensitivity (dB)")
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(fpath, dpi=150)
    plt.close(fig)


@click.group()
def cli():
    """Build and verify hydrophone calibration netCDF files from YAML specs."""


@cli.command()
def template():
    """Print a starter spec."""
    click.echo(TEMPLATE)


@cli.command()
@click.argument("specs", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--outdir",
    type=click.Path(path_type=Path),
    default=META_DIR,
    show_default=True,
    help="Parent of cals/.",
)
@click.option("--plot", is_flag=True, help="Save a QA plot per deployment next to the spec.")
@click.option("--dry-run", is_flag=True, help="Validate and report without writing netCDF.")
def build(specs, outdir, plot, dry_run):
    """Write the netCDF file for every deployment in SPECS."""
    for spec_path in specs:
        spec = load_spec(spec_path)
        click.echo(f"{spec.refdes} ({len(spec.deployments)} deployments) from {spec_path}")

        for dep, cal in sorted(spec.deployments.items(), key=lambda kv: int(kv[0])):
            ds = dataset_for(spec, dep, spec_path)
            fname = f"{spec.refdes}_{dep}.nc"
            flag = f"  PLACEHOLDER: {cal.placeholder}" if cal.placeholder else ""
            click.echo(
                f"  deployment {dep}: {len(cal.frequencies)} points, "
                f"{cal.frequencies[0]:.0f}-{cal.frequencies[-1]:.0f} Hz, "
                f"{float(ds.sensitivity.min()):.1f} to "
                f"{float(ds.sensitivity.max()):.1f} dB re 1 V/uPa{flag}"
            )
            if plot:
                png = spec_path.with_name(f"{spec.refdes}_{dep}.png")
                plot_cal(ds, png, f"{spec.refdes} deployment {dep}")
            if not dry_run:
                (outdir / "cals").mkdir(parents=True, exist_ok=True)
                ds.to_netcdf(outdir / "cals" / fname, mode="w")

        no_lf = [d for d, c in spec.deployments.items() if c.lf_sens is None]
        if no_lf:
            click.secho(
                f"  no lf_sens on {len(no_lf)} of {len(spec.deployments)} deployments - flat "
                "below their first tabulated point",
                fg="yellow",
            )
        for a, b, delta in spec.drift():
            click.secho(
                f"  drift: mean sensitivity changes {delta:+.1f} dB from deployment {a} to {b}",
                fg="yellow",
            )
        if dry_run:
            click.echo("  dry run, nothing written")


@cli.command()
@click.argument("specs", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--outdir", type=click.Path(path_type=Path), default=META_DIR, show_default=True)
def check(specs, outdir):
    """Verify committed netCDF files still match their specs. Exits 1 on any drift."""
    stale = 0
    for spec_path in specs:
        spec = load_spec(spec_path)
        for dep in sorted(spec.deployments, key=int):
            built = dataset_for(spec, dep, spec_path)
            path = outdir / "cals" / f"{spec.refdes}_{dep}.nc"
            if not path.exists():
                click.secho(f"MISSING {path}", fg="red")
                stale += 1
                continue
            diffs = compare(xr.open_dataset(path), built)
            if diffs:
                stale += 1
                click.secho(f"STALE {path}", fg="red")
                for d in diffs:
                    click.echo(f"    {d}")
    if stale:
        raise click.ClickException(f"{stale} file(s) do not match their spec - run `build`")
    click.secho("all committed cal files match their specs", fg="green")


@cli.command("from-nc")
@click.argument("refdes")
@click.option(
    "--indir",
    type=click.Path(path_type=Path),
    default=META_DIR / "cals",
    show_default=True,
    help="Directory of exact (uncorrected) cal netCDF files to read.",
)
@click.option("--outdir", type=click.Path(path_type=Path), default=SPEC_DIR, show_default=True)
def from_nc(refdes, indir, outdir):
    """Backfill a YAML spec for REFDES from existing netCDF cal files."""
    files = sorted(indir.glob(f"{refdes}_*.nc"), key=lambda p: int(p.stem.split("_")[-1]))
    if not files:
        raise click.ClickException(f"no cal files for {refdes} in {indir}")

    deployments = {}
    for f in files:
        ds = xr.open_dataset(f)
        dep = f.stem.split("_")[-1]
        if "lf_sensitivity" in ds.attrs:
            raise click.ClickException(f"{f.name} has an lf_sens point; edit its spec instead")
        cal = {
            "asset_id": ds.attrs.get("asset_id"),
            "model": ds.attrs.get("model"),
            "sn": int(ds.attrs["serial_number"]) if "serial_number" in ds.attrs else None,
            "cal_date": None,
            "source_pdf": ds.attrs.get("source_pdf"),
            "placeholder": ds.attrs.get("placeholder"),
            "freq_units": "Hz",
            "frequencies": [float(v) for v in ds.frequency.values[1:]],  # drop the 0 Hz anchor
        }
        if "calibration_date" in ds.attrs:
            cal["cal_date"] = datetime.strptime(
                ds.attrs["calibration_date"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).date()
        # `sensitivity` on directional files is the derived 0/90 average `build` recreates,
        # so it stays out of the spec
        if {"sensitivity_0", "sensitivity_90"} <= set(ds.data_vars):
            cal["sens0"] = [float(v) for v in ds.sensitivity_0.values[1:]]
            cal["sens90"] = [float(v) for v in ds.sensitivity_90.values[1:]]
        else:
            cal["sens"] = [float(v) for v in ds.sensitivity.values[1:]]
        deployments[dep] = {k: v for k, v in cal.items() if v is not None}

    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"{refdes}.yaml"
    out.write_text(
        yaml.safe_dump(
            {"refdes": refdes, "deployments": deployments},
            sort_keys=False,
            default_flow_style=None,
        )
    )
    click.echo(f"wrote {out} ({len(deployments)} deployments)")


def _yaml_date(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:timestamp", data.isoformat())


yaml.SafeDumper.add_representer(date, _yaml_date)


if __name__ == "__main__":
    cli()
