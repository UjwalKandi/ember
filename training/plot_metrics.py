"""Plot train/eval loss and learning rate from a sft_lora.py metrics.csv (Lab 04).

    python training/plot_metrics.py runs/qwen3-1.7b-lora/metrics.csv
How to read it: train loss should fall smoothly; eval loss should fall and then flatten.
Eval loss rising while train loss keeps falling = overfitting (fewer epochs, lower lr or rank).
"""
import csv
import sys
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/lora/metrics.csv")
    rows = list(csv.DictReader(open(path)))
    series = {k: [(int(r["step"]), float(r[k])) for r in rows if r.get(k)] for k in ("loss", "eval_loss", "learning_rate")}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4))
    for key, label in (("loss", "train loss"), ("eval_loss", "eval loss")):
        if series[key]:
            a.plot(*zip(*series[key]), label=label, marker="o" if key == "eval_loss" else None)
    a.set_xlabel("optimizer step"); a.set_ylabel("cross-entropy (assistant tokens)"); a.legend(); a.grid(alpha=.3)
    if series["learning_rate"]:
        b.plot(*zip(*series["learning_rate"]), color="tab:green")
    b.set_xlabel("optimizer step"); b.set_ylabel("learning rate"); b.grid(alpha=.3)
    out = path.with_suffix(".png")
    fig.tight_layout(); fig.savefig(out, dpi=120)
    print("saved", out)
    for key in ("loss", "eval_loss"):
        if series[key]:
            print(f"{key}: first {series[key][0][1]:.3f} -> last {series[key][-1][1]:.3f}")


if __name__ == "__main__":
    main()
