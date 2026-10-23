"""Export scientific figures from the saved report; requires the experiments dependency group."""

import argparse
import json
from pathlib import Path
from statistics import mean, median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("../docs/cp-sat-experiments.json"))
    parser.add_argument("--output", type=Path, default=Path("../docs/cp-sat-experiments.png"))
    args = parser.parse_args()
    report = json.loads(args.input.read_text("utf-8"))
    rows = [r for r in report["runs"] if not r["sensitivity"] and r["family"] != "control"]
    sizes = sorted({r["size"] for r in rows})
    algorithms = ["greedy", "beam", "cp-sat"]
    names = ["Greedy", "Beam", "CP-SAT"]
    colors = ["#bf6548", "#31594a", "#3d6fa3"]
    common = {}
    for instance in sorted({r["instance"] for r in rows}):
        best = {
            a: max(
                (
                    r["objective"]
                    for r in rows
                    if r["instance"] == instance and r["algorithm"] == a and r["feasible"]
                ),
                default=None,
            )
            for a in algorithms
        }
        if all(v is not None for v in best.values()):
            size = next(r["size"] for r in rows if r["instance"] == instance)
            common[instance] = (size, {a: max(best.values()) - best[a] for a in algorithms})
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.5), layout="constrained")
    for algorithm, label, color in zip(algorithms, names, colors, strict=True):
        medians, success, regrets = [], [], []
        for size in sizes:
            selected = [r for r in rows if r["size"] == size and r["algorithm"] == algorithm]
            medians.append(median(r["elapsed_seconds"] for r in selected))
            success.append(sum(r["feasible"] for r in selected) / len(selected))
            paired = [values[algorithm] for n, values in common.values() if n == size]
            regrets.append(mean(paired) if paired else float("nan"))
        axes[0].plot(sizes, medians, "o-", label=label, color=color)
        axes[1].plot(sizes, success, "o-", label=label, color=color)
        axes[2].plot(sizes, regrets, "o-", label=label, color=color)
    axes[0].set(
        title="Время: подготовка + поиск + проверка", ylabel="Медиана, с (логарифмическая шкала)"
    )
    axes[0].set_yscale("log")
    axes[1].set(title="Найден допустимый маршрут", ylabel="Доля запусков", ylim=(-0.05, 1.08))
    axes[2].set(
        title="Качество на общих решённых задачах", ylabel="Средний разрыв F до лучшего найденного"
    )
    for size in sizes:
        count = sum(n == size for n, _ in common.values())
        axes[2].annotate(
            f"n={count}",
            (size, 0),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            color="#555555",
        )
    for axis in axes:
        axis.set_xticks(sizes)
        axis.set_xlabel("Размер исходного каталога, POI")
        axis.grid(alpha=0.2)
    axes[0].legend(frameon=False)
    figure.suptitle(
        f"Синтетические задачи: 1 и 3 дня, два seed, "
        f"CP-SAT до {report['configuration']['time_limit']:g} с",
        fontsize=13,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=180)
    svg = args.output.with_suffix(".svg")
    figure.savefig(svg)
    svg.write_text(
        "\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(figure)
    print(args.output)


if __name__ == "__main__":
    main()
