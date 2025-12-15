import pvlib

import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
import pandas as pd
import matplotlib.dates as md

from datetime import datetime, timedelta
from pathlib import Path
from matplotlib import gridspec
from typing import Optional

from ooipy.request.hydrophone_request import get_acoustic_data_LF

LOW_FREQ_DICT = {
    "RS01SLBS-MJ01A-05-HYDLFA101": ["HYSB1", (44.50829, -125.40466)],
    "RS03AXBS-MJ03A-05-HYDLFA301": ["AXBA1", (45.82051, -129.73671)],
    "RS01SUM1-LJ01B-05-HYDLFA104": ["HYS14", (44.56922, -125.14816)],
    "RS03CCAL-MJ03F-06-HYDLFA305": ["AXCC1", (45.95479, -130.00932)],
    "RS03ECAL-MJ03E-09-HYDLFA304": ["AXEC2", (45.93997, -129.73383)],
}


# TODO just call the packaged mbari function one changes are merged to mbari-pbp
# TODO might not be straightforward because of the datetime bug?
def plot_dataset_summary(
    ds: xr.Dataset,
    lat_lon_for_solpos: tuple[float, float] = (44.50829, -125.40466),
    title: str = None,
    ylim: tuple[int, int] = (0.1, 100),
    yscale: str = "log",
    cmlim: tuple[int, int] = (32, 108),
    dpi: int = 200,
    cmap: str = "rainbow",
    jpeg_filename: Optional[str] = None,
    show: bool = False,
):
    """
    Generate a summary plot from the given dataset.
    :param ds: Dataset to plot.
    :param lat_lon_for_solpos: Lat/Lon for solar position calculation.
    :param title: Title for the plot.
    :param ylim: Limits for the y-axis.
    :param cmlim: Limits passed to pcolormesh.
    :param dpi: DPI to use for the plot.
    :param jpeg_filename: If given, filename to save the plot to.
    :param show: Whether to show the plot.
    """
    plt.rcParams["text.usetex"] = False
    plt.rcParams["axes.edgecolor"] = "black"

    # Transpose psd array for plotting
    da = xr.DataArray.transpose(ds.psd)

    # get solar elevation
    # Estimate the solar position with a specific SPA defined with the argument 'method'
    latitude, longitude = lat_lon_for_solpos
    solpos = pvlib.solarposition.get_solarposition(
        ds.time, latitude=latitude, longitude=longitude
    )
    se = solpos.elevation  # isolate solar elevation
    # map elevation to gray scale
    seg = 0 * se  # 0 covers nighttime (black)
    # day (white)
    d = np.squeeze(np.where(se > 0))
    seg.iloc[d] = 1
    # dusk / dawn (gray range)
    d = np.squeeze(np.where(np.logical_and(se <= 0, se >= -12)))
    seg.iloc[d] = 1 - abs(
        se.iloc[d] / np.max(abs(se.iloc[d]), 0)
    )  # TODO np.max in mbari version?
    # TODO before the line above would error if only times at night...
    # Get the indices of the min and max
    seg1 = pd.Series.to_numpy(solpos.elevation)
    minidx = np.squeeze(np.where(seg1 == min(seg1)))
    maxidx = np.squeeze(np.where(seg1 == max(seg1)))

    seg3 = np.tile(seg, (50, 1))

    # plotting variables

    psdlabl = r"Spectrum level (dB re 1 $\mu$Pa$\mathregular{^{2}}$ Hz$\mathregular{^{-1}}$)"
    freqlabl = "Frequency (Hz)"

    # define percentiles
    pctlev = np.array([1, 10, 25, 50, 75, 90, 99])
    # initialize output array
    pctls = np.empty((pctlev.size, ds.frequency.size))
    # get percentiles
    np.nanpercentile(ds.psd, pctlev, axis=0, out=pctls)

    # create a figure
    fig = plt.figure()
    fig.set_figheight(6)
    fig.set_figwidth(12)
    spec = gridspec.GridSpec(
        ncols=2,
        nrows=2,
        width_ratios=[2.5, 1],
        wspace=0.02,
        height_ratios=[0.045, 0.95],
        hspace=0.09,
    )

    # Use more of the available plotting space
    plt.subplots_adjust(left=0.06, right=0.94, bottom=0.12, top=0.89)

    # Spectrogram
    ax0 = fig.add_subplot(spec[2])
    vmin, vmax = cmlim

    time_values = md.date2num(
        ds["time"].values
    )  # HACK is this matplotlib backend dependent? not
    # in the original mbari function
    sg = plt.pcolormesh(
        time_values, ds["frequency"], da, shading="nearest", cmap=cmap, vmin=vmin, vmax=vmax
    )
    plt.yscale(yscale)
    plt.ylim(list(ylim))
    plt.ylabel(freqlabl)
    xl = ax0.get_xlim()
    ax0.set_xticks([])
    # plt.colorbar(location='left', shrink = 0.25, fraction = 0.05)

    # Percentile
    pplabels = ["L99", "L90", "L75", "L50", "L25", "L10", "L1"]
    ax1 = fig.add_subplot(spec[3])
    ax1.yaxis.tick_right()
    ax1.yaxis.set_label_position("right")
    plt.plot(pctls.T, ds.frequency, linewidth=1)
    plt.yscale(yscale)
    plt.ylim(list(ylim))
    plt.xlabel(psdlabl)
    plt.ylabel(freqlabl)
    plt.legend(loc="lower left", labels=pplabels)

    # day night
    ax3 = fig.add_subplot(spec[0])
    ax3.pcolormesh(seg3, shading="flat", cmap="gray")
    ax3.annotate("Day", (maxidx, 25), weight="bold", ha="center", va="center")
    ax3.annotate("Night", (minidx, 25), weight="bold", color="white", ha="center", va="center")
    ax3.set_xticks([])
    ax3.set_yticks([])

    # colorbar for spectrogram
    r = np.concatenate(np.squeeze(ax0.get_position()))
    cb_ax = fig.add_axes([r[0] + 0.09, r[1] - 0.025, r[2] - 0.25, 0.015])
    q = fig.colorbar(sg, orientation="horizontal", cax=cb_ax)
    q.set_label(psdlabl)

    # time axes for the day/night panel
    # create a dummy time / zero range variable
    timax = fig.add_axes(ax3.get_position(), frameon=False)
    timax.plot(solpos.elevation * 0, "k")
    timax.tick_params(top=True, labeltop=True, bottom=False, labelbottom=False)
    timax.set_ylim(0, 100)
    timax.set_yticks([])
    timax.set_xlim(xl)
    timax.xaxis.set_major_formatter(md.ConciseDateFormatter(timax.xaxis.get_major_locator()))

    plt.gcf().text(0.5, 0.955, title, fontsize=14, horizontalalignment="center")
    plt.gcf().text(0.65, 0.91, "UTC")

    if jpeg_filename is not None:
        plt.savefig(jpeg_filename, dpi=dpi)
    if show:
        plt.show()
    plt.close(fig)


def run_low_freq_oneday(
    hyd_refdes,
    date,
):
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)

    instrument = hyd_refdes[-9:]
    start_date = date.replace("/", "")
    date = datetime.strptime(date, "%Y/%m/%d")
    endtime = date + timedelta(days=1)

    lf_data = get_acoustic_data_LF(
        starttime=date,
        endtime=endtime,
        node=LOW_FREQ_DICT[hyd_refdes][0],
        verbose=True,
        merge_traces=True,
    )

    spec = lf_data.compute_spectrogram(L=2048, avg_time=20, verbose=True)
    ds = spec.to_dataset(name="psd")

    plot_dataset_summary(
        ds,
        lat_lon_for_solpos=LOW_FREQ_DICT[hyd_refdes][1],
        title=hyd_refdes,
        ylim=(0.1, 100),
        cmlim=(32, 108),
        jpeg_filename=f"{str(output_dir)}/{instrument}_{start_date}.png",
        yscale="linear",
        cmap="inferno",
        show=True,
    )
