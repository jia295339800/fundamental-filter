# 历史时点检索

用于历史回测和历史时点估值，包括已知答案的开发案例。此工具只修复取证输出边界，不改变估值或准入标准。

通过包内单入口查询，替代直接把 Exa、研报客或网页搜索的摘要打印给研究进程：

```sh
python3 /path/to/fundamental-filter/scripts/entry.py history-search --cutoff 2021-12-31 --provider exa --query '公司 历史报告' --limit 5 --out /path/to/retrieval
```

`--provider yanbaoke` 使用免费搜索API，发送结束日期后仍在本地复核。入口捕获供应端stdout/stderr，只回显候选标题、URL和候选日期；未知或越界日期不回显标题或正文。供应端日期只作为候选线索，不能充当原文版本证明。所有搜索正文均留在取证端，即使供应端声称日期合格，也不直接放行。

`取证端未审返回.txt` 可能含后验答案，不在历史研究进程中打开。需要重放故障时，使用 `--input` 读取已捕获返回并检查过滤结果；不要直接回显原返回。

核对交易所目录、发行人公告或原始出版记录中的发布日期和版本。可变网页缺少历史版本时，继续找原件；不能用原帖旧日期证明今天的全文在当时已存在。建立版本清单，每条包含 `url`、`published_on`、`version_on`、原文件 `sha256`、`date_evidence`，日期用YYYY-MM-DD。SHA256绑定具体原件，`date_evidence`记录日期/版本证据的URL或取证文件位置。该清单是已核事实的凭证，不是把搜索日期抄入后的自动认证。

原件放行：

```sh
python3 /path/to/fundamental-filter/scripts/entry.py history-release --cutoff 2021-12-31 --manifest /path/to/verified-versions.json --url 'https://issuer.example/report' --file /path/to/original.pdf
```

日期或版本缺失、超过截止日、重复身份及哈希不符时不回显正文。PDF使用包内提取器，列错位或扫描页仍须核原页。目标时点为开盘前等日内时刻时，先核同日发布时间；本工具的日截止只能保证日粒度，不证明同日先后。

此边界只保证通过该入口的输出。它不能阻止宿主绕过入口直接调用原始搜索，也不能清除模型既有知识、已发生的泄漏，或从伪造日期凭证中推断真实历史。此类情况应降低实验等级，保留污染记录。
