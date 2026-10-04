# 2026-10-04 电厂目录扩充与资料核查

内置入口 15 → 30；新增 15 个，覆盖 11 个国家。参考资料来自 SolarPACES/NLR、HelioCon 和项目业主/技术供应商公开资料。

新增项目统一保留公开场址、时区、容量、镜数等字段；逐镜坐标为参数化模拟，不能称为原厂竣工排布。接收器半径/高度、镜面外框尺寸和误差参数均为显式建模假设。

| 新增项目 | 公开镜数 | 公开塔高或瞄准标高 m | 字段来源 |
|---|---:|---:|---|
| PS20（西班牙） | 1,255 | 165 | [资料](https://solarpaces.nlr.gov/project/planta-solar-20-ps20) |
| Ashalim / Megalim（以色列） | 50,600 | 240 | [资料](https://solarpaces.nlr.gov/project/ashalim-plot-b-megalim) |
| Redstone（南非） | 41,260 | 250 | [资料](https://solarpaces.nlr.gov/project/redstone) |
| Noor Energy 1 / DEWA IV 塔式单元（阿联酋） | 70,000 | 260 | [资料](https://solarpaces.nlr.gov/project/noor-energy-1-dewa-iv-100mw-tower-segment) |
| Jülich（德国） | 2,153 | 60 | [资料](https://solarpaces.nlr.gov/project/julich-solar-tower) |
| 八达岭大汉 1 MW（中国） | 100 | 118 | [资料](https://solarpaces.nlr.gov/project/badaling-dahan-1-mw-tower) |
| Sundrop Farms（澳大利亚） | 23,712 | 127 | [资料](https://solarpaces.nlr.gov/project/sundrop-csp-project) |
| Lake Cargelligo（澳大利亚） | 620 | 未核实；模型 20 | [资料](https://solarpaces.nlr.gov/project/lake-cargelligo) |
| ACME Solar Tower（印度） | 14,280 | 未核实；模型 30 | [资料](https://solarpaces.nlr.gov/project/acme-solar-tower) |
| Solar One 历史试验电站（美国） | 1,818 | 90 | [资料](https://www.heliocon.org/plants/Solar_One.html) |
| Solar Two 历史试验电站（美国） | 1,926 | 未核实；模型 90 | [资料](https://www.heliocon.org/plants/Solar_Two.html) |
| Sandia NSTTF 光热试验场（美国） | 218 | 63 | [资料](https://www.heliocon.org/plants/National_Solar_Thermal_Test_Facility.html) |
| 金塔中光 100 MW（中国） | 25,594 | 220 | [资料](https://www.heliocon.org/plants/Jinta_Zhongguang_Solar_100MW.html) |
| 三峡格尔木乌图美仁 100 MW（中国） | 6,460 | 未核实；模型 210 | [资料](https://solarpaces.nlr.gov/project/ctgr-qinghai-golmud100mw-tower-1000mw-pv) |
| 中电建托克逊 100 MW（中国） | 14,680 | 180 | [资料](https://www.heliocon.org/plants/Power%20_China_Toksun_100MW.html) |

金塔 220 m 是吸热器中心标高，混凝土塔身 195 m，按 [可胜技术](https://www.cosinsolar.com/En/news/detail/id/259.html) 记录；托克逊 180 m 同样是 [供应商](https://www.cosinsolar.com/en/cases/detail/id/10.html) 公布的中心离地高度。

格尔木坐标采用 [三峡集团招标](https://eps.ctg.com.cn/cms/channel/1ywgg1/7147.htm) 场址中心 E93°20.475′、N36°48.219′，不是旧档案的三位小数近似点，也不宣称为塔中心控制点。

Redstone、DEWA 的单镜面积采用21 m²演示估值；Sundrop 和 Solar Two 使用公开总采光面积/镜数计算平均面积。均未冒充公开逐镜外框尺寸。PS20、八达岭、Jülich、Lake Cargelligo 和 NSTTF 的非外圆柱或试验接收器用外圆柱近似，不能用模型效率复现实场性能。

Solar One/Two 为退役研究案例；NSTTF 的5 MW为热功率；Lake Cargelligo的旧档案记录停运，不标为当前在运。部分新项目资料年份为计划年份，未当作真实投运时间。

若羌暂未纳入：旧 [项目档案](https://solarpaces.nlr.gov/project/power-china-ruoqiang-100mw-tower-900mw-pv) 为12,371面48 m²镜；[若羌政府2025年报道](https://www.xjrq.gov.cn/rqxrmzf/c108592/202506/9d0bade3e8154eaf95d8f0bd07d78072.shtml) 已为30万面小镜。缺乏统一几何口径时不直接套用旧参数，也避免在手机上直接加入30万面造成内存压力。

每个目录场址绑定2025年8,760小时 ERA5-Land气温。气温为历史再分析，不是现场测量；DNI延续晴空模拟。历史研究场使用2025年天气仅供同口径比较，不表示2025年实际运行。

资料许可：SolarPACES/NLR项目数据标注CC BY 4.0，保留原始链接；HelioCon条目保留出处；气温沿用Open-Meteo/ERA5-Land来源与许可。
