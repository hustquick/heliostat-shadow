"""Render external search JSON comparisons as a reproducible report and figure."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('comparisons',nargs='+',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    inputs=[json.loads(path.read_text()) for path in a.comparisons]
    rows=[]
    for data in inputs:
        for report in data['reports']:
            if 'annual_candidate_kwh' not in report:
                raise ValueError('Report requires independent full-year validation')
            rows.append((data['search_samples'],report))
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    labels=[f'{n} nodes\n{r["mode"]}' for n,r in rows]
    gains=[(r['annual_candidate_kwh']-r['annual_baseline_kwh'])/1000 for _,r in rows]
    colours=['#4279b6' if r['annual_accepted'] else '#b65555' for _,r in rows]
    axes[0].bar(labels,gains,color=colours);axes[0].axhline(0,color='black',lw=.6)
    axes[0].set_ylabel('Full-hour annual receiver energy gain (MWh)')
    axes[0].set_title('One round; same initial PS10 field')
    for k,value in enumerate(gains): axes[0].annotate(f'{value:.2f}',(k,value),xytext=(0,4),textcoords='offset points',ha='center',fontsize=8)
    axes[1].bar(labels,[r['evaluations'] for _,r in rows],color='#679d8a')
    axes[1].set_ylabel('Full-field search evaluations (including baseline)')
    axes[1].set_title('Candidate budgets differ; no speedup claim')
    for ax in axes: ax.tick_params(axis='x',labelsize=8);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.savefig(a.output/'comparison.png',dpi=180);plt.close(fig)
    lines=['# PS10 APP 外镜场优化验算','',
        '本轮验证搜索和年度验收流程。固定 624 面镜子的尺寸、高度、光学参数与接收器，三种搜索都从同一目录重建布局出发；只运行一轮、步长 2 m。不是连续坐标空间全局最优或 SOTA 算法排名。','',
        '全年气象为 2025 年 pvlib 晴空估计，8760 个 UTC 小时中 4402 个有效日照小时。光学评分使用 shared_rust；年能量是接收器入射光能量，不是发电量。','',
        '| 搜索方向数 | 方法 | 全年能量增量 MWh | 全年相对增益 | 全场搜索评估数 | 搜索秒数 | 年度验收 |',
        '|---:|---|---:|---:|---:|---:|---|']
    for n,r in rows:
        delta=(r['annual_candidate_kwh']-r['annual_baseline_kwh'])/1000
        lines.append(f'| {n} | {r["mode"]} | {delta:.4f} | {r["annual_relative_gain"]*100:.5f}% | {r["evaluations"]} | {r["elapsed_search_seconds"]:.2f} | {"通过" if r["annual_accepted"] else "回退原布局"} |')
    lines+=['','![全年增量与计算次数](comparison.png)','',
        '搜索耗时不包括全年复核；部分任务同时运行，用时只描述本轮执行，不能用于严格性能比较。评估次数更适合衡量本轮计算预算。物理模式保留四轴方向并增加提案，因此可能更慢；需要另做同预算收益曲线才能证明加速。','',
        '区域由 DNI 加权的潜在入射/反射光线耦合图提出。第一版仅取最强的 16 个镜对时，联合移动全部被保守间距约束排除；修正版跳过不能形成合法联合候选的镜对，并记录实际的联合评估数。4 方向结果保留为首版流程对照，16 方向协同结果使用可移动性筛选。','']
    for n,r in rows:
        error=(r['baseline_search_kwh']/r['annual_baseline_kwh']-1)*100
        lines.append(f'- {n} 方向 {r["mode"]}：基线年能量近似误差 {error:.4f}%；最终候选类型 {r["history"][-1].get("kind","无移动")}。')
        for h in r['history']:
            if h.get('evaluated_by_kind'):
                lines.append(f'  - 合法候选数：{json.dumps(h["evaluated_by_kind"],ensure_ascii=False)}；相对该轮基线的最好收益 kWh：{json.dumps(h["best_gain_kwh_by_kind"],ensure_ascii=False)}。')
    lines+=['',
        '若协同模式最终仍选择单镜，表示本轮定义的联合候选未胜过最佳单镜，不能据此声称协同算法提高了收益；也不能据此否定多轮、更大步长或三镜区域。保留年度增益阈值，并在失败时输出原布局。','',
        '边界使用初始镜子中心凸包，间距采用两镜半对角线之和再加 1 m，均是实验约束。未加入道路、禁入区、地形、实测气象、运行可用率或发电模型。后续应加密至全部月/小时节点，使用多步长与多轮，并在同等计算预算下比较。','',
        '原始结果：'+', '.join(str(path) for path in a.comparisons)+'.','']
    (a.output/'RESULTS.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
