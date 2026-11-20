# fundamental-filter

A股、港股自主基本面研究与估值 Skill。当前稳定版本 **v0.6.3**。

支持单家公司研究与估值、批量快筛、定向问题研究，以及自主选择研究深度。共用原始资料取证、准入规则、经营分析、经济关系检查、条件估值、现价隐含要求反推和证据复用能力。

本仓库同步已发布的稳定安装：`skills/fundamental-filter/` 内28个文件与原正式版本逐字节一致。本次限定维护会计政策及估计变更的章节边界定位，没有修改准入规则、估值数学或经营预测方法，也没有上传个股研究底稿、历史预测、课程原件、桌面附件或账号凭据。

## 在另一台设备上安装

仓库为私有，安装前应登录有权访问 `jia295339800/fundamental-filter` 的 GitHub 账号。命令行可先执行：

```sh
gh auth login
gh auth setup-git
```

### Codex

把下面这句话发给目标设备上的 Codex：

> 使用 skill-installer，从 GitHub 仓库 jia295339800/fundamental-filter 的 v0.6.3 标签安装 skills/fundamental-filter。它是私有仓库，请使用本机已登录的 GitHub/git 凭据；如已有同名 Skill，先核对版本并保留回退副本。

也可以运行 Codex 自带的安装器：

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo jia295339800/fundamental-filter \
  --path skills/fundamental-filter \
  --ref v0.6.3 \
  --method git
```

安装器默认放到 `${CODEX_HOME:-$HOME/.codex}/skills/fundamental-filter`，同名目录已存在时会停止，不会直接覆盖。安装后在新的对话轮次使用 `$fundamental-filter`；若目标客户端尚未发现新 Skill，重新打开会话。

### 其他支持本地 SKILL.md 的 Agent

克隆本仓库，把整个 `skills/fundamental-filter/` 目录放进目标 Agent 配置的 Skill 目录。`SKILL.md`、`scripts/` 和 `references/` 必须保留在同一目录结构中。

```sh
gh repo clone jia295339800/fundamental-filter
```

也可在 [v0.6.3 Release](https://github.com/jia295339800/fundamental-filter/releases/tag/v0.6.3) 下载原始 `fundamental-filter-v0.6.3.zip`，解压后安装 `fundamental-filter/`。无需复制本仓库的 Git 历史或整个研究项目。

## 环境与功能边界

- 完整自动取证路径目前要求 **macOS、Python 3.9+、curl，以及能导入 PDFKit 的 Swift**。Swift通常由 Xcode Command Line Tools 提供。工具需要访问巨潮、交易所等资料来源。
- 当前自动取证脚本针对11—12月的深圳A股，且要求名录快照与研究日期匹配。其他市场或日期按包内 `references/workflow.md` 的证据复核路径执行，不能宣称已有全市场全日期自动采集覆盖。
- Linux/Windows 上没有原生 PDFKit，Windows 也不能直接使用本版依赖的 `fcntl`。本次没有增加跨平台适配；安装目录成功不等于完整自动取证能运行。Linux可使用兼容的数值计算部分，并按既有路径提供可复核、带原页标记的归档正文。
- Skill 是供宿主 Agent 执行的研究指引与计算工具，不是输入股票代码后无需 Agent 就会独立产出研究的终端程序。宿主仍需具备检索、读取资料、业务判断和报告交付能力。
- 研报客、雪球、iFinD、Longbridge等属于可用时调用的外部工具，不在本包内，不携带任何登录凭据。关键原始证据不足时保留待核。
- 截图主体独立校验是原项目的外部预处理工具，不属于这28文件的v0.6.3核心包。新设备尚未接入该工具时，优先用明确的文字公司名或证券代码发起研究；不能声称核心包已包含这项截图校验。

## 使用示例

```text
使用 $fundamental-filter 分析中宠股份，使用最新可得资料。
重点说明价值驱动、盈利和资本投入、现价需要哪些条件成立，以及是否值得关注。
```

```text
使用 $fundamental-filter，快速筛选这批公司，自主决定补证、深入或停止。
```

```text
使用 $fundamental-filter，只核查某家公司海外工厂的资本需求及回报，不生成无关的完整研报。
```

正式准入和估值交付须使用披露覆盖工作流与 `entry.py formal`。`entry.py check` 检查已声明经济关系；通过不代表预测准确或估值合理。`calculate.py` 保留为历史数值复算入口，不能冒充新的正式准入结论。

## 本地安装冒烟

以下仅验证工具可加载及合成例子的共享检查，不查询真实股票，也不代表完成正式研究：

```sh
python3 -B skills/fundamental-filter/scripts/entry.py --help
python3 -B skills/fundamental-filter/scripts/entry.py check \
  skills/fundamental-filter/references/economic-example.json
```

## 版本与完整性

`v0.6.3` 修复会计政策及估计变更的章节边界，防止把相邻事项勾选归入目标事项；保留跨位置披露矛盾及边界不明时的待核。生产代码只改披露定位器，保留v0.6.2现金数值核验、原准入、经济检查、估值数学及多场景研究。事项性质与期间仍由Agent复核，不宣称支持全部PDF版式、零漏检、预测精度提高或Token成本下降。原v0.6.2标签与Release保留，可用于回退。

原始ZIP SHA256：

```text
af06810f4d02de29adc311d59a2ac993f6fe598babfa90b13abaf60d34eb8d54
```

28文件逐项 SHA256 见 `release-manifest.json`。方法来源见 `skills/fundamental-filter/references/provenance.md`；第三方方法说明保留原出处，本仓库没有重新分发整本书或外部课程材料。
