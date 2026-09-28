# paper-radar

每周检索小目标检测、航拍/遥感和红外/多光谱小目标论文，并生成中文周报。默认只收录配置中 JIF 不低于 8.0 的期刊论文及其录用信息，不收录会议和未录用预印本。

数据源包括 arXiv、Crossref、OpenAlex 和 Semantic Scholar。脚本使用 Python 标准库，无需安装第三方 Python 包。API key 和联系邮箱均可通过环境变量提供，不要写入公开配置。

## 使用

```powershell
Copy-Item config.example.json config.json
python scripts/paper_radar.py --days 7
```

首次运行前可在 `config.json` 中调整主题、期刊名单和 JIF 门槛。周报写入 `reports/`，去重状态写入 `state/seen.json`，可下载的开放 PDF 写入 `data/pdfs/`。这些运行数据和 PDF 不应提交到 Git。

可选环境变量：`OPENALEX_API_KEY`、`SEMANTIC_SCHOLAR_API_KEY`、`CROSSREF_MAILTO`。脚本默认会尝试下载开放 PDF；使用 `--no-download` 可跳过下载，使用 `--no-save` 可不更新去重状态。

```powershell
python scripts/paper_radar.py --days 14 --no-download
```

## 命令行参数

- `--days N`：回溯天数，默认使用配置中的 `days_window`。
- `--max-per-query N`：覆盖单个主题的查询上限。
- `--config PATH`：指定配置文件。
- `--no-save`：生成周报但不更新去重状态。
- `--no-download`：不下载开放 PDF。

运行命令请从仓库根目录执行，以便相对路径配置写入仓库内对应目录。
