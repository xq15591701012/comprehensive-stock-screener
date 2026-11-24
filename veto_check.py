# -*- coding: utf-8 -*-
"""
公告一票否决判定器 v1.0  ·  配套 comprehensive-stock-screener v1.3.3

做什么：
  把「重大事件 / 公告」数据（NeoData events 或 westock data_notice 导出）
  按严重度三级 + 时效窗口判定，对最终候选池输出：
    VETO      高危且近期       → 直接降档至「剔除」
    DOWNGRADE 中危近期 / 高危超期 → 降一档或标「观察」
    NOTE      低危 / 中危超期   → 仅标注，不影响排名
    IGNORE    低危或过期很久    → 忽略

为什么必须分级（实测教训，2026-11-18）：
  NeoData events 的「风险提示」类返回的多是历史轻微事件——
  中国汽研 2026-02「出具监管关注函」、华测检测 2025「公告未披露 / 罚款 4 万」、
  徐工机械 2024「责令限期改正」。若只按类别「风险提示」或关键词「违规」一把抓，
  会把这些优质股误杀。因此：按 desc 的 reason/penalty 关键词分级 + 按事件日期时效衰减，
  且每条判定都给出命中词与理由，可审计、可复核。

输入：JSON 数组，每个元素至少含 stock_code/stock_name/event_date/desc，
      event_type_name/category/title 有则更好。字段名兼容 events 与 notice 两种来源。
用法：
  python veto_check.py --input events.json --asof 2026-11-18 \
      --out-json veto_result.json --out-csv veto_result.csv
  python veto_check.py --self-test      # 用内置实测样本自测，不联网

边界：
  - 只对「最终候选池」（建议 TOP 20~50）跑，不做全市场扫描（接口每批限 10 只、且有成本）。
  - 不联网、不编造数据；本脚本只做规则判定，数据由 agent 用 MCP 工具拉取后喂入。
  - 判定是「风险排列」，不是投资结论；命中 VETO 表示需人工复核，不等于永远排除。
"""
import json, csv, sys, os, re, argparse, datetime, collections

# ---------- 分级词表（按 desc 的 reason / penalty / 全文匹配，第一个命中的类别优先）----------
# 高危：实质影响持续经营或存在重大违法/退市/造假风险
HIGH = [
    "立案调查", "立案侦查", "移送司法", "涉嫌犯罪", "被采取强制措施", "留置", "失联",
    "重大违法", "强制退市", "退市风险警示", "*ST", "暂停上市", "终止上市",
    "财务造假", "虚假陈述", "欺诈发行", "重大遗漏", "业绩造假",
    "资不抵债", "债务违约", "债券违约", "破产重整", "破产清算", "银行账户冻结",
    "主要资产被查封", "实际控制人变更且失控", "违规担保", "资金占用",
]
# 中危：监管措施、一般处罚、股东减持、业绩明显下滑、减值
MID = [
    "行政处罚", "警告", "罚款", "没收", "责令改正", "限期整顿", "监管措施",
    "监管函", "监管关注函", "关注函", "问询函", "通报批评", "公开谴责", "警示函",
    "纪律处分", "行政处理", "立案", "减持", "计划减持", "股份质押", "高比例质押",
    "商誉减值", "资产减值", "业绩预减", "业绩下滑", "业绩变脸", "大额计提",
    "仲裁", "诉讼", "冻结", "违规处罚", "责令", "整改",
]
# 低危：常规资本运作 / 日常经营事件，本身不是利空
LOW = [
    "融资融券", "大宗交易", "股权变动", "分红", "送配", "回购", "增持",
    "中标", "合同", "调研", "机构调研", "股东户数", "龙虎榜", "解禁",
]
# 时效窗口（自然日，相对 --asof 基准日）
W_HIGH_VETO = 180       # 高危 ≤180 天 → VETO
W_HIGH_DOWN = 365       # 高危 181~365 天 → DOWNGRADE；>365 → NOTE
W_MID_DOWN = 90         # 中危 ≤90 天 → DOWNGRADE
W_MID_NOTE = 365        # 中危 91~365 天 → NOTE；>365 → IGNORE
ACT_RANK = {"IGNORE": 0, "NOTE": 1, "DOWNGRADE": 2, "VETO": 3}


def _date(s):
    if s is None:
        return None
    s = str(s).strip()[:10].replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", s):
        return None
    try:
        return datetime.datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def grade(ev, asof):
    """返回 dict：level / action / reason / hit / days。"""
    desc = " ".join(str(ev.get(k) or "") for k in
                    ("desc", "reason", "penalty", "title", "event_type_name", "content"))
    etype = str(ev.get("event_type_name") or ev.get("type") or "")
    cat = str(ev.get("category") or "")
    text = desc + " " + etype + " " + cat

    hit_hi = next((w for w in HIGH if w in text), None)
    hit_mid = next((w for w in MID if w in text), None)
    hit_low = next((w for w in LOW if w in text), None)
    d = _date(ev.get("event_date") or ev.get("info_publ_date") or ev.get("notice_date"))
    days = (asof - d).days if d else None

    if hit_hi:
        level = "高危"
        if days is None:
            action = "DOWNGRADE"          # 日期缺失保守处理，不直接否决
            why = "命中高危词「%s」但事件日期缺失，保守降档、需人工复核" % hit_hi
        elif days <= W_HIGH_VETO:
            action = "VETO"
            why = "命中高危词「%s」，发生于 %d 天前（≤%d），建议降档至剔除并人工复核" % (hit_hi, days, W_HIGH_VETO)
        elif days <= W_HIGH_DOWN:
            action = "DOWNGRADE"
            why = "命中高危词「%s」，发生于 %d 天前（%d~%d），时效衰减为降档观察" % (hit_hi, days, W_HIGH_VETO, W_HIGH_DOWN)
        else:
            action = "NOTE"
            why = "命中高危词「%s」但已 %d 天（>%d），仅保留标注" % (hit_hi, days, W_HIGH_DOWN)
        return dict(level=level, action=action, reason=why, hit=hit_hi, days=days)

    if hit_mid:
        level = "中危"
        if days is None:
            action, why = "NOTE", "命中中危词「%s」但日期缺失，仅标注" % hit_mid
        elif days <= W_MID_DOWN:
            action = "DOWNGRADE"
            why = "命中中危词「%s」，发生于 %d 天前（≤%d），建议降一档/标观察" % (hit_mid, days, W_MID_DOWN)
        elif days <= W_MID_NOTE:
            action = "NOTE"
            why = "命中中危词「%s」，发生于 %d 天前（%d~%d），仅标注" % (hit_mid, days, W_MID_DOWN, W_MID_NOTE)
        else:
            action, why = "IGNORE", "命中中危词「%s」但已 %d 天（>%d），忽略" % (hit_mid, days, W_MID_NOTE)
        return dict(level=level, action=action, reason=why, hit=hit_mid, days=days)

    if hit_low:
        return dict(level="低危", action="NOTE",
                    reason="仅命中常规事件词「%s」，本身非利空，仅记录" % hit_low,
                    hit=hit_low, days=days)

    # 兜底：类别是风险提示但没命中任何词 → 标注待人工判断，绝不自动否决
    if "风险" in cat or "违规" in etype:
        return dict(level="待判", action="NOTE",
                    reason="属「%s/%s」但描述未命中词表，需人工判断，不自动否决" % (cat, etype),
                    hit="", days=days)
    return dict(level="无", action="IGNORE", reason="未命中任何风险词表", hit="", days=days)


def dedup(events):
    """同一(代码,事件类型,日期,描述)去重——接口常把一条处罚拆成「被处罚/被警告」两条。"""
    seen, out = set(), []
    for ev in events:
        key = (str(ev.get("stock_code") or ""), str(ev.get("event_type_name") or ev.get("title") or ""),
               str(ev.get("event_date") or "")[:10], str(ev.get("desc") or ev.get("content") or "")[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def judge(events, asof, candidates=None):
    """按个股汇总，取最严重 action。candidates 为代码集合时只判这些票。"""
    by = collections.defaultdict(list)
    for ev in dedup(events):
        code = str(ev.get("stock_code") or ev.get("symbol") or "").replace(".SH", "").replace(".SZ", "")
        if candidates and code not in candidates:
            continue
        g = grade(ev, asof)
        g.update(stock_code=code, stock_name=ev.get("stock_name") or ev.get("name") or "",
                 event_type=ev.get("event_type_name") or ev.get("title") or "",
                 event_date=str(ev.get("event_date") or ev.get("info_publ_date") or "")[:10],
                 desc=str(ev.get("desc") or ev.get("content") or "")[:200])
        by[code].append(g)
    out = []
    for code, items in by.items():
        worst = max(items, key=lambda z: ACT_RANK[z["action"]])
        out.append(dict(stock_code=code, stock_name=worst["stock_name"],
                        action=worst["action"], level=worst["level"],
                        reason=worst["reason"], hit=worst["hit"],
                        event_date=worst["event_date"], event_type=worst["event_type"],
                        n_events=len(items),
                        detail=[{k: it[k] for k in ("event_date", "event_type", "level", "action", "hit", "reason")}
                                for it in sorted(items, key=lambda z: ACT_RANK[z["action"]], reverse=True)]))
    order = {"VETO": 0, "DOWNGRADE": 1, "NOTE": 2, "IGNORE": 3}
    out.sort(key=lambda z: (order[z["action"]], z["stock_code"]))
    return out


SELF_TEST = [
    # 真实实测样本（NeoData events，查询于 2026-11-18），覆盖各严重度
    {"stock_code": "601965.SH", "stock_name": "中国汽研", "event_type_name": "违规处罚", "category": "风险提示",
     "event_date": "20260212", "desc": "因出具监管关注函被行政处理"},
    {"stock_code": "300012.SZ", "stock_name": "华测检测", "event_type_name": "违规处罚", "category": "风险提示",
     "event_date": "20250908", "desc": "因公告未披露被行政处理，处分措施：公告未披露"},
    {"stock_code": "300012.SZ", "stock_name": "华测检测", "event_type_name": "违规处罚", "category": "风险提示",
     "event_date": "20240315", "desc": "给予警告，处罚款人民币4万元，公司罚款40000.00元"},
    {"stock_code": "000425.SZ", "stock_name": "徐工机械", "event_type_name": "违规处罚", "category": "风险提示",
     "event_date": "20240228", "desc": "因责令限期改正被限期整顿"},
    {"stock_code": "601717.SH", "stock_name": "中创智领", "event_type_name": "违规处罚", "category": "风险提示",
     "event_date": "20240603", "desc": "关于信托理财产品到期赎回事项的监管工作函被行政处理"},
    {"stock_code": "600519.SH", "stock_name": "贵州茅台", "event_type_name": "融资融券", "category": "交易提示",
     "event_date": "20261116", "desc": "融资余额147.89亿元"},
    # 构造的高危近期样本（验证 VETO 通路），仅测试用、非真实事件
    {"stock_code": "TEST01.SZ", "stock_name": "测试-立案", "event_type_name": "立案调查", "category": "风险提示",
     "event_date": "20261101", "desc": "因涉嫌信息披露违法违规被立案调查"},
]


def main():
    ap = argparse.ArgumentParser(description="公告一票否决判定器 v1.0")
    ap.add_argument("--input", help="events/notice 导出的 JSON 数组文件")
    ap.add_argument("--asof", default=None, help="基准日 YYYY-MM-DD，默认今天")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-csv", default=None)
    ap.add_argument("--candidates", default=None, help="只判这些代码，逗号分隔（6位数字）")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    asof = _date(a.asof) if a.asof else datetime.date.today()
    if a.self_test:
        events = SELF_TEST
        print("== 自测模式（内置真实样本 + 1 条构造高危样本）asof=%s ==" % asof)
    else:
        if not a.input:
            print("错误：需 --input 或 --self-test", file=sys.stderr)
            sys.exit(2)
        with io_open(a.input) as f:
            data = json.load(f)
        events = data.get("result", data) if isinstance(data, dict) else data
        if not isinstance(events, list):
            print("错误：输入 JSON 应为数组或含 result 数组", file=sys.stderr)
            sys.exit(2)
        print("读入 %d 条事件，asof=%s" % (len(events), asof))

    cands = set(x.strip() for x in a.candidates.split(",") if x.strip()) if a.candidates else None
    res = judge(events, asof, cands)
    cnt = collections.Counter(r["action"] for r in res)
    print("判定 %d 只：VETO %d / DOWNGRADE %d / NOTE %d / IGNORE %d" %
          (len(res), cnt["VETO"], cnt["DOWNGRADE"], cnt["NOTE"], cnt["IGNORE"]))
    for r in res[:12]:
        print("  [%s] %s %s | %s" % (r["action"], r["stock_code"], r["stock_name"], r["reason"]))

    if a.out_json:
        with io_open(a.out_json, "w") as f:
            json.dump(dict(asof=str(asof), n=len(res), counts=dict(cnt), results=res),
                      f, ensure_ascii=False, indent=2)
        print("JSON:", a.out_json)
    if a.out_csv:
        with io_open(a.out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["代码", "名称", "最终判定", "级别", "命中词", "事件日期", "事件类型", "事件数", "理由"])
            for r in res:
                w.writerow([r["stock_code"], r["stock_name"], r["action"], r["level"], r["hit"],
                            r["event_date"], r["event_type"], r["n_events"], r["reason"]])
        print("CSV:", a.out_csv)


def io_open(path, mode="r", **kw):
    kw.setdefault("encoding", "utf-8-sig" if mode == "r" else "utf-8")
    return open(path, mode, **kw)


if __name__ == "__main__":
    main()
