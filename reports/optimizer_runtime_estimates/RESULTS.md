# 镜场贪心优化时间估算

基准在本机以 PS10 的 624 面参数化场测得；几何计算调用 Rust 内核。每轮假定四个代表时刻、每镜一个候选位置、排名前三个候选执行完整全场复核。时间是规划估计，实际会随镜场密度、接收器、候选格点、CPU 和时刻样本数变化。

- PS10 候选筛选：0.20 s
- PS10 单个时刻完整场计算：0.05 s
- 目录中的双塔场需先加入逐镜跨塔分配优化，当前单塔优化器不会假装支持。

| 电厂 | 镜数 | 30 次移动估计 | 60 次移动估计 | 适用性 |
| --- | ---: | ---: | ---: | --- |
| PS10（西班牙） | 624 | 25 s | 50 s | single-tower ready |
| Crescent Dunes（美国） | 10,347 | 14.8 min | 29.6 min | single-tower ready |
| Khi Solar One（南非） | 4,120 | 4.5 min | 8.9 min | single-tower ready |
| Cerro Dominador（智利） | 10,600 | 15.3 min | 30.5 min | single-tower ready |
| NOOR III（摩洛哥） | 2,650 | 2.5 min | 5.1 min | single-tower ready |
| 首航敦煌一期 10 MW（中国） | 1,525 | 76 s | 2.5 min | single-tower ready |
| 首航敦煌二期 100 MW（中国） | 12,121 | 18.2 min | 36.4 min | single-tower ready |
| 中电建青海共和 50 MW（中国） | 30,016 | 60.8 min | 2.0 h | single-tower ready |
| 鲁能海西 50 MW（中国） | 4,400 | 4.9 min | 9.7 min | single-tower ready |
| 中控德令哈 10 MW（中国） | 22,500 | 41.3 min | 82.7 min | single-tower ready |
| 中控德令哈 50 MW（中国） | 27,135 | 53.1 min | 1.8 h | single-tower ready |
| 中电工程哈密 50 MW（中国） | 14,500 | 23.0 min | 46.1 min | single-tower ready |
| 玉门鑫能 50 MW 二次反射（中国） | 13,015 | 20.0 min | 40.0 min | single-tower ready |
| 三峡恒基能脉瓜州 100 MW 双塔一机（中国） | 26,944 | 52.6 min | 1.8 h | multi-tower assignment required |
