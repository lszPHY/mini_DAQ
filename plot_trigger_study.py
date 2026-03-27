import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit

CSV_PATH = "/Users/lishuzhe/Desktop/test data/run00131_20260309_164054_filtered_trigger_study.csv"   # file outpath

XMIN, XMAX = -1000, 1000
NBINS_ALL_HIT = 800
NBINS_EVENT = 800
HIST_RANGE = (XMIN, XMAX)



def gaussian(x, amp, mu, sigma):
    return amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def fit_gaussian_from_hist(values, bins, fit_range=None):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return None

    if fit_range is not None:
        lo, hi = fit_range
        values = values[(values >= lo) & (values <= hi)]
        if values.size == 0:
            return None

    counts, edges = np.histogram(values, bins=bins, range=HIST_RANGE)
    centers = 0.5 * (edges[:-1] + edges[1:])

    mask = counts > 0
    x = centers[mask]
    y = counts[mask]
    if x.size < 5:
        return None

    peak_idx = np.argmax(y)
    mu0 = x[peak_idx]
    sigma0 = max(np.std(values), 20.0)
    amp0 = float(np.max(y))

    try:
        popt, pcov = curve_fit(
            gaussian,
            x,
            y,
            p0=[amp0, mu0, sigma0],
            bounds=([0.0, XMIN, 1.0], [np.inf, XMAX, 2000.0]),
            maxfev=20000,
        )
        amp, mu, sigma = popt
        variance = sigma ** 2
        return {
            "amp": float(amp),
            "mu": float(mu),
            "sigma": float(sigma),
            "variance": float(variance),
            "x": x,
            "y": y,
            "centers": centers,
            "counts": counts,
        }
    except Exception as e:
        print(f"[WARN] Gaussian fit failed: {e}")
        return None
def plot_all_hit_deltas(df):
    offsets = [1]

    for off in offsets:
        sub = df[df["candidate_offset"] == off]
        if sub.empty:
            print(f"[WARN] no data for offset {off}")
            continue
        values = sub["delta_ns_wrapped"].to_numpy(dtype=float)
        fit = fit_gaussian_from_hist(values, bins=NBINS_ALL_HIT, fit_range=HIST_RANGE)


        if fit is not None:
            xfit = np.linspace(XMIN, XMAX, 1000)
            yfit = gaussian(xfit, fit["amp"], fit["mu"], fit["sigma"])
            plt.plot(xfit, yfit, linewidth=2)
            print(
                f"[ALL HIT][offset {off}] mean = {fit['mu']:.3f} ns, "
                f"sigma = {fit['sigma']:.3f} ns, variance = {fit['variance']:.3f} ns^2"
            )
        else:
            print(f"[ALL HIT][offset {off}] Gaussian fit unavailable")

        plt.figure(figsize=(8, 5))
        plt.hist(sub["delta_ns_wrapped"], bins=400,range=HIST_RANGE)
        plt.xlim(-1000,1000)
        plt.xlabel("hit_time - trigger_time (ns)")
        plt.ylabel("Counts")
        plt.title(f"All-hit Δt histogram, candidate trigger offset = {off}")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


def plot_event_min_hit_deltas(df):
    offsets = [-2, -1, 1, 2]

    event_level = (
        df[["event_id", "candidate_offset", "event_min_hit_delta_ns_wrapped"]]
        .drop_duplicates()
        .copy()
    )

    for off in offsets:
        sub = event_level[event_level["candidate_offset"] == off]
        if sub.empty:
            print(f"[WARN] no event-level data for offset {off}")
            continue

        plt.figure(figsize=(8, 5))
        plt.hist(sub["event_min_hit_delta_ns_wrapped"], bins=200)
        plt.xlim(-1000,1000)
        plt.xlabel("event_min_hit_time - trigger_time (ns)")
        plt.ylabel("Counts")
        plt.title(f"Event-min-hit Δt histogram, candidate trigger offset = {off}")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


def plot_overlay_event_min_hit(df):
    offsets = [-2, -1, 1, 2]

    event_level = (
        df[["event_id", "candidate_offset", "event_min_hit_delta_ns_wrapped"]]
        .drop_duplicates()
        .copy()
    )

    plt.figure(figsize=(9, 6))
    for off in offsets:
        sub = event_level[event_level["candidate_offset"] == off]
        if sub.empty:
            continue
        plt.hist(
            sub["event_min_hit_delta_ns_wrapped"],
            bins=200,
            histtype="step",
            linewidth=1.5,
            label=f"offset {off}",
        )

    plt.xlabel("event_min_hit_time - trigger_time (ns)")
    plt.ylabel("Counts")
    plt.title("Overlay of event-level Δt for candidate triggers")
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


def main():
    df = pd.read_csv(CSV_PATH)
    print(df.head())
    print(df.columns.tolist())

    plot_all_hit_deltas(df)
    plot_event_min_hit_deltas(df)
    plot_overlay_event_min_hit(df)


if __name__ == "__main__":
    main()