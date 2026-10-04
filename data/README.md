# Gemasolar 数据来源与建模边界

## 应用内塔式电厂目录

截至 2026-10-04，内置入口从 15 个扩充为 30 个（含 Gemasolar），目录文件包含其余 29 个项目。
新增 15 个项目的字段来源和建模缺口见 [本次数据核查](catalog_expansion_2026-10-04.md)。
公开档案的预计投运年份不作为实际投运年份；Solar One/Two 为历史研究案例，NSTTF 的功率为热功率。
缺少公开塔高时，`tower_height_m` 保留空值，计算单独使用 `model_tower_height_m`，界面标为估值。
所有新增接收器尺寸均明确为演示估值，不冒充已核实实场尺寸；天气环境由电厂统一拥有，原厂参考与设计方案共享。

`power_tower_catalog.json` 收录四端共用的公开塔式光热电厂和研究试验场参数。
国际项目以及首航敦煌一期/二期、中电建青海共和、鲁能海西、中控德令哈 10/50 MW、
中电工程哈密和玉门鑫能等中国项目的场址、容量、塔高、镜数/孔径面积字段来自各记录中
列出的 NLR/SolarPACES 项目页。数据库字段保留原始页面链接。每座电厂分别保存塔高、
接收器半径/高度、定日镜宽度/高度/有效面积、反射率、清洁度和跟踪误差；切换电厂时整套
参数随之切换。公开资料没有给出接收器或镜面外框尺寸的条目，使用逐电厂显式记录且标为
`per-plant-estimate` 的建模值，不再从 Gemasolar 配置静默继承。除 Gemasolar 公开研究重建
布局外，这些项目没有随本仓库提供竣工镜位坐标，不能用作真实电厂镜位或总功率复现。
有公开镜数的项目建立相同镜数的完整参数化场；NOOR III 项目页未列镜数和单镜面积，应用
不反推这两项，其 2,650 面、120 m²/面仅是可视化建模假设。

所有目录电厂现在都保存 `coordinate_datum`、`coordinate_accuracy`、`coordinate_source_name`、
`coordinate_source_url` 和 `coordinate_note`。应用显示六位小数是为了避免格式跳动，不代表
来源具有六位小数的测量精度。`exact_project_site` 表示来源明确把点标为项目精确场址，仍不能
直接解释为塔筒中心测量控制点；`project_site_approximate` 表示只有项目场址级近似位置。

| 电厂 | 当前 WGS 84 场址点（纬度，经度） | 位置口径与来源 |
|---|---:|---|
| Gemasolar | 37.560700，-5.331600 | [Global Energy Observatory](https://globalenergyobservatory.org/geoid/43848) 公开项目场址 |
| PS10 | 37.442260，-6.250060 | [Global Energy Observatory](https://globalenergyobservatory.org/geoid/4942) 公开项目场址 |
| Crescent Dunes | 38.238900，-117.363600 | [Global Energy Monitor](https://www.gem.wiki/Crescent_Dunes_Solar_Energy) 标记 exact |
| Khi Solar One | -28.536400，21.078200 | [Global Energy Monitor](https://www.gem.wiki/Khi_Solar_One_Thermosolar_Plant) 标记 exact |
| Cerro Dominador 光热阶段 | -22.770700，-69.479200 | [Global Energy Monitor](https://www.gem.wiki/Cerro_Dominador_Solar_Complex) 标记 Thermosolar exact |
| NOOR III（第 3 阶段） | 31.062300，-6.870400 | [Global Energy Monitor](https://www.gem.wiki/Noor_Ouarzazate_solar_farm) 标记 Phase 3 exact |
| 首航敦煌一期 | 40.083000，94.434000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/shouhang-dunhuang-phase-i-10-mw-tower) 项目场址近似 |
| 首航敦煌二期 | 40.062000，94.425000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/shouhang-dunhuang-phase-ii-100-mw-tower) 项目场址近似 |
| 中电建青海共和 | 36.102000，100.625000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/power-china-qinghai-gonghe-50mw-tower) 项目场址近似 |
| 鲁能海西 | 36.397000，95.227000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/luneng-haixi-50mw-tower) 项目场址近似 |
| 中控德令哈 10 MW | 37.366000，97.293000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/supcon-delingha-10-mw-tower) 项目场址近似 |
| 中控德令哈 50 MW | 37.366000，97.293000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/supcon-delingha-50-mw-tower) 项目场址近似 |
| 中电工程哈密 | 43.613785，94.956374 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/ceec-hami-50mw-tower) 公布六位小数，但未声明为塔中心测量点 |
| 玉门鑫能二次反射 | 40.338000，97.276000 | [SolarPACES/NLR](https://solarpaces.nlr.gov/project/yumen-xinneng-xinchen-50mw-beam-down) 项目场址近似 |
| 三峡恒基能脉瓜州双塔 | 40.660000，96.160000 | [瓜州县自然资源局许可公示](https://www.cspplaza.com/article-29441-1.html)只给出“双塔水库东北侧约 8 km”；当前坐标为区域级近似值 |

三峡恒基能脉瓜州双塔条目采用[三峡集团采购公告](https://eps.ctg.com.cn/cms/channel/1ywgg1/240613245.htm)公布的 26,944 面定日镜、29.7 m² 四边形镜面、2.5 m 定日镜标高、190 m 双塔中心标高和 6 h 储热参数；[三峡集团项目报道](https://www.ctg.com.cn/ztxw/xsxylxzc/fwnyqgjs/sgdd/2025112111401167936/index.html)给出两塔东西相距约 1 km、两座圆形镜场部分重叠，并说明重叠区镜面上午导向东塔、下午转向西塔。当前重建场在统一六角网格上生成两个相交圆场，重叠区逐时比较两塔方案的余弦、联合阴影/遮挡、沿程透过率与接收器截获率，并选择总光学效率更高的目标塔。逐镜坐标、6.6 m × 4.5 m 外框比例、780 m 边界和接收器半径 7 m × 高 18 m 均为显式建模值，不是竣工数据。

首航敦煌二期单独采用公开的 10.8 m × 10.8 m 定日镜、0.92 反射率、2 mrad 跟踪误差、
直径 19.2 m × 高 40 m 外置圆柱接收器，以及 78 个环向排和最远约 1500 m 的镜场边界。
参数化生成器以 48 面首排生成 78 排，再将最外排约束到 1500 m；镜数与公开目录一致，
但各镜坐标仍不是竣工测量数据。镜数公开口径并不一致：NLR/SolarPACES 项目目录为
12,121 面，SolarPACES 2023 论文为 11,935 面，2024 蓝皮书为约 12,000 面；当前模型采用
目录主源的 12,121 面，并在界面中提示这项差异。

中电建青海共和采用可胜技术项目页和中国光热蓝皮书给出的 30,016 面、20 m²/面、
5.8 m × 3.5 m 外框、2.13 km² 占地和 210 m 吸热器中心高度。NLR/SolarPACES 目录仍列
25,795 面和 51.6 万 m²，属于不同阶段的公开口径。由于未取得竣工镜位，应用用 9 m
六角网格建立环形重建场，并以 2.13 km² 的等面积圆半径 823.4 m 限制边界；该边界修正
避免仅按镜数外推时把最外镜错误放到约 1.95 km。接收器半径和高度仍是明确标注的估值。

## 镜场

来源：[LiuZengqiang/CSPHeliostatFieldLayout](https://github.com/LiuZengqiang/CSPHeliostatFieldLayout)，
固定版本 `34ece6400c2754d3667bcebca90c00446fa39eaf`。下载地址、时间和 SHA-256
完整保存在 `source_manifest.json`；原始文件在 `raw/gemasolar/`，未覆盖或手工修补。
上游仓库为 GPL-3.0，许可证与说明原文随数据保留；本项目对 CSV 的转换记录在代码中。

- `map_layout.csv`：上游说明为从 Google Maps 提取的 Gemasolar 布局，实际 2,650 行。
- `fluxspt_layout.csv`：上游说明来自 FluxSPT，实际 2,649 行；比公开镜数少一面，未补点。
- 计算基线采用 2,650 行版本。它代表真实电站的公开研究重建，不是业主测量或竣工坐标。
- 原 CSV 只有 x/y，没有独立的测量坐标基准说明。按公开研究的东 x、北 y 约定解释，
  其北向更大范围与公开布局一致；没有把这当作已完成测量控制点或整体旋转校准。

计算所需的参数存在来源冲突，因此统一记录在 `gemasolar_config.json`，不隐藏缺口：

| 项目 | 本次基线 | 证据与边界 |
|---|---|---|
| 场址 | 37.560700°N，5.331600°W | [Global Energy Observatory](https://globalenergyobservatory.org/geoid/43848) 公开项目场址；不是塔中心测量控制点 |
| 海拔 | 167 m | 校订场址坐标的 PVGIS DEM 高程；上游工作簿为 168 m |
| 矩形尺寸 | 宽 12.305 m，高 9.752 m | [Collado/Guallar 研究](https://doi.org/10.1016/j.rser.2012.11.076)及相关研究；上游工作簿宽高相反，HelioCon 则列 11.5×10.4 m |
| 有效反射面积 | 115.7 m²/镜 | 同一研究参数；不把约 120 m² 外接矩形全部当成有效镜面 |
| 实际塔高 | **140 m** | [Sener 官方 EPC 项目资料](https://www.group.sener/proyecto/gemasolar/)明确公布，NLR/SolarPACES 亦为 140 m |
| 计算瞄准高度 | 140 m 名义值 | 用实际塔高作为瞄准高度；接收器中心、塔高起算基准与镜面中心平面的精确偏移未公开，未编造修正。116 m 仅保留为文献差异敏感性场景，不作为真实塔高 |
| 接收器 | 半径 4 m、高度 10.5 m 的有限圆柱 | 高度和误差场景参考 Sánchez-González 等研究；实际接收器数据与瞄准策略并未完全公开 |
| 镜面反射率 | 0.88 | Collado & Guallar 2016、Sánchez-González 等 Gemasolar-like 场景；不是现场逐镜测量 |
| 镜面清洁度 | 0.95 | 同上；固定文献场景，不是逐时清洁度监测 |
| 接收器太阳吸收率 | 0.93 | Pérez-Álvarez 等 2022 Gemasolar 接收器参数；不等于热效率 |
| 光斑误差 | 2.51/2.6/2.1 mrad | 太阳张角/斜率误差/跟踪误差，来自 Sánchez-González 等；等效高斯模型 |
| 镜心离地高度 | 未查到可靠公开值 | 模型内部仍用统一 z=0 共同基准，但应用不再把该坐标显示成实际离地高度 |
| 姿态 | 方位角—高度角机构，零附加转角 | 上游工作簿描述该跟踪类型；宽度轴保持水平，不等同于现场机构标定 |

`reports/geometry_sensitivity.csv` 使用两个时刻、固定的人工 DNI=800 W/m²，分别改变
光学高度和外框尺寸，单独量化参数不确定性。它不是历史辐射下的功率结果。

## 历史太阳辐射

来源：[欧盟 JRC PVGIS 5.3 API](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en)。
数据库 `PVGIS-SARAH3`，年份 2023；坐标已随场址校订重新获取。这是卫星模型估计的实际日历年序列，
不是现场辐射表实测，也不是晴空模型或典型气象年。气温来自 API 标示的 ERA5。

- 获取 `trackingtype=2` 双轴跟踪面与 `components=1` 分量：此时 `Gb(i)` 垂直太阳光线，直接作为 DNI。
- `Gd(i)` 与 `Gr(i)` 保留为跟踪面散射/地面反射分量，**不是水平面的 DHI/GHI**。
- 单位 W/m²；原时间 `YYYYMMDD:HHMM` 按 UTC 解析，保留每小时 `:10`，另输出 Europe/Madrid 当地时间。
- 保留 `Int` 重建标记、温度、风速及 API 太阳高度；本次 8,760 行的 `Int` 均为 0。
- SARAH 为小时采样的卫星瞬时辐照度。尽管 JSON 元数据笼统写 hourly averages，
  [官方时间戳说明](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/pvgis-5-user-manual_en)
  明确解释了 SARAH 的瞬时性质。能量由每个样本乘 1 h 近似积分，不视为连续实测积分。
- `usehorizon=1`：包括 PVGIS 地形地平线遮蔽；本地几何程序另算镜间阴影/遮挡，不重复施加地形遮蔽。
- PVGIS 官方手册说明数据免费使用、无需注册；保留 JRC/PVGIS 和 SARAH3 来源说明。

## 已执行检查

`reports/data_quality.json` 包括数量、唯一性、缺失、时间连续性、DNI 非负性、镜间距离与文件哈希。
数据读取器遇到重复时间、缺小时、非法单位、非双轴跟踪数据、空值或负辐照度时直接报错。
没有填补缺失点，没有静默改用晴空 DNI。

本次计算范围：2023-03-21、06-21、09-21、12-21 当地日期的全部小时及全部 2,650 面镜。
四天只是明确选取的季节算例，不代表典型日加权或全年镜场性能。全年只有辐射序列统计。

## 输出的物理含义

`P_geom = DNI × 115.7 × cos(入射角) × eta_joint`，逐镜求和。
阴影和遮挡损失投回同一目标镜面求并集，`eta_joint` 不是两项效率的乘积。
矩形外框用于遮光，多面镜有效反射面积按均匀填充比例处理，未逐片追踪缝隙。
原几何输出未包含光路大气衰减；新增 `post_atmosphere` 输出已乘固定透明度模型。
完整输出继续乘镜面反射率、清洁度、沿程透过率和有限圆柱截获率，得到接收器表面光学效率；
再乘太阳吸收率得到接收器吸收光能。它们仍不含接收器对流/辐射/导热损失、储热和发电效率，
因此不能当作接收器净热功率或电功率。
截获模型不是 SolTRACE/UNIZAR 逐光线通量复现，不含实际多点瞄准、塔身遮阴、支架遮挡、镜面分片曲率、
镜面缝隙逐片求交、太阳张角非高斯细节和地形。真实工况精度仍受上述建模边界约束。

文献对照：Collado & Guallar (2016) 的 Gemasolar-like 年度场效率为 58.71%（ρ=0.88、清洁度=0.95、
RR=4 m、THT=140 m）；Wang 等 (2019) 报告 Gemasolar 设计点场效率估算 63.6%（ρ=0.88、清洁度=0.95）。
本项目报告的四个 2023 季节日是历史 DNI 加权的局部算例，布局重建、接收器模型和时间口径不同，
仅作数量级对照，不宣称复现这些年度或设计点结果。

## 沿程大气透过率

[Noone、Torrilhon 与 Mitsos (2012)](https://doi.org/10.1016/j.solener.2011.12.007)，
Solar Energy 86, 792–803，第 2.6 节式 11。公开全文检索页：
[作者论文全文](https://www.researchgate.net/publication/256328061_Heliostat_Field_Optimization_A_New_Computationally_Efficient_Model_and_Biomimetic_Layout)。

约 40 km 能见度下，`d ≤ 1000 m` 时透过率为 `0.99321 − 0.0001176d + 1.97e-8d²`，
更远时为 `exp(−0.0001106d)`。d 是镜面至其瞄准点的三维斜距。
模型截距并非 1，分段交界也有微小不连续，均保留原拟合，不用于零距离。
该模型并未使用 2023 年当地气溶胶或能见度观测，不能宣称是历史沿程透过率实测。

## 截获与接收器吸收来源

[Sánchez-González、Rodríguez-Sánchez 与 Santana (2016)](https://doi.org/10.1016/j.solener.2015.12.055)
给出 Gemasolar-like 接收器直径 8.5 m、高度 10.5 m，以及太阳张角 2.51 mrad、斜率误差 2.6 mrad、
跟踪误差 2.1 mrad 的场景参数，并明确其光学模型包含阴影、遮挡、余弦、衰减、反射率和截获。
本项目用这些参数建立透明的等效高斯角光斑积分，不声称复刻其 UNIZAR 光斑函数。

[Pérez-Álvarez 等 (2022)](https://doi.org/10.1016/j.applthermaleng.2022.119097)
给出 Gemasolar 接收器管壁太阳吸收率 0.93、热发射率 0.87；本项目当前只将 0.93 用于吸收光能，
不将热发射率直接当作太阳光学损失，也不进行接收器温度相关热辐射计算。

Crescent Dunes 接收器尺寸采用 [NREL/TP-5500-67464 表7](https://www.energy.gov/sites/default/files/2017/04/f34/67464.pdf)：高35 m、直径15.8 m、最远定日镜1620 m。替换了此前按塔高比例估算的14.625 m高、11.7 m直径；重建坐标约束至公开最远距离，仍不是竣工镜位。2023-03-21当地12时重建场最低光学效率约19.85%、平均47.36%；结果依赖估算光学误差和布局，不能作为实测电厂效率。
