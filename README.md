# paper-radar

每周检索小目标检测、航拍/遥感和红外/多光谱方向的新论文，合并不同数据源中的重复记录，并生成中文 Markdown 周报。当前配置以 **JIF ≥ 8.0** 的期刊为筛选门槛；纯预印本和会议论文不进入周报。

## 检索主题

arXiv 分类：`cs.CV`、`eess.IV`。以下关键词来自 `config.example.json`；可以在本地 `config.json` 的 `categories`、`topics[].name` 和 `topics[].phrases` 中修改。

| 主题 | 检索关键词 |
| --- | --- |
| 小目标检测 | `small object detection`; `tiny object detection`; `small target detection`; `tiny target detection` |
| 航拍/遥感目标检测 | `aerial object detection`; `UAV object detection`; `drone object detection`; `remote sensing object detection`; `object detection in remote sensing images`; `object detection in aerial images`; `remote sensing detection` |
| 红外/多光谱小目标 | `infrared small target`; `infrared dim target`; `hyperspectral small target` |

## 学术数据源与 API 申请

| 数据源 | 用途 | 申请方式 |
| --- | --- | --- |
| [arXiv API](https://info.arxiv.org/help/api/user-manual.html) | 检索 arXiv 论文及录用信息 | 无需 API key。 |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) | 补充 DOI、期刊和发表信息 | 公共接口无需注册或 key。可选提供联系邮箱以使用 Polite pool。 |
| [OpenAlex API](https://help.openalex.org/api/authentication/) | 检索和核对论文元数据 | 常规检索需配置免费 API key：注册或登录 OpenAlex，然后从 [API key 设置页](https://openalex.org/settings/api)获取。 |
| [Semantic Scholar API](https://www.semanticscholar.org/product/api) | 补充论文和发表信息 | 多数接口可匿名使用；如需 key，在官方 API 页面申请，key 会发到申请邮箱。 |

### 配置 API 凭据

在运行脚本的 PowerShell 窗口设置自己的值。将占位文字替换为实际 key；未申请 Semantic Scholar key 或不使用 Crossref 邮箱时，可删除对应行。

```powershell
$env:OPENALEX_API_KEY = "自己的 OpenAlex key"
$env:SEMANTIC_SCHOLAR_API_KEY = "自己的 Semantic Scholar key"
$env:CROSSREF_MAILTO = "your-email@example.com"
```

`$env:` 设置只对当前 PowerShell 窗口有效。定期任务需要在实际运行任务的 Windows 用户环境或自动化运行环境中设置这些变量。不要把 API key 或个人邮箱写进 README、`config.example.json` 或公开提交。

## 安装与运行

需要 Python 3；脚本只使用标准库，无需安装第三方 Python 包。从仓库根目录运行：

```powershell
Copy-Item config.example.json config.json
python scripts/paper_radar.py --days 7 --no-download
```

首次运行前，可在本地 `config.json` 中调整检索主题、JIF 门槛和输出目录。该文件已被 Git 忽略。

## 输出与参数

- 周报：`reports/`
- 去重状态：`state/seen.json`
- 可下载的开放 PDF：`data/pdfs/`
- `--days N`：回溯 N 天，默认使用配置中的 `days_window`。
- `--max-per-query N`：覆盖单个主题的抓取上限。
- `--config PATH`：指定其他配置文件。
- `--no-save`：生成报告但不更新去重状态。
- `--no-download`：跳过 PDF 下载。

运行数据、PDF、本地配置和 API 凭据不应提交到公开仓库。
