# ANI-RSS Auto Subscribe Agent Skill

这是一个面向 AI Agent 的开源 Skill 项目，用来帮助 Codex、Claude Code 等支持 `SKILL.md` 的 Agent，通过 Mikan 搜索在自托管 [ANI-RSS](https://github.com/wushuo894/ani-rss) 实例里添加番剧订阅。

项目目标不是让脚本替用户“拍板”，而是让脚本稳定地收集 ANI-RSS/Mikan 数据，把候选番剧、字幕组、RSS、样例标题等证据交给 LLM，由 LLM 判断并向用户确认。

## 功能

- 通过 `POST /api/mikan?text=` 搜索番剧。
- 通过 `POST /api/mikanGroup?url=` 获取 Mikan 字幕组/RSS 候选。
- 输出字幕组、RSS、tags、样例标题等原始证据，让 LLM 判断字幕语言和规格。
- 对样例标题做合集/整季包检测，提前暴露 ANI-RSS 可能无法按单集解析的风险。
- 通过 `POST /api/rssToAni` 把用户确认的 RSS 转成 ANI-RSS 订阅对象。
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
  .gitignore
```

发布到 GitHub 时，仓库名或 Skill 目录名建议使用 `ani-rss-auto-subscribe`，这样能和 `SKILL.md` 里的 `name` 保持一致。

## 配置

使用前需要配置 ANI-RSS 地址和 API Key。脚本不会内置任何 endpoint。

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
  "api_key": ""
}
```

推荐把 API Key 放进 `ani-rss-key.txt`，然后在配置里使用 `api_key_file`。如果你只在本机使用，也可以直接把 API Key 写进 `api_key`，但不要提交 `ani-rss-config.local.json`。

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

Agent 应该把这些候选展示给用户，并要求用户确认：

1. 选择哪一个番剧候选。
2. 选择哪一个字幕组/RSS 源。

用户确认具体 RSS 后，转换成 Ani JSON：

```bash
python scripts/ani_rss.py build-from-rss \
  --rss "https://mikanime.tv/RSS/Bangumi?bangumiId=3945&subgroupid=1231" \
  --type mikan \
  --bgm-url "https://bgm.tv/subject/533027" \
  --subgroup "沸班亚马制作组" > ani-rss-selected.local.json
```

最终添加订阅：

```bash
python scripts/ani_rss.py add --ani-json ani-rss-selected.local.json --confirm-add
```

如果不传 `--confirm-add`，`add` 命令会拒绝调用 ANI-RSS，不会添加订阅。

## 不存在和多匹配场景

脚本会把搜索结果数量交给 Agent 处理：

- `count = 0`：没有找到候选。Agent 应提示用户换一个名称、日文名、英文名或补充季度信息，不能添加订阅。
- `count = 1`：只有一个番剧候选，但仍要让用户确认字幕组/RSS。
- `count > 1`：存在多个番剧候选。Agent 必须让用户确认具体是哪一部，不能静默选择第一个。
- 某个候选 `exists = true`：说明可能已经订阅。Agent 应提示用户确认是否继续。

## 极端情况处理流程

这个 Skill 的原则是：脚本只收集候选和证据，LLM 负责理解语义，用户负责最终确认。

推荐处理顺序：

1. 先运行 `plan`，读取 Mikan 返回的候选、字幕组、RSS 和 `language_evidence`。
2. 如果证据不足，再让 LLM 上网搜索标题别名、官方译名、日文名、英文名、季度编号、播出年份、TV/剧场版/OVA 类型等。
3. 用更好的关键词重新运行 `plan`。
4. 把候选和判断依据展示给用户确认。
5. 如果某个字幕组出现 `download_risk = batch-release-detected`，先明确提醒用户这类源可能是合集/整季包，ANI-RSS 后续下载后也可能无法按单集解析。
6. 用户确认具体番剧和字幕组/RSS 后，才能执行 `build-from-rss` 和 `add --confirm-add`。

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
4. 让用户确认字幕组、语言/规格和 RSS 源。
5. 运行 `build-from-rss`。
6. 在用户确认后运行 `add --confirm-add`。

## 安全规则

- 不要提交 API Key。
- 不要在聊天或日志里打印 API Key。
- 不要内置 ANI-RSS endpoint。
- 默认只使用 Mikan 流程，不默认调用 BGM 端点。
- 不要在测试中运行 `add --confirm-add`，除非你确实想修改 ANI-RSS 实例。
