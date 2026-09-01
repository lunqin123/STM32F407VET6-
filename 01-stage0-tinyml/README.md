# 阶段 0 · TinyML 关键词唤醒（KWS）

> 目标：用 6 周时间，在 STM32F407VET6 上跑完「训练 → 量化 → 部署 → 性能剖析」的完整闭环。
> 详细操作见 **[`手册-阶段0.md`](手册-阶段0.md)** —— 那份文档才是说明书，本页只是速查表。

---

## 30 秒速查

```powershell
# 进入目录
cd D:\STM32F407VET6\01-stage0-tinyml

# 激活环境
.\venv\Scripts\Activate.ps1

# 1. 环境自检
python 00-check-env.py

# 2. 冒烟测试（不下载数据，2 分钟验证流水线）
python src/train.py --smoke

# 3. 正式训练（首次会自动下载 2.3 GB 数据集）
python src/train.py

# 4. 评估 + 混淆矩阵
python src/evaluate.py --ckpt runs\<运行名>\best.pt

# 5. 模型剖析（参数量 / MACs / 体积）
python src/benchmark.py --ckpt runs\<运行名>\best.pt

# 6. 导出 INT8 TFLite + C 数组
python src/export_tflite.py --ckpt runs\<运行名>\best.pt

# 7. 验证 INT8 准确率（必须做，量化最容易悄悄掉点）
#    带 --compare-ckpt 可以在同一批样本上直接和 FP32 对比，不用手动对数字
python src/evaluate_tflite.py --tflite export\kws_dscnn_int8.tflite --compare-ckpt runs\<运行名>\best.pt
```

---

## 验收标准

| 指标 | 目标 | 检查方式 |
| --- | --- | --- |
| 浮点测试准确率 | **> 95%** | `evaluate.py` |
| INT8 后准确率 | **> 92%** | 量化后重跑评估 |
| INT8 模型体积 | **< 80 KB** | `benchmark.py` |
| 板端推理延迟 | **< 30 ms** | 第 5-6 周在 MCU 上实测 |

---

## 目录结构

```
01-stage0-tinyml/
├── 手册-阶段0.md          ← 详细说明书，第一次来请先读这个
├── README.md              ← 本页
├── requirements.txt       ← 依赖清单（版本已锁定）
├── setup_env.ps1          ← Windows 环境一键搭建
├── 00-check-env.py        ← 环境自检
│
├── src/
│   ├── config.py          ← 所有超参集中在此，调参只改这里
│   ├── model.py           ← DS-CNN 模型定义
│   ├── dataset.py         ← SpeechCommands 数据加载 + MFCC + 增强
│   ├── train.py           ← 训练
│   ├── evaluate.py        ← 评估 + 混淆矩阵 + 误唤醒分析
│   ├── benchmark.py       ← 参数量 / MACs / 体积剖析
│   ├── export_tflite.py   ← 导出 INT8 TFLite + C 头文件
│   └── evaluate_tflite.py ← 验证 INT8 模型准确率
│
├── venv/                  ← Python 虚拟环境（gitignore）
├── data/                  ← 数据集，首次运行自动下载 2.3 GB（gitignore）
├── runs/                  ← 训练产物（gitignore）
└── export/                ← 导出的 .tflite / .h（gitignore）
```

---

## 设计取舍（面试会问，值得记住）

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 网络结构 | DS-CNN | CMSIS-NN 对 depthwise conv 有专用汇编内核，Cortex-M4 上比通用 GEMM 快数倍。省参数是副产品 |
| 输入特征 | MFCC 49×10 | ARM 官方参考实现的取值，两次 stride=2 下采样后尺寸规整，量化友好 |
| 归一化 | per-sample，折叠进模型 | 抵消麦克风增益/音量差异；放进模型可随图一起导出，避免部署时漏实现 |
| 激活函数 | ReLU 而非 ReLU6 | ReLU6 人为截断会压缩有用动态范围，小模型上实测掉点更多 |
| 通道数 | 对齐到 8 的倍数 | CMSIS-NN 的 SIMD 内核按 8 通道一组处理，不对齐会落到慢速路径 |
| 量化方案 | 逐通道 INT8 静态量化 | per-channel 精度更高，CMSIS-NN 支持，需要代表性数据集校准 |
| 导出路线 | Keras 重建 + 权重搬运 + 数值验证 | **自动转换器（ONNX→onnx2tf）出错是静默的**，会产出能跑但精度已崩的模型。手工重建后用 `verify_equivalence()` 把静默错误变成硬失败。详见手册 5.3 |
| 导出依赖 | 只要 `tensorflow-cpu` | 不用 onnx / onnx2tf：其依赖树会拖垮环境（实测解析 41 分钟并损坏虚拟环境）。详见手册 5.3 |

---

## 当前进度（2026-09-01）

| 项 | 状态 | 实测数据 |
| --- | --- | --- |
| 环境搭建 | ✅ | Python 3.13.14 / torch 2.13.0+cpu / torchaudio 2.11.0+cpu |
| 数据流水线 | ✅ | SpeechCommands + MFCC(49×10) + 波形增强 + SpecAugment |
| 模型 | ✅ | DS-CNN，**21,830 参数**，INT8 约 20.2 KB（折叠 BN 后） |
| 冒烟测试 | ✅ | `train.py --smoke` 全流程跑通 |
| 训练脚本 | ✅ | warmup + cosine，早停，曲线，checkpoint |
| 评估 / 剖析 | ✅ | 混淆矩阵 + 业务指标；**0.70 MMACs / 20.2 KB** |
| 量化导出 | 🔧 | 脚本已写完，待安装 tensorflow 后实测 |
| 板端部署 | ⬜ | 第 5 周 |
| 性能剖析报告 | ⬜ | 第 6 周 |

**三个已经踩过的坑（都记录在手册排错表里）：**

1. `src/profile.py` 会遮蔽 Python 标准库的 `profile` 模块，
   导致 torch 在 `import cProfile` 时崩溃。已改名为 `benchmark.py`。
2. `ai-edge-torch` 在 Python 3.13 / Windows 上**所有版本都装不上**
   （依赖锁死了已下架的 TF 开发版）。
3. 装 `onnx2tf` 导致虚拟环境报废：pip 卡在依赖解析 41 分钟，
   然后开始卸载 torch / numpy / matplotlib，卸载完卡死。
   最终 `rm -rf venv` 重建。**本路线不需要 onnx / onnx2tf，只装 tensorflow-cpu。**

> 第 2、3 条合起来导出最终方案：
> **Keras 手工重建 + 权重搬运 + 数值一致性验证**（手册 5.3 有详细说明）。
