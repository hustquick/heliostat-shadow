"""Build a Chinese calculation note and verify saved output reconciliation."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'reports/gemasolar_2023'
    daily=pd.read_csv(out/'daily_summary.csv')
    hourly=pd.read_csv(out/'hourly_summary.csv')
    detail=pd.read_csv(out/'per_mirror.csv.gz')
    run=json.loads((out/'run_manifest.json').read_text())
    quality=json.loads((ROOT/'reports/data_quality.json').read_text())
    audit=json.loads((ROOT/'reports/geometry_audit.json').read_text())
    if detail.duplicated(['time_utc','mirror_id']).any():raise AssertionError('Duplicate mirror-time rows')
    calculated=hourly[hourly.status=='calculated'].set_index('time_utc')
    powers=('incident_normal','incident_cosine','geometric_usable','post_atmosphere',
            'cosine_loss','joint_loss','atmospheric_loss','reflected','reflection_loss',
            'clean','cleanliness_loss','atmosphere_after_reflection',
            'atmospheric_after_reflection_loss','optical_upper_bound','interception_loss',
            'receiver_incident','receiver_absorbed','receiver_absorption_loss',
            'receiver_net_thermal','receiver_thermal_loss')
    for name in powers:
        totals=detail.groupby('time_utc')[f'{name}_power_w'].sum()/1e6
        np.testing.assert_allclose(totals.sort_index(),calculated[f'{name}_mw'].sort_index(),atol=1e-9,rtol=1e-12)
        days=hourly.groupby('date_local')[f'{name}_mw'].sum().sort_index()
        np.testing.assert_allclose(days,daily.set_index('date_local')[f'{name}_mwh_estimate'].sort_index(),atol=1e-9,rtol=1e-12)
    balance=detail[['cosine_loss_power_w','joint_loss_power_w','atmospheric_loss_power_w','post_atmosphere_power_w']].sum(axis=1)
    np.testing.assert_allclose(balance,detail.incident_normal_power_w,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.incident_cosine_power_w,detail.incident_normal_power_w*detail.eta_cosine,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.post_atmosphere_power_w,detail.geometric_usable_power_w*detail.eta_atmosphere,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.clean_power_w,detail.reflected_power_w*detail.mirror_cleanliness,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.atmosphere_after_reflection_power_w,detail.clean_power_w*detail.eta_atmosphere,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.receiver_incident_power_w,detail.atmosphere_after_reflection_power_w*detail.eta_intercept,atol=1e-8,rtol=1e-12)
    np.testing.assert_allclose(detail.receiver_absorbed_power_w,detail.receiver_incident_power_w*detail.receiver_absorptivity,atol=1e-8,rtol=1e-12)
    full_balance=detail[['reflection_loss_power_w','cleanliness_loss_power_w',
                         'atmospheric_after_reflection_loss_power_w','interception_loss_power_w',
                         'receiver_absorption_loss_power_w','receiver_thermal_loss_power_w',
                         'receiver_net_thermal_power_w']].sum(axis=1)
    np.testing.assert_allclose(full_balance,detail.geometric_usable_power_w,atol=1e-8,rtol=1e-12)
    if (detail[['cosine_loss_power_w','joint_loss_power_w','atmospheric_loss_power_w']] < -1e-8).any().any():
        raise AssertionError('Negative loss power')
    counts=detail.groupby('time_utc').mirror_id.nunique()
    if not (counts==2650).all():raise AssertionError('Incomplete mirror results')
    for key in ['eta_cosine','eta_shadow','eta_blocking','eta_joint','eta_atmosphere','eta_intercept','eta_optical_upper_bound','eta_absorbed','eta_net_thermal']:
        if not detail[key].between(0,1).all():raise AssertionError('Efficiency outside [0,1]')
    if (detail.eta_joint>detail[['eta_shadow','eta_blocking']].min(axis=1)+1e-9).any():
        raise AssertionError('Invalid joint loss ordering')
    if len(detail)!=run['full_field_daylight_samples']*2650:raise AssertionError('Unexpected result count')
    validation=dict(full_mirror_time_rows=len(detail),daylight_timestamps=len(calculated),mirror_count_each_time=2650,
                    duplicate_mirror_time_rows=0,all_efficiencies_in_unit_interval=True,joint_loss_ordering_passed=True,
                    mirror_power_sum_matches_field_summary=True,daily_sums_match_hourly=True,
                    sequential_energy_balance_passed=True,cosine_applied_once=True,
                    full_optical_chain_balance_passed=True,
                    distance_range_m=[float(detail.receiver_distance_m.min()),float(detail.receiver_distance_m.max())],
                    atmospheric_loss_range_percent=[float(100*(1-detail.eta_atmosphere.max())),float(100*(1-detail.eta_atmosphere.min()))])
    (out/'output_validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    peak=hourly.loc[hourly.geometric_usable_mw.idxmax()]
    table='| 当地日期 | DNI 日辐照量估算 (kWh/m²) | 联合几何损失 (%) | 几何可用光能估算 (MWh) | 峰值几何光功率 (MW) |\n|---|---:|---:|---:|---:|\n'
    for row in daily.itertuples():
        table+=f'| {row.date_local} | {row.dni_kwh_m2_estimate:.3f} | {row.loss_joint_percent:.2f} | {row.geometric_usable_mwh_estimate:.2f} | {row.peak_geometric_mw:.2f} |\n'
    losses='| 当地日期 | 余弦 | 阴影/遮挡 | 反射率 | 清洁度 | 沿程大气 | 截获 | 接收器吸收 | 全链光学效率 | 净热效率场景 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    budget='| 当地日期 | DNI×面积基准 | 余弦损失 | 阴影/遮挡损失 | 反射率损失 | 清洁度损失 | 沿程损失 | 截获损失 | 接收器吸收损失 | 接收器热损失场景 | 净热剩余 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    for row in daily.itertuples():
        losses+=f'| {row.date_local} | {100*row.incident_cosine_mwh_estimate/row.incident_normal_mwh_estimate:.2f}% | {100*row.geometric_usable_mwh_estimate/row.incident_cosine_mwh_estimate:.2f}% | {100*row.reflected_mwh_estimate/row.geometric_usable_mwh_estimate:.2f}% | {100*row.clean_mwh_estimate/row.reflected_mwh_estimate:.2f}% | {100*row.atmosphere_after_reflection_mwh_estimate/row.clean_mwh_estimate:.2f}% | {100*row.receiver_incident_mwh_estimate/row.atmosphere_after_reflection_mwh_estimate:.2f}% | {100*row.receiver_absorbed_mwh_estimate/row.receiver_incident_mwh_estimate:.2f}% | {row.eta_optical_upper_bound_percent:.2f}% | {row.eta_net_thermal_percent:.2f}% |\n'
        budget+=f'| {row.date_local} | {row.incident_normal_mwh_estimate:.2f} | {row.cosine_loss_mwh_estimate:.2f} | {row.joint_loss_mwh_estimate:.2f} | {row.reflection_loss_mwh_estimate:.2f} | {row.cleanliness_loss_mwh_estimate:.2f} | {row.atmospheric_after_reflection_loss_mwh_estimate:.2f} | {row.interception_loss_mwh_estimate:.2f} | {row.receiver_absorption_loss_mwh_estimate:.2f} | {row.receiver_thermal_loss_mwh_estimate:.2f} | {row.receiver_net_thermal_mwh_estimate:.2f} |\n'
    literature=[
        dict(source='本项目（四个2023季节算例）',value=float(daily.eta_optical_upper_bound_percent.mul(daily.incident_normal_mwh_estimate).sum()/daily.incident_normal_mwh_estimate.sum()),
             scope='公开重建布局；历史PVGIS DNI；140 m；ρ=0.88；清洁度=0.95；有限圆柱截获模型'),
        dict(source='Collado & Guallar 2016, Table 1',value=58.71,scope='Gemasolar-like；年度TMY；RR=4 m；THT=140 m；ρ=0.88；清洁度=0.95；campo field efficiency'),
        dict(source='Wang et al. 2019, Table 3',value=63.60,scope='Gemasolar design point estimate；ρ=0.88；清洁度=0.95；147 m tower'),
    ]
    comparison='| 来源 | 光学/场效率 (%) | 口径与限制 |\n|---|---:|---|\n'
    for item in literature:comparison+=f"| {item['source']} | {item['value']:.2f} | {item['scope']} |\n"
    model_eff=literature[0]['value']
    text=f'''# Gemasolar 历史 DNI 与镜场完整光学链计算

计算对象为公开提取的 Gemasolar 2,650 面镜研究重建布局；辐射输入来自欧盟 PVGIS-SARAH3
的 2023 年历史卫星数据。**不是业主竣工坐标、现场 DNI 实测或实际电站发电量验证。**

## 数据与计算范围

- 全年输入：8,760 条小时采样记录，原时间为 UTC 每小时 :10，0 个缺失/重复时间。
- 年 DNI 辐照量约 **{quality['radiation']['annual_dni_kwh_m2_hourly_sample_estimate']:.2f} kWh/m²**，由小时采样乘 1 h 近似积分。
- 几何计算：4 个指定当地日期、96 个小时记录，其中 {len(calculated)} 个日间时刻逐一计算全部 2,650 面镜，
  共 **{len(detail):,} 条镜面—时刻结果**。夜间功率置零，几何效率记为不适用。
- 阴影和遮挡区域投回同一镜面后求并集，未使用两项效率相乘近似。
- 四个日期属于季节算例，不是按月或全年加权的典型日，不外推全年镜场能量。

## 计算结果

{table}
联合几何损失以各小时未受阴影/遮挡前的余弦入射光能为分母，按历史 DNI 和镜面余弦加权。
这不同于简单平均各面镜、各时刻的面积损失比例。
峰值样本为 **{peak.time_local}**，历史 DNI 为 **{peak.dni_w_m2:.2f} W/m²**，
本模型几何可用光功率为 **{peak.geometric_usable_mw:.2f} MW**。

`P_geom = Σ DNI × 115.7 m² × cos(入射角) × eta_joint`。
这里使用有效反射面积 115.7 m²，而遮挡轮廓为 12.305×9.752 m 的外接矩形。
几何光功率之后继续加入文献场景参数：镜面反射率 **{run['configuration']['mirror_reflectivity']:.2f}**、清洁度 **{run['configuration']['mirror_cleanliness']:.2f}**、
镜面—接收器沿程透过率，以及有限圆柱接收器截获率。接收器吸收率 **{run['configuration']['receiver_absorptivity']:.2f}** 单独列在吸收效率中。
完整光学效率到达接收器表面为止；吸收效率再乘接收器太阳吸收率，净热效率再乘文献热效率场景，均不是发电量。
日光能为逐时功率乘 1 h 的近似和；SARAH 本质为卫星时刻采样，不是连续辐射表积分。

## 余弦与沿程损失分解

{losses}
表中效率是各阶段输出除以其前一阶段输入，损失率则是相邻两阶段的差额除以前一阶段输入。
这些百分比不能直接相加；应沿着光路相乘。下面以 **MWh** 表示同一条顺序能量收支，
各项损失与接收器净热剩余之和等于 DNI×反射面积基准：

{budget}

## 完整光学效率与文献对比

四个日期按 `DNI × 反射面积` 加权得到的本模型平均完整光学效率为 **{model_eff:.2f}%**。
这里的完整光学效率定义为：

```text
eta_optical = eta_cosine × eta_joint × reflectivity × cleanliness
              × eta_atmosphere × eta_intercept
eta_absorbed = eta_optical × receiver_absorptivity
eta_net_thermal = eta_absorbed × receiver_thermal_efficiency
```

文献对比必须看口径：

{comparison}
本项目的四日平均不是全年效率，且公开镜场是航拍/地图提取重建，接收器瞄准和误差参数是文献场景值。
Collado & Guallar 的 58.71% 是年度 TMY 的 campo 场效率；Wang 等的 63.60% 是设计点估算，塔高和布局参数也不同。
因此这里仅作数量级和口径对照，不能据此宣称复现了任一文献结果。

### 为什么没有重复扣除余弦

太阳法线平面上的完整投影面积为 `A_proj = A × cos(theta)`；未遮阴投影面积为 `A_eff_proj`。
原阴影接口输出 `eta_shadow = A_eff_proj / A_proj`，分子分母的投影因子相消。
因此 `DNI × A_eff_proj = DNI × A × cos(theta) × eta_shadow`。
如果直接使用投影有效面积，不能再乘余弦；本项目使用实际镜面面积与面积比例，余弦只乘一次。
联合阴影/遮挡是在同一实际镜面平面上求有效比例，同样不自带余弦损失。
新增余弦损失列只是把原本已计入的 `DNI×A − P_cos` 单独列出，没有再次扣减。

### 沿程模型与口径

本次“反射损失”按用户给出的路径定义解释为**镜面到接收器的大气衰减**，并非镜面材料反射率损失。
对每面镜使用三维中心到其瞄准点的斜距 `d`，不使用地面水平距离。
按 [Noone、Torrilhon 与 Mitsos (2012)，式 11](https://doi.org/10.1016/j.solener.2011.12.007)：

```text
eta_atm = 0.99321 - 0.0001176*d + 1.97e-8*d²    (0 < d ≤ 1000 m)
eta_atm = exp(-0.0001106*d)                      (d > 1000 m)
P_air   = Σ P_geom,i × eta_atm,i
```

该经验模型假设约 **40 km 能见度**，是固定的大气透明度场景，不是 2023 年逐时能见度实测。
历史 DNI 已包含太阳至地面的衰减，新增项仅用于镜面至接收器这段光路。
不把历史 DNI 的变化再转换为额外的大气损失，也不对 DNI 重复应用太阳至地面的大气模型。
经验式零距离截距并非 1，未重归一化；接口拒绝非正距离，分段拟合在 1 km 处存在微小不连续。
本布局斜距范围为 **{validation['distance_range_m'][0]:.2f}–{validation['distance_range_m'][1]:.2f} m**，
各镜沿程损失率约为 **{validation['atmospheric_loss_range_percent'][0]:.2f}%–{validation['atmospheric_loss_range_percent'][1]:.2f}%**。
单镜透过率在固定布局下不随时间变化，但全场损失率按各镜实际光能加权，因此各日略有差异。

### 截获与接收器吸收模型

接收器取半径 **{run['configuration']['receiver_radius_m']:.1f} m**、高度 **{run['configuration']['receiver_height_m']:.1f} m** 的有限圆柱，
中心按 140 m 名义瞄准高度放置。每面镜的反射方向误差用等效圆对称高斯角分布表示：太阳张角
{run['configuration']['sunshape_mrad']:.2f} mrad、镜面斜率误差 {run['configuration']['slope_error_mrad']:.2f} mrad、
跟踪误差 {run['configuration']['tracking_error_mrad']:.2f} mrad；有效标准差按
`σ² = σ_sun² + 2(1+cosθ)σ_slope² + σ_tracking²` 计算。
对圆柱近侧表面进行数值积分，光斑落在圆柱外侧或上下边界之外的部分计为截获损失。
该方法是可复现的等效光斑模型，不是 SolTRACE/UNIZAR 逐光线通量复现；未模拟多点瞄准、镜面分片曲率、
太阳张角的非高斯细节和接收器管束间隙。接收器表面太阳吸收率取 **{run['configuration']['receiver_absorptivity']:.2f}**，
并将文献场景接收器热效率 **{run['configuration']['receiver_thermal_efficiency']:.4f}** 作为吸收光能之后的净热功率比例；
该比例不是温度相关的对流、辐射和导热热平衡模型。

## 结果图

![四季逐时各阶段光功率](seasonal_power.png)

![同一基准下的每日能量收支](loss_budget.png)

![某一已计算时刻的镜面联合面积损失](field_joint_loss.png)

![三维镜场诊断快照](field_3d_snapshot.png)

![目标镜共享投影诊断](projection_diagnostic.png)

![全年历史 DNI 的月度积分估算](monthly_dni.png)

## 已验证与仍有的边界

数据检查：2,650 面镜无重复 ID/坐标，最近镜面中心间距 15 m。备选 FluxSPT 文件实际只有
2,649 行，没有补造第 2,650 面。源文件版本、下载时间、哈希和许可证已保留。
单位测试 109 项通过，包含投影有效面积与余弦因子功率等价、沿程公式参考值、有限接收器截获积分、投影诊断接口和完整能量收支检查；
七个真实布局目标镜与原逐镜接口和取消候选筛选结果的最大绝对差为
{audit['scalar_and_unfiltered_max_absolute_error']:.3g}，低于 1e-8 面积比例容差。
另已核对每个日间时刻的镜数、联合效率顺序、各阶段逐镜功率求和与全场汇总，
以及逐日汇总与逐时结果一致；余弦、阴影/遮挡、大气损失与剩余光能逐镜闭合。

本次采用 Sener 官方公布的实际塔高 **140 m**，平坦镜面中心平面、圆柱近侧中高瞄准和方位角—高度角机构。
名义瞄准高度取 140 m；接收器中心、塔高基准与镜面中心平面之间的精确偏移尚未公开，未编造修正。
上游研究参数表列出的 116 m 未用作真实塔高；公开研究对光学高度与镜面尺寸存在差异；数据说明保留这些冲突，并另算了
[参数敏感性](../geometry_sensitivity.csv)（人工固定 DNI=800 W/m²，不能与历史结果混淆）。
本模型不含实际多点瞄准策略、聚焦、塔身/支架遮挡、镜面缝隙的逐片求交、实际地形、
SolTRACE/UNIZAR 逐光线通量和接收器温度相关的对流/辐射热平衡；已包含有限圆柱截获、
文献误差以及吸收率和热效率场景。基于原始二维布局的东/北解释也未经测量控制点校准。

## 文件与复算

- [逐时全场结果](hourly_summary.csv)、[逐日汇总](daily_summary.csv)、[峰值时刻逐镜结果](peak_snapshot.csv)。
- [全部日间逐镜明细（gzip CSV）](per_mirror.csv.gz)、[结果一致性检查](output_validation.json)、[运行记录](run_manifest.json)。
- [来源及假设说明](../../data/README.md)、[参数配置](../../data/gemasolar_config.json)、[下载及哈希](../../data/source_manifest.json)。
- [数据质量检查](../data_quality.json)、[几何核对](../geometry_audit.json)。

在项目根目录激活 `~/venv` 后，执行 `python -m scripts.prepare_gemasolar`、
`python -m scripts.run_gemasolar`、`python -m scripts.audit_gemasolar`、
`python -m scripts.plot_gemasolar` 和 `python -m scripts.summarize_gemasolar`。
下载步骤另见 README；已有原始缓存时复算无需联网。

## 原始来源

[Sener 官方塔高资料](https://www.group.sener/proyecto/gemasolar/)、
[公开 Gemasolar 布局](https://github.com/LiuZengqiang/CSPHeliostatFieldLayout)、
[HelioCon 电站资料](https://www.heliocon.org/plants/gemasolar_thermosolar.html)、
[Collado/Guallar 参数研究](https://doi.org/10.1016/j.rser.2012.11.076)、
[Collado/Guallar Gemasolar-like 年度场效率](https://doi.org/10.1016/j.solener.2016.06.065)、
[Wang 等 Gemasolar 设计点参数](https://doi.org/10.1002/ese3.280)、
[Sánchez-González 等接收器光学模型](https://doi.org/10.1016/j.solener.2015.12.055)、
[Pérez-Álvarez 等接收器吸收率参数](https://doi.org/10.1016/j.applthermaleng.2022.119097)、
[PVGIS API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)、
[PVGIS 时间戳说明](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/pvgis-5-user-manual_en)。
'''
    (out/'RESULTS.md').write_text(text)
    print(json.dumps(validation,indent=2));print(out/'RESULTS.md')


if __name__=='__main__':main()
