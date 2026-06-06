import argparse
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=str, default="logs/strategy_daily_metrics.csv",
                    help="Daily metrics CSV to analyze.")
    args = ap.parse_args()
    df = pd.read_csv(args.csv, parse_dates=["date"])
    df = df[df["mode"] == "dl"].copy()

    print("总样本数:", len(df))
    if "crash_cap_hit" in df.columns:
        print("crash_cap_hit 次数:", int(df["crash_cap_hit"].sum()))
    if "qqq5_cap_hit" in df.columns:
        print("qqq5_cap_hit 次数:", int(df["qqq5_cap_hit"].sum()))

    if "crash_cap_hit" in df.columns:
        hit_rows = df[df["crash_cap_hit"] == 1][
            [
                "date",
                "crash_prob",
                "L_base_pre_crash",
                "crash_L_cap",
                "effective_leverage",
            ]
        ]
        print("\n有 crash_cap_hit 的日期示例:")
        print(hit_rows.head(20))

    if "crash_prob" in df.columns:
        print("\ncrash_prob 分布 (简单分位数):")
        print(df["crash_prob"].quantile([0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]))

        subset = df[df["crash_prob"] >= 0.3]
        print("\n在 crash_prob >= 0.3 的天数:", len(subset))
        if len(subset) > 0:
            cols = [
                "date",
                "crash_prob",
                "L_base_pre_crash",
                "crash_L_cap",
                "effective_leverage",
            ]
            print(subset[cols].head(20))


if __name__ == "__main__":
    main()
