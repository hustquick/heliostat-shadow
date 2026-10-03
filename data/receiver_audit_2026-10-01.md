# 全库接收器核查（2026-10-01）

已检查14个目录电厂及Gemasolar基准（含3个布局变体）。published仅表示尺寸有公开出处，不代表模型经过实场校验。占地等面积圆不是竣工边界，模型效率上升不构成实测验证。

|电厂|当前模型直径 × 高度/m|状态与处理|来源|
|---|---|---|---|
|PS10（西班牙）|6.9 × 8.625|实际为腔式接收器；研究资料给出腔体直径14 m、高12 m，不能直接作为外圆柱截获尺寸。当前外圆柱仅为演示近似，总效率不是实场复现。|[资料](https://solarpaces.nlr.gov/project/planta-solar-10-ps10)|
|Crescent Dunes（美国）|15.8 × 35|NREL 表7：直径15.8 m、高35 m。镜位和光学误差仍为重建假设。|[资料](https://www.energy.gov/sites/default/files/2017/04/f34/67464.pdf)|
|Khi Solar One（南非）|12 × 15|实际为多腔蒸汽接收器；未确认各腔开口尺寸与朝向。当前外圆柱仅为演示近似，总效率不是实场复现。|[资料](https://solarpaces.nlr.gov/project/khi-solar-one)|
|Cerro Dominador（智利）|20 × 18.4|NREL 报告引用接收器供应商 John Cockerill 尺寸：直径20 m、高18.4 m。镜位和光学误差仍为重建假设。|[资料](https://www.nrel.gov/docs/fy21osti/79323.pdf)|
|NOOR III（摩洛哥）|20 × 22|接收器制造商 SMARMEC 项目记录：直径20 m、高22 m。镜位和光学误差仍为重建假设。|[资料](https://www.smarmec.com/en/success-stories/solar-thermal-plant-in-morocco/)|
|首航敦煌一期 10 MW（中国）|8.28 × 10.35|已核对首航官方项目资料，未查到可靠的接收器有效吸热面直径/高度；保留估值。|[资料](https://solarpaces.nlr.gov/project/shouhang-dunhuang-phase-i-10-mw-tower)|
|首航敦煌二期 100 MW（中国）|19.2 × 40|蓝皮书2024 第3.2节；英文表中mm为排版错误，采用既有米制来源：直径19.2 m、高40 m。镜位和光学误差仍为重建假设。|[资料](https://www.solarpaces.org/wp-content/uploads/2025/03/Chinas-CSP-blue-book-2024.pdf)|
|中电建青海共和 50 MW（中国）|12.9 × 14.2|蓝皮书2024 第3.5节：直径12.9 m、高14.2 m。镜位和光学误差仍为重建假设。|[资料](https://www.solarpaces.org/wp-content/uploads/2025/03/Chinas-CSP-blue-book-2024.pdf)|
|鲁能海西 50 MW（中国）|11.28 × 14.1|蓝皮书给出147.4 m混凝土塔、40.7 m接收器结构高度及50.8 mm管径；未确认有效吸热面直径/高度与中心标高，不能把管径或总装高度当作有效尺寸。保留估值。|[资料](https://solarpaces.nlr.gov/project/luneng-haixi-50mw-tower)|
|中控德令哈 10 MW（中国）|6 × 8|未查到可靠有效吸热面尺寸；公开资料为两座80 m塔和两种镜面规格，当前单塔统一镜面模型仅为近似。|[资料](https://solarpaces.nlr.gov/project/supcon-delingha-10-mw-tower)|
|中控德令哈 50 MW（中国）|12.14 × 15.03|蓝皮书2024 第3.3节：直径12.14 m、高15.03 m。镜位和光学误差仍为重建假设。|[资料](https://www.solarpaces.org/wp-content/uploads/2025/03/Chinas-CSP-blue-book-2024.pdf)|
|中电工程哈密 50 MW（中国）|14.15 × 15.69|蓝皮书2024 第3.6节：直径14.15 m、高15.69 m。镜位和光学误差仍为重建假设。|[资料](https://www.solarpaces.org/wp-content/uploads/2025/03/Chinas-CSP-blue-book-2024.pdf)|
|玉门鑫能 50 MW 二次反射（中国）|6 × 8|实际为二次反射、地面容积式吸热器；当前塔顶外圆柱是光路占位近似，不能用于实场总效率验证。|[资料](https://solarpaces.nlr.gov/project/yumen-xinneng-xinchen-50mw-beam-down)|
|三峡恒基能脉瓜州 100 MW 双塔一机（中国）|14 × 18|未查到可确认的有效吸热面尺寸；施工报道的总装尺寸不等于吸热面尺寸。两塔各自的R7 m/H18 m保留估值。|[资料](https://eps.ctg.com.cn/cms/channel/1ywgg1/240613245.htm)|
|Gemasolar及3个布局变体|8 × 10.5|保留论文基准；公开资料也有8.1 × 10.6，不静默混用。140 m为名义瞄准标高，精确中心基准仍未验证。|[原模型论文](https://doi.org/10.1016/j.solener.2015.12.055)|

## 高度与类型边界

- 共和：供应商明确标为中心高度210 m，蓝皮书标228 m；保留210 m并记录冲突。
- 鲁能：147.4 m是混凝土塔高，40.7 m是总结构高度，不能直接替换188 m名义瞄准标高。
- PS10、Khi Solar One、玉门鑫能：当前计算内核不具备相应腔式/二次反射接收器模型，只能作几何演示。
- 中控德令哈10 MW实际两塔且镜面混合规格，当前统一模型不等同于实场。
- 其余塔中心基准未取得测量资料的保持原名义值，不按总高推算中心。

德令哈50 MW依据蓝皮书采用1721 m × 1500 m矩形边界，内部六角网格为重建假设。哈密单镜有效面积改为48.5 m²、最远镜距1253.14 m。

逐厂复算详见 receiver_audit_calculations.json，取2023-03-21当地12时附近数据集采样点；三种不支持接收器及德令哈10 MW的结果仅检验计算数值，不是实际电厂效率。
