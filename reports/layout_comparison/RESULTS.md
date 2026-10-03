# Gemasolar 排布方式文献复现与同口径比较

本阶段复现 Collado–Guallar Campo 三分区径向交错方法。它先生成 3,864 面候选镜，
再按光学表现裁剪为 2,650 面。本实现严格采用文献给出的 DM、Nhel1=46、
Dr1=cos(30°)、分区镜数翻倍和半节距交错规则。由于 HFLCAL 的原始逐镜裁剪程序未公开，
候选裁剪使用明确记录的 20 个季节/时刻的平均余弦效率×大气透射率代理；因此这是
“文献排布规则复现 + 本项目统一裁剪与光学评价”，不是作者 Campo 数值结果的逐点复制。

## 方案

- Gemasolar 重建坐标：公开地图提取的 2,650 面镜，用作实际排布参考。
- Campo-2013 旧参数：Dr2=1.4、Dr3=2.0。
- Campo-2016 改进参数：Dr2=1.0、Dr3=2.4；属于文献扫描并推荐的更密 zone 2 / 更疏 zone 3 组合。

## 同口径结果

| layout         | sample_count | mean_cosine_efficiency | mean_joint_efficiency | mean_optical_efficiency | mean_receiver_incident_mw | mirror_count | radius_min_m | radius_median_m | radius_max_m | north_fraction | convex_bbox_area_ha |
| -------------- | ------------ | ---------------------- | --------------------- | ----------------------- | ------------------------- | ------------ | ------------ | --------------- | ------------ | -------------- | ------------------- |
| Gemasolar重建坐标  | 4.00000      | 0.80364                | 0.98893               | 0.49580                 | 128.39950                 | 2650.00000   | 80.99945     | 409.99964       | 857.00057    | 0.60151        | 202.86566           |
| Campo-2013旧参数  | 4.00000      | 0.82499                | 0.98744               | 0.45441                 | 117.64213                 | 2650.00000   | 114.94742    | 495.88256       | 907.24303    | 0.72906        | 216.63818           |
| Campo-2016改进参数 | 4.00000      | 0.82983                | 0.97623               | 0.48393                 | 125.32495                 | 2650.00000   | 114.94742    | 414.23850       | 875.84147    | 0.72906        | 199.40428           |

比较采用 2023 年春分、夏至、秋分、冬至各自历史 DNI 最大的一个小时。所有方案共用
140 m 瞄准高度、同一镜面/接收器参数和完整损失链。四点平均只用于快速、可复核的阶段比较，
不代表文献的 TMY 年效率，也不代表全年最优结论。

## 文献

1. Collado & Guallar (2012), *Campo: Generation of regular heliostat fields*, DOI: 10.1016/j.renene.2012.03.011.
2. Collado & Guallar (2013), *A review of optimized design layouts for solar power tower plants with campo code*, DOI: 10.1016/j.rser.2012.11.019.
3. Collado & Guallar (2016), *Two-stages optimised design of the collector field of solar power tower plants*, DOI: 10.1016/j.solener.2016.06.065.
