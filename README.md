# ANI-RSS Auto Subscribe Agent Skill

这是一个面向 AI Agent 的开源 Skill 项目，用来帮助 Codex、Claude Code 等支持 `SKILL.md` 的 Agent，通过 Mikan 搜索在自托管 [ANI-RSS](https://github.com/wushuo894/ani-rss) 实例里添加番剧订阅。

项目目标不是让脚本或 LLM 替用户“拍板”，而是让脚本稳定地收集 ANI-RSS/Mikan 数据，把候选番剧、字幕组、RSS、样例标题等证据交给 LLM，由 LLM 整理、解释和推荐，最终由用户自己选择字幕组/RSS。

## 功能

- 通过 `POST /api/mikan?text=` 搜索番剧。
- 通过 `POST /api/mikanGroup?url=` 获取 Mikan 字幕组/RSS 候选。
- 输出字幕组、RSS、tags、样例标题等原始证据，让 LLM 判断字幕语言和规格。
- 对样例标题做合集/整季包检测，提前暴露 ANI-RSS 可能无法按单集解析的风险。
- 通过 `POST /api/rssToAni` 把用户确认的 RSS 转成 ANI-RSS 订阅对象。
- 通过 `patch` 命令修改匹配/排除正则、备用 RSS、日期、季度、集数偏移和总集数等 Ani 字段。
- 通过 `tmdb-lookup`、`tmdb-groups` 暴露 TMDB 原始数据，由 LLM 按工作流确认季度和集数对齐，不在脚本里猜测。
- 通过 `preview` 在真正添加前检查修改后的 Ani 对象。
- 只有在显式传入 `--confirm-add` 时，才会通过 `POST /api/addAni` 真正添加订阅。

## 项目结构

```text
ani-rss-auto-subscribe/
  README.md
  SKILL.md
  scripts/
    ani_rss.py
  references/
    ani-rss-api.md
    tmdb-alignment.md
  ani-rss-patch.example.json
  tests/
    test_ani_rss.py
  .gitignore
```

发布到 GitHub 时，仓库名或 Skill 目录名建议使用 `ani-rss-auto-subscribe`，这样能和 `SKILL.md` 里的 `name` 保持一致。

## 配置

使用前需要配置 ANI-RSS 地址和 API Key。脚本不会内置任何 endpoint。

每个 TV 订阅都需要逐集 TMDB 证据，因此必须配置 `TMDB_API_TOKEN`（推荐）或 `TMDB_API_KEY`。TMDB 凭据只放环境变量或 ignored 的本地配置，不要写入示例文件或提交。

推荐方式是在导入/安装 Skill 后复制配置模板：

```bash
cp ani-rss-config.example.json ani-rss-config.local.json
```

Windows PowerShell:

```powershell
Copy-Item ani-rss-config.example.json ani-rss-config.local.json
```

然后编辑 `ani-rss-config.local.json`：

```json
{
  "base_url": "http://your-ani-rss-host:7789",
  "api_key_file": "./ani-rss-key.txt",
  "api_key": "",
  "tmdb_proxy": ""
}
```

推荐把 API Key 放进 `ani-rss-key.txt`，然后在配置里使用 `api_key_file`。如果你只在本机使用，也可以直接把 API Key 写进 `api_key`，但不要提交 `ani-rss-config.local.json`。

`tmdb_proxy` 只代理脚本直接访问 TMDB 的请求，不影响 ANI-RSS 或 Mikan。留空表示直连。使用 HTTP 代理，例如：

- HTTP 示例：`"tmdb_proxy": "http://127.0.0.1:7890"`

代理需要认证时可使用标准 URL 形式，例如 `http://user:password@proxy.example.com:7890`。无需安装额外依赖。

脚本读取配置的优先级：

1. 命令行参数，例如 `--base-url`、`--api-key-file`。
2. 环境变量。
3. `ani-rss-config.local.json`。
4. 当前目录的 `ani-rss-key.txt` fallback。

也可以不用 JSON，直接使用环境变量。

Bash:

```bash
export ANI_RSS_BASE_URL="<your-ani-rss-base-url>"
export ANI_RSS_API_KEY="<your-api-key>"
```

Windows PowerShell:

```powershell
$env:ANI_RSS_BASE_URL = "<your-ani-rss-base-url>"
$env:ANI_RSS_API_KEY = "<your-api-key>"
```

也可以把 API Key 放进本地文件，并通过环境变量指定：

```bash
export ANI_RSS_API_KEY_FILE="./ani-rss-key.txt"
```

如果当前目录存在 `ani-rss-key.txt`，脚本也会把它当作本地 fallback key 文件。`ani-rss-key.txt`、`ani-rss-config.local.json`、`.env` 和本地生成的 JSON 文件已加入 `.gitignore`。

## 推荐流程

这是一个适合聊天驱动的三阶段流程：

```text
搜索 → 展示候选并等待用户选择字幕组/RSS
→ 强制 TMDB 查询 → 修改 → 预览并等待用户确认
→ 添加/修改 → list/get 持久化验证
```

LLM 可以推荐字幕组，但不能替用户选择。展示候选后必须结束当前回复，等待用户明确选择；展示最终预览后也必须结束当前回复，等待用户明确确认。不能在同一回复里继续调用 `build-from-rss` 或 `add`。

先运行只读规划命令：

```bash
python scripts/ani_rss.py plan "租借女友 第五季"
```

`plan` 会返回 JSON，包含：

- 番剧候选数量和候选列表。
- 每个候选的 Mikan URL、是否已存在、评分等信息。
- 字幕组/RSS 候选。
- `language_evidence`：字幕组名、tags、样例标题、样例 subgroup。
- `batch_evidence`：合集、全集、整季包、多集区间标题等检测结果。
- `download_risk`：`normal` 或 `batch-release-detected`，方便 Agent 直接提醒用户。

如果原始搜索 `count = 0`，才进入片名规范化：保留原输入，去掉“订阅/追番”等请求词，拆分明确的季数标记，使用搜索引擎确认简体、繁体、日文、英文、罗马字和简称，再用少量唯一候选重试 Mikan。标题固有数字不能当季数，`Part/第二部/二期/cour` 不能自动当成第二季。必须展示“原输入 → 规范化片名 → 证据”，不能静默替换作品。

如果用户未写季数但唯一结果是第二季或更后面的季度，必须额外询问是否就是该季度；唯一结果不代表用户确认。

Agent 应该把这些候选展示给用户。LLM 可以标出推荐项和理由，但不能把推荐当成选择；必须等待用户明确选择：

1. 选择哪一个番剧候选。
2. 选择哪一个字幕组/RSS 源。

在用户明确点名或确认具体字幕组/RSS 之前，不得运行 `build-from-rss`，不得按某个字幕组生成专属正则，不得添加备用 RSS，也不得调用 `add`。只有用户明确选择后，才能转换成 Ani JSON：

```bash
python scripts/ani_rss.py build-from-rss \
  --rss "https://mikanime.tv/RSS/Bangumi?bangumiId=3945&subgroupid=1231" \
  --type mikan \
  --bgm-url "https://bgm.tv/subject/533027" \
  --subgroup "沸班亚马制作组" > ani-rss-selected.local.json
```

如果需要修改 ANI-RSS 的匹配规则、日期或集数信息，先复制示例 patch，再按证据编辑：

```powershell
Copy-Item ani-rss-patch.example.json ani-rss-patch.local.json
python scripts/ani_rss.py patch `
  --ani-json ani-rss-selected.local.json `
  --patch-json ani-rss-patch.local.json > ani-rss-patched.local.json
```

`patch` 不替 LLM 解释正则，也不自动猜 TMDB 季数。`match`/`exclude` 是过滤规则；`customEpisodeStr` 才是集数提取规则。普通新增规则应使用 `appendMatch` / `appendExclude`，它们会追加到当前规则之后；`match` / `exclude` 才是整组替换。原始正则（如 `简繁日|简日`）匹配所有字幕组；只限定一个字幕组时使用 `{{字幕组名}}:正则`（如 `{{绿茶字幕组}}:简繁日|简日`）。界面里的空字幕组就是原始正则，不能写成 `{{}}:...`。这样会保留 ANI-RSS 已有的默认排除，如 `720[Pp]`、`\d-\d`、`合集`、`特别篇`，并把新增项置于它们之后。全局排除属于 ANI-RSS 实例配置，本脚本不读取或修改它；只有用户明确要求时才应在 Web UI 中单独处理。只有用户明确要求备用 RSS 时，才可写入 `standbyRssList`；每项必须有 `url` 和 `offset`，且备用源必须覆盖同一内容范围。不同编号方式允许存在，但要单独计算 offset；ANI-RSS 全局的备用 RSS 开关也必须开启。

每个新建 TV 订阅都必须先保存 TMDB 身份和季列表证据，再保存选定季的逐集证据：

```powershell
python scripts/ani_rss.py tmdb-lookup --tmdb-id "<id>" `
  --result-file .ani-rss-runs/020-tmdb-lookup.json
python scripts/ani_rss.py tmdb-season --tmdb-id "<id>" --season-number <resolved-tmdb-season> `
  --result-file .ani-rss-runs/021-tmdb-season.json
```

`tmdb-lookup --title` 可以用于初步搜标题，但不能替代 `--tmdb-id` 的直接查询。直接 TMDB 查询要求本地配置 `TMDB_API_TOKEN` 或 `TMDB_API_KEY`。必须先读取 `tmdb-lookup` 返回的 `result.seasons`，再从真实存在的季号中选择 `<resolved-tmdb-season>`；RSS 的 `S3` 不能直接当成 TMDB Season 3。若 RSS 季号不在列表中，不要用 404 试探，改查列表中合理的候选季并按逐集范围对齐；最终 TV `season` 不在该列表时，写入会被拒绝。

这些命令只返回 TMDB 原始证据。LLM 需要先确认标题、年份、类型和季度对应的是同一部作品，再读取 `result.seasons` 选择真实存在的 TMDB 季，判断 RSS 是季度内编号还是连续编号，并确认 TMDB 剧集组使用季内编号还是全局编号。公式是 `目标集数 = RSS解析集数 + offset`：例如 RSS `S3E01` 对应合并单季的 TMDB `S1E25` 时使用 `offset: 24`；如果 TMDB 有独立第四季，RSS `S4E11` 则使用 `season: 4, offset: 0`；只有全局 `E77` 映射到独立 `S4E11` 时才使用 `offset: -66`。必须用 preview 返回的实际解析集数验证至少两个样本；双编号标题如 `[11 - 总第77]` 还需用 `customEpisodeStr` 捕获 `11`。无法确认编号模式、TMDB 剧集组或已有订阅时，必须询问用户。

当标准 TMDB 季度无法解释 RSS 编号时，再额外查询剧集组：

```powershell
python scripts/ani_rss.py tmdb-season --tmdb-id "<id>" --season-number <season-from-lookup>
python scripts/ani_rss.py tmdb-group-details --group-id "<episode-group-id>"
```

`tmdb-groups` 只有摘要时不能用于逐集映射；`tmdb-season`/`tmdb-group-details` 只返回 TMDB 原始集列表，不替 LLM 选季数或计算 offset。

修改后先预览：

```powershell
python scripts/ani_rss.py preview `
  --ani-json ani-rss-patched.local.json `
  --result-file .ani-rss-runs/040-preview.json
```

预览结果会附带 `coverage`、`input_sha256` 和 `confirmation`。`confirmation` 是实际发给 `/previewAni` 的 Ani 对象摘要，包含季度、offset、日期、总集数、TMDB 身份、来源和规则。向用户展示这些输出值，不要从先前推理或 patch 文件重述。必须检查第一、中间、最后三个已解析样本，并核对所有返回项的 offset。少于 3 条已解析集数时只能判定为 `insufficient-samples`。滚动 RSS 可以只说明当前观测范围，不得声称覆盖完整季度。

最终添加订阅：

```powershell
python scripts/ani_rss.py add `
  --ani-json ani-rss-patched.local.json `
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json `
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json `
  --preview-evidence .ani-rss-runs/040-preview.json `
  --confirm-add
```

如果缺少任一证据文件或不传 `--confirm-add`，`add` 命令会拒绝调用 ANI-RSS。确认后必须复用预览时同一个 Ani JSON 和预览证据；不要重新运行 `rssToAni` 或切回原始草稿。哈希不一致时 helper 会拒绝提交。

`add` 收到成功响应后会自动调用 `listAni` 验证持久化。输出包含 `submitted` 摘要和 `verification.field_comparison`，逐项对照实际提交值与后台保存值。仅当 `verification.verified = true` 且所有 `matches = true`（特别是 season、offset、releaseDate、totalEpisodeNumber）时才能报告添加成功。不要从通用 HTTP 工具直接调用 `/api/addAni`/`/api/setAni`。终端回显为空或验证失败时，不得直接重试 `add`，先运行 `get/list` 判断写入是否已经成功。

编辑已有订阅时，不能调用 `add`。先读取完整对象、patch 并 preview，用户确认后再提交：

```powershell
python scripts/ani_rss.py get --id "<existing-ani-id>" > ani-rss-existing.local.json
python scripts/ani_rss.py patch `
  --ani-json ani-rss-existing.local.json `
  --patch-json ani-rss-patch.local.json > ani-rss-patched.local.json
python scripts/ani_rss.py preview `
  --ani-json ani-rss-patched.local.json `
  --result-file .ani-rss-runs/040-preview.json
python scripts/ani_rss.py set `
  --ani-json ani-rss-patched.local.json `
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json `
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json `
  --preview-evidence .ani-rss-runs/040-preview.json `
  --confirm-set
```

`set` 默认不移动文件；只有用户明确要求移动文件时才增加 `--move-files`。写入后 helper 会再次读取并验证。

## 不存在和多匹配场景

脚本会把搜索结果数量交给 Agent 处理：

- `count = 0`：没有找到候选。Agent 应提示用户换一个名称、日文名、英文名或补充季度信息，不能添加订阅。
- `count = 1`：只有一个番剧候选，但仍要让用户确认字幕组/RSS。
- `count > 1`：存在多个番剧候选。Agent 必须让用户确认具体是哪一部，不能静默选择第一个。
- 用户未写季数但结果是第二季或更后季度：必须询问季度是否正确。
- 某个候选 `exists = true`：说明可能已经订阅。Agent 应提示用户确认是否继续。

## 极端情况处理流程

这个 Skill 的原则是：脚本只收集候选和证据，LLM 负责理解语义，用户负责最终确认。

推荐处理顺序：

1. 先运行 `plan`，读取 Mikan 返回的候选、字幕组、RSS 和 `language_evidence`。
2. 只有 `count = 0` 时，才让 LLM 上网确认标题别名、官方译名、日文名、英文名、季度编号、播出年份、TV/剧场版/OVA 类型。
3. 用少量规范化候选重新运行 `plan`，并展示转换依据。
4. 先让用户确认作品和季数，再展示字幕组/RSS 候选。
5. 如果某个字幕组出现 `download_risk = batch-release-detected`，先明确提醒用户这类源可能是合集/整季包，ANI-RSS 后续下载后也可能无法按单集解析。
6. 用户确认具体番剧、季数和字幕组/RSS 后，才能执行 `build-from-rss`。
7. 完成 TMDB 逐集证据、patch 和预览覆盖审计。
8. 用户确认最终对象后才能执行 `add --confirm-add`，并等待添加后的 `list/get` 验证。

需要向用户确认的情况：

- 一个名字可能对应多季、重制版、剧场版、OVA、特别篇或外传。
- Mikan 返回多个看起来都合理的候选。
- 用户给的名称不存在，但联网搜索发现多个可能别名。
- 用户名称可能有错别字，但无法唯一确定正确标题。
- 候选已经 `exists = true`，可能已订阅。
- 字幕组语言/规格无法从 `language_evidence` 明确解释。
- `batch_evidence` 提示这个字幕组喜欢发合集、全集、整季包，或样例标题直接覆盖多集区间。
- RSS 源缺失、过旧、没有样例标题，或样例标题和用户需求不匹配。
- 需要用户偏好：简体、繁体、简繁内封、简日、繁日、分辨率、文件格式、片源平台、字幕组等。
- 提权执行后终端没有回显，或命令退出码/结果文件不明确。

适合让 LLM 上网搜索的情况：

- `plan` 返回 `count = 0`。
- 用户输入的是中文译名、简称、错别字、英文名、日文罗马音，Mikan 不一定能直接搜到。
- 返回多个候选，需要解释每个候选的季数、年份或类型差异。
- 用户说“第五季”“第二部”等，但源站可能使用连续集数或不同季名。
- 字幕组标题里出现不熟悉的缩写，解释这些缩写能帮助用户选择。

联网搜索只用于改善候选和解释，不等于用户确认。任何订阅创建前仍必须回到用户确认。

## 字幕组和语言判断

脚本不会用正则解析字幕语言，也不会输出“这是简体/繁体/内封”的最终结论。原因是字幕组命名不总是规范，硬编码规则容易误导 LLM。

脚本只输出 `language_evidence`：

```json
{
  "group_label": "ANi",
  "tags": ["1080P", "CHT", "AVC", "MP4"],
  "sample_titles": [
    "[ANi] RentaGirlfriend S05 / 出租女友 第五季 - 10 [1080P][Baha][WEB-DL][AAC AVC][CHT][MP4]"
  ],
  "sample_subgroups": []
}
```

LLM 应基于这些证据进行解释，并向用户确认。例如可以说：“这个源标题里多次出现 `CHT`，看起来可能是繁体中文，但请你确认是否选择这个字幕组。”

## 合集检测

有些字幕组会上传合集、全集、季度包，特别是老番补档或 Netflix 一次性放出整季时更常见。ANI-RSS 即使能下载这类文件，后续也可能没法按单集解析。

脚本会额外输出：

```json
{
  "download_risk": "batch-release-detected",
  "batch_evidence": {
    "has_batch_like_samples": true,
    "reasons": ["keyword:合集", "pattern:[xx-yy]:01-12"],
    "matching_sample_titles": [
      "[字幕组] 某动画 合集 [01-12][1080p]"
    ]
  }
}
```

这是启发式检测，不是绝对判断。当前会重点识别：

- 标题里直接出现 `合集`、`全集`、`全季`、`season pack`、`complete season`、`batch`。
- 标题里出现明显的多集区间，例如 `[01-12]`、`E01-12`、`S01E01-E12`、`第01-12话`。

Agent 看到这些信号时，应该在让用户确认字幕组之前先提示风险，而不是等 ANI-RSS 下载失败后再解释。

## Agent 使用建议

Agent 应以 `SKILL.md` 作为主要操作说明。正常流程是：

1. 运行 `plan`。
2. 如果没有候选，向用户索要其他名称。
3. 如果有多个候选，让用户确认番剧。
4. 展示字幕组、语言/规格和 RSS 源；LLM 可以推荐，但等待用户自己确认具体选择。
5. 运行 `build-from-rss`。
6. 运行 `tmdb-lookup`/`tmdb-groups`，先读取直接 TMDB 返回的 `seasons` 列表，再按 `references/tmdb-alignment.md` 比较 RSS 与 TMDB 的季度、集数和日期。
7. 每个 TV 订阅都只对 `seasons` 列表中存在的季号运行 `tmdb-season`；只有标准季度无法解释 RSS 时才运行 `tmdb-group-details`，然后生成最小 patch 和 preview。
8. 向用户展示最终对象并结束当前回复；用户确认后，TV 订阅带 TMDB 身份、TMDB 季度和 preview 三份证据运行 `add --confirm-add`，电影带身份和 preview 两份证据；已有订阅同样使用 `set --confirm-set`，再验证 `list/get`。

命令输出异常时，给命令增加 `--result-file .ani-rss-runs/<run-id>/<step>.json`。结果文件使用步骤编号命名，例如 `010-plan.json`、`040-preview.json`、`050-add.json`，不要把未经清洗的片名放进 Windows 文件名。回显为空时先读取结果文件、stderr 和退出码；写操作永远先验证，不能盲目重试。

## 安全规则

- 不要提交 API Key。
- 不要在聊天或日志里打印 API Key。
- 不要内置 ANI-RSS endpoint。
- 默认只使用 Mikan 流程，不默认调用 BGM 端点。
- 不要主动建议或添加备用 RSS；仅在用户明确要求时才处理。
- 不要在测试中运行 `add --confirm-add`，除非你确实想修改 ANI-RSS 实例。
