# -*- coding: utf-8 -*-
"""
IC 时间序列跟踪框架 v1.0  ·  配套 comprehensive-stock-screener v1.3.3

解决什么：
  单个截面的 IC（如 11-10→11-18 的 0.28）不能说明因子稳定。判断因子可用需要
  IC 序列的均值/标准差/IR/正率（经验阈值 IR>0.5、正率>55%）。本框架把每次
  「截面评分 → 前瞻收益」登记为一个观测点，累积后自动算这些统计量。

设计：
  - 存储：一个 JSON 库文件（默认 data/ic_timeseries.json），append-only。
  - 观测点 = 一对截面：t0（评分日，含每只股票分数）+ t1（收益结算日，含每只股票收益）。
  - IC = Spearman(分数, 区间收益)；同时可登记多口径 IC（raw / 中性化 / 分组）。
  - 数据源来自本地快照与复现脚本，本工具不联网、不编造数据。

命令：
  python ic_tracker.py --db data/ic_timeseries.json init
  python ic_tracker.py --db ... add --t0 2026-11-10 --t1 2026-11-18 \
      --n 4411 --ic-raw 0.2806 --ic-neu 0.1686 --note "普跌窗口,6交易日" \
      --scores data/全个股评分明细_20261110.csv --old data/eastmoney_snapshot_20261110.csv \
      --new data/eastmoney_snapshot_20261118.csv
      （带 --scores/--old/--new 时从原始文件现算 IC；只带 --ic-raw 时登记外部已算好的值）
  python ic_tracker.py --db ... report        # 输出 IC 序列、均值、std、IR、正率、样本数告警
  python ic_tracker.py --db ... list

边界：
  - 观测点 < 8 时 report 明确标注「样本不足，IR 不具统计意义」，不给出因子可用结论。
  - 收益为两期不复权收盘价直接计算，无复权/成本/滑点；与复核报告口径一致。
"""
import json, csv, sys, os, math, statistics, argparse, datetime

MAX_POINTS_WARN = 8   # IR 至少 8 个观测点才有讨论价值


def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    def ranks(a):
        idx = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n and a[idx[j]] == a[idx[i]]:
                j += 1
            av = (i + j - 1) / 2 + 1
            for k in range(i, j):
                r[idx[k]] = av
            i = j
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    return num / (dx * dy) if dx and dy else None


def num(x, k):
    v = x.get(k)
    if v in (None, "", "-", "null", "None"):
        return None
    try:
        f = float(v)
        return f if f == f else None
    except Exception:
        return None


def calc_ic_from_files(scores_csv, old_csv, new_csv):
    """从评分明细 + 两期快照现算 IC（按代码对齐收盘价）。"""
    def load(p):
        with open(p, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    det = load(scores_csv)
    op = {x.get("SECURITY_CODE") or x.get("代码"): num(x, "NEW_PRICE")
          for x in load(old_csv)}
    np = {x.get("SECURITY_CODE") or x.get("代码"): num(x, "NEW_PRICE")
          for x in load(new_csv)}
    xs, ys = [], []
    for d in det:
        c = d.get("代码") or d.get("SECURITY_CODE")
        sc = num(d, "归一化分") or num(d, "v132总分")
        p0, p1 = op.get(c), np.get(c)
        if None in (sc, p0, p1) or p0 <= 0 or p1 <= 0:
            continue
        xs.append(sc)
        ys.append((p1 / p0 - 1) * 100)
    return spearman(xs, ys), len(xs)


def load_db(path):
    if not os.path.exists(path):
        return dict(version=1, created=str(datetime.date.today()), points=[])
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_db(path, db):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def report(db):
    pts = db.get("points", [])
    print("IC 观测点数：%d" % len(pts))
    if not pts:
        print("  库为空，先 add 观测点。")
        return
    print("  %-12s %-12s %6s %9s %9s %8s %s" %
          ("评分日", "结算日", "N", "IC(raw)", "IC(中性)", "交易日", "备注"))
    for p in pts:
        print("  %-12s %-12s %6s %9s %9s %8s %s" %
              (p.get("t0", ""), p.get("t1", ""), p.get("n", ""),
               fmt(p.get("ic_raw")), fmt(p.get("ic_neutral")),
               p.get("trading_days", ""), (p.get("note") or "")[:28]))
    for key, label in (("ic_raw", "IC(raw)"), ("ic_neutral", "IC(中性化)")):
        vals = [p[key] for p in pts if isinstance(p.get(key), (int, float))]
        if not vals:
            continue
        mu = statistics.mean(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        ir = mu / sd if sd else None
        pos = sum(1 for v in vals if v > 0) / len(vals)
        print("\n%s 序列：均值 %.4f ｜ 标准差 %.4f ｜ IR %s ｜ 正率 %.1f%% ｜ 点数 %d" %
              (label, mu, sd, ("%.3f" % ir) if ir is not None else "不可得", 100 * pos, len(vals)))
    if len(pts) < MAX_POINTS_WARN:
        print("\n⚠ 样本不足：仅 %d 个观测点（建议 ≥%d）。IR 与正率此刻不具统计意义，"
              "不得据此判定因子可用或据此调权重。" % (len(pts), MAX_POINTS_WARN))
    else:
        print("\n样本量达到 %d 点：可开始讨论 IR>0.5 / 正率>55% 的可用门槛。" % len(pts))


def fmt(v):
    return ("%.4f" % v) if isinstance(v, (int, float)) else "—"


def main():
    ap = argparse.ArgumentParser(description="IC 时间序列跟踪框架 v1.0")
    ap.add_argument("--db", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "data", "ic_timeseries.json"))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init")
    sub.add_parser("list")
    sub.add_parser("report")
    ad = sub.add_parser("add")
    ad.add_argument("--t0", required=True, help="评分日 YYYY-MM-DD")
    ad.add_argument("--t1", required=True, help="收益结算日 YYYY-MM-DD")
    ad.add_argument("--n", type=int, default=None, help="配对样本数")
    ad.add_argument("--ic-raw", type=float, default=None)
    ad.add_argument("--ic-neutral", type=float, default=None)
    ad.add_argument("--trading-days", type=int, default=None)
    ad.add_argument("--note", default="")
    ad.add_argument("--scores", default=None, help="评分明细 CSV（现算 IC 用）")
    ad.add_argument("--old", default=None, help="t0 快照 CSV（现算 IC 用）")
    ad.add_argument("--new", default=None, help="t1 快照 CSV（现算 IC 用）")
    a = ap.parse_args()

    db = load_db(a.db)
    if a.cmd == "init":
        save_db(a.db, db)
        print("初始化:", a.db, "观测点", len(db["points"]))
        return
    if a.cmd in (None, "list", "report"):
        report(db)
        return
    if a.cmd == "add":
        ic, n = a.ic_raw, a.n
        if a.scores and a.old and a.new:
            ic, n = calc_ic_from_files(a.scores, a.old, a.new)
            print("从原始文件现算 IC=%.4f (n=%d)" % (ic, n))
        if ic is None:
            print("错误：需 --ic-raw 或同时给 --scores/--old/--new", file=sys.stderr)
            sys.exit(2)
        if any(p.get("t0") == a.t0 and p.get("t1") == a.t1 for p in db["points"]):
            print("已存在同 t0/t1 观测点，未重复写入。")
            return
        db["points"].append(dict(
            t0=a.t0, t1=a.t1, n=n, ic_raw=ic, ic_neutral=a.ic_neutral,
            trading_days=a.trading_days, note=a.note,
            added=str(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))
        save_db(a.db, db)
        print("已登记观测点：%s → %s，IC=%.4f，累计 %d 点" % (a.t0, a.t1, ic, len(db["points"])))
        report(db)


if __name__ == "__main__":
    main()
