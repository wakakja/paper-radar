---
name: paper-radar
description: 每周检索小目标检测方向（含航拍/遥感、红外/多光谱）的高影响因子期刊论文，并生成周报。用户提到论文推送、文献周报、查论文、本周新论文、补跑论文、small object detection papers 或 weekly paper digest 时使用。
---

# paper-radar

从 arXiv、Crossref、OpenAlex 和 Semantic Scholar 检索近 N 天的小目标检测、航拍/遥感和红外/多光谱小目标论文。默认按 `config.json` 中的最低 JIF 门槛筛选期刊论文及其录用信息；未录用预印本和会议论文不纳入周报。通过 DOI、arXiv ID 和标准化标题跨源合并记录。

## 运行

从仓库根目录运行：

```bash
python scripts/paper_radar.py --days 7
```

先从 `config.example.json` 复制生成本地 `config.json`，再按需调整主题、期刊名单和输出目录。`config.json` 可包含本机路径，因此已加入 Git 忽略规则，不要提交到仓库。

- `--days N`：设置回溯天数。
- `--max-per-query N`：设置单个主题的抓取上限。
- `--no-save`：生成报告但不更新去重状态。
- `--no-download`：跳过开放 PDF 下载。
- `--config PATH`：指定其他配置文件。

## 输出和凭据

默认周报输出到 `reports/`，去重状态写入 `state/seen.json`，开放 PDF 写入 `data/pdfs/`。这些本机运行产物不应提交。

可选环境变量为 `OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY` 和 `CROSSREF_MAILTO`。不要将密钥写入配置文件、周报或 Git 提交。
