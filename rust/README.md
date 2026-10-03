# Rust 计算内核

`heliostat-core` 是四个平台共享的几何内核。输入是定日镜中心、尺寸、瞄准点、滚转角、安装形式和太阳 ENU 向量；输出为逐镜余弦效率、阴影效率、遮挡效率、联合效率和遮挡贡献镜数量。

实现保持现有计算口径：

- 候选镜先经过保守空间索引和包围球筛选；
- 使用完整四顶点裁剪目标镜前方的有限面积部分；
- 遮挡还要裁剪接收器参考面之后的部分；
- 阴影和遮挡沿各自物理方向投回目标镜实际平面；
- 多边形求并集，重叠损失只扣除一次；
- Rayon 并行计算目标镜，结果顺序保持输入顺序。
- 多塔共用镜可在一次批处理中传入候选塔瞄准点；场内其他镜面姿态保持一致，逐镜比较完整光学链并选择效率更高的塔。

接口包括：

- PyO3 扩展 `_heliostat_rust`，由 `rust_core.py` 封装；
- C ABI，供 Swift 和其他原生平台调用；
- Android JNI，供 `NativeCore.java` 调用；
- 静态库、动态库和普通 Rust 库三种输出。

验证命令：

```bash
cargo fmt --manifest-path rust/Cargo.toml --all -- --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets --all-features -- -D warnings
cargo test --manifest-path rust/Cargo.toml
cargo test --manifest-path rust/Cargo.toml --features android-jni
```

Python 对照测试覆盖随机倾斜镜场、真实 Gemasolar 边界算例以及接收器截止面。设置 `HELIOSTAT_RUST_CORE=0` 可强制使用 Python/Shapely 回退路径。

本机 Apple Silicon 的一次参考测试中，2,650 面 Gemasolar 镜场单时刻几何计算由约 0.893 s 降至 0.020 s，四项效率最大绝对差为 `1.54e-9`。26,944 面双塔参数化场中，5,084 面共用镜分别比较两座塔的分配由约 7.98 s 降至 2.73 s，最终塔归属完全一致。数值会随硬件、场形和遮挡密度变化，这两组数据只用于回归与量级参考。
