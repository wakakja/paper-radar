# paper-radar

每周检索小目标检测、航拍/遥感和红外/多光谱方向的新论文，合并不同数据源中的重复记录，并生成中文 Markdown 周报。当前配置以 **JIF ≥ 8.0** 的期刊为筛选门槛；纯预印本和会议论文不进入周报。

> **本次 README 修改标记**
> - `[重写]`：项目用途、快速开始、运行与输出说明。
> - `[新增]`：公开学术 API 的用途、申请入口和本机配置方法。
> - `[当前主题]`：以下检索类别和关键词与 `config.example.json` 同步；可按研究方向修改。
> - `[说明]`：论文检索 API 只用于获取学术元数据，不负责向小红书发帖。

## [当前主题] 检索范围与关键词

arXiv 分类为 `cs.CV`、`eess.IV`。表中主题和关键词按当前配置列出；各来源会按其接口规则检索这些关键词。

| 主题 | 当前关键词 |
| --- | --- |
| 小目标检测 | `small object detection`; `tiny object detection`; `small target detection`; `tiny target detection` |
| 航拍/遥感目标检测 | `aerial object detection`; `UAV object detection`; `drone object detection`; `remote sensing object detection`; `object detection in remote sensing images`; `object detection in aerial images`; `remote sensing detection` |
| 红外/多光谱小目标 | `infrared small target`; `infrared dim target`; `hyperspectral small target` |

修改位置：本地复制生成的 `config.json` 中的 `categories`、`topics[].name` 和 `topics[].phrases`。不要直接改公开仓库里的示例密钥或提交本地配置。

## [新增] 论文数据 API

本项目调用以下学术元数据 API。它们和小红书发布接口是两回事。

| 数据源 | 用途 | 是否需要申请 |
| --- | --- | --- |
| [arXiv API](https://info.arxiv.org/help/api/user-manual.html) | 搜索 arXiv 论文及录用信息 | 无需 API key；请遵守 arXiv 的 API 使用规则。 |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) | 补充和核对 DOI、期刊及发表信息 | 公共接口无需注册或 key。可提供联系邮箱进入 Polite pool。 |
| [OpenAlex API](https://help.openalex.org/api/authentication/) | 检索和核对学术论文元数据 | 常规检索请申请免费 key；免 key 仅适合试用，额度和规则以官方页面为准。 |
| [Semantic Scholar API](https://www.semanticscholar.org/product/api) | 补充论文和发表信息 | 多数接口可匿名使用；建议申请 key，以便使用更稳定的访问额度。 |

### [新增] 自行申请与配置

1. **OpenAlex**：在 [OpenAlex 注册或登录](https://openalex.org/)，打开 [API key 设置页](https://openalex.org/settings/api)复制 key。官方说明免费账户可获得免费 API 额度；额度和使用规则以官方页面为准。
2. **Semantic Scholar**：打开 [Academic Graph API 页面](https://www.semanticscholar.org/product/api)，使用 **Request an API key** 申请；key 会发送到申请邮箱。
3. **Crossref**：不用申请 key。若愿意提供联系邮箱以使用 Polite pool，把自己的邮箱设置到 `CROSSREF_MAILTO`；不设置时仍可走公共接口。
4. **arXiv**：不需要申请 key，也无需填写凭据。
5. 在运行脚本的 PowerShell 窗口设置你自己的值，然后运行检索：

```powershell
$env:OPENALEX_API_KEY = "粘贴自己的 OpenAlex key"
$env:SEMANTIC_SCHOLAR_API_KEY = "粘贴自己的 Semantic Scholar key"
$env:CROSSREF_MAILTO = "your-email@example.com"
python scripts/paper_radar.py --days 7 --no-download
```

请先把示例占位文字替换为自己申请到的值；未设置 Semantic Scholar key 或 Crossref 邮箱时，删掉对应那一行即可。环境变量只对当前 PowerShell 窗口有效。长期运行时，请把变量配置在实际运行任务的 Windows 用户环境或自动化运行环境中。

**不要把真实 key 或个人邮箱写进 README、`config.example.json` 或公开提交。** key 只应保存在自己的运行环境里；若曾提交到公开仓库，请立即在对应服务中撤销或轮换。

## [重写] 快速开始

需要 Python 3；脚本只使用 Python 标准库，无需安装第三方包。从仓库根目录运行：

```powershell
Copy-Item config.example.json config.json
python scripts/paper_radar.py --days 7 --no-download
```

首次运行前可在 `config.json` 中调整主题、JIF 门槛和输出目录。`config.json` 已被 Git 忽略，适合填写本机路径；提交时只保留不含个人信息的 `config.example.json`。

## 输出与常用参数

- 周报：`reports/`
- 去重状态：`state/seen.json`
- 可下载的开放 PDF：`data/pdfs/`
- `--days N`：回溯 N 天；默认使用配置里的 `days_window`。
- `--max-per-query N`：覆盖单个主题的抓取上限。
- `--config PATH`：指定其他配置文件。
- `--no-save`：生成报告但不更新去重状态。
- `--no-download`：跳过 PDF 下载；建议在预览或准备发布内容时使用。

运行数据、PDF、本地配置和 API key 都不应提交到公开仓库。

## [说明] 发布到小红书

当前项目负责论文检索和生成 Markdown 周报，不会自动向小红书发布。学术 API key 只用于查询论文数据。可以先从周报中挑选内容，再编辑成适合小红书读者的图文笔记并由账号本人手动发布；如需自动发布，需要另行确认平台提供的、适用于该账号的官方授权接口。

## 官方 API 说明

- [arXiv API 使用手册](https://info.arxiv.org/help/api/user-manual.html)
- [Crossref REST API 认证与访问](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
- [OpenAlex 认证说明](https://help.openalex.org/api/authentication/)
- [Semantic Scholar API 申请与说明](https://www.semanticscholar.org/product/api)
