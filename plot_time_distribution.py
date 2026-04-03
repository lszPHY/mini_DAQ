import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = "/Users/lishuzhe/Desktop/test data/run00131_20260309_164054_filtered_kept_matches.csv"

def main():
    df = pd.read_csv(CSV_PATH)

    print(df.head())
    print("rows =", len(df))

    # 1) dt 直方图：看 keep 下来的匹配是不是在 -50000 ~ 50000 内随机分布
    plt.figure(figsize=(8, 5))
    plt.hist(df["dt_ns_wrapped"], bins=400, range=(-50000, 50000))
    plt.xlabel("dt_ns_wrapped = event_min_hit_time_ns - trigger_time_ns (ns)")
    plt.ylabel("Counts")
    plt.title("Kept matches: dt distribution")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    # 2) trigger_time 绝对值分布
    plt.figure(figsize=(8, 5))
    plt.hist(df["trigger_time_ns"], bins=200, range=(0, 102400))
    plt.xlabel("trigger_time_ns")
    plt.ylabel("Counts")
    plt.title("Kept matches: trigger absolute time")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    # 3) earliest hit 绝对值分布
    plt.figure(figsize=(8, 5))
    plt.hist(df["event_min_hit_time_ns"], bins=200, range=(0, 102400))
    plt.xlabel("event_min_hit_time_ns")
    plt.ylabel("Counts")
    plt.title("Kept matches: earliest-hit absolute time")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    # 4) trigger vs earliest-hit 散点图
    plt.figure(figsize=(6, 6))
    plt.scatter(
        df["trigger_time_ns"],
        df["event_min_hit_time_ns"],
        s=4,
        alpha=0.3,
    )
    plt.xlabel("trigger_time_ns")
    plt.ylabel("event_min_hit_time_ns")
    plt.title("Kept matches: trigger vs earliest-hit")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()