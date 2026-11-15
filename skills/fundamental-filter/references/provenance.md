# 来源说明

包内规则采用项目已确认的阶段 1.1 v1.6 与阶段 1.2–1.4 v1.0，脚本由本项目既有试跑实现移植。政策文本中的历史版本说明留作出处，执行以包内政策和本次源快照为准。

| 来源 | 包内采用的思路 |
| --- | --- |
| [财报排雷规则](https://github.com/terancejiang/financial-report-minesweeper/blob/483d3fcd65da32c2de2898ab6f9529ae87e92353/skill/references/checklist-rules.md) | 三表对照、现金兑现、应收与营收、存货周转、现金和借债的核查；包内独立实现，按项目规则定性 |
| [龟龟 5.2.2](https://github.com/wsadneal-debug/turtle_project/tree/f8c194992680035859f642e917ac791f9c1b94a2/skill) | 六年归属现金回收、负现金及安全垫 |
| 格林沃尔特三步价值桌面包 | EPV 的经营盈余和归母权益桥、成熟业务维护投入 |
| 桌面烟蒂股分析与报告审查 | 可兑现价值、资金烧损、兑现路径和底稿重算 |
| 桌面财报工具与龟龟旧框架 | 官方 PDF、报告主体和期间核验、附注定位 |
| [知行录](https://github.com/firyrice/zhixinglu/blob/acdaca92025d1c8d9a2c4428fbae2af50f67e79c/app/ai/valuation_models.py)与[TradingAgents](https://github.com/TauricResearch/TradingAgents/tree/9fa1196fffbcfd9de5146d2dd2869df84ab99838) | 现价反推、时点完整性、反证与保存恢复 |

| 价投小猪仔《投资课全集》，用户提供 PDF | [估值校准](valuation-calibration.md)所列页码。采用现金转化、分阶段建模、跨周期检查与终值检验，自行实现 FCFF 与股权桥 |

来源仅为研究溯源。程序、政策、复核和估值接口均包含在本包。

v0.5.2的经济关系依据Damodaran在NYU的[DCF课程](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/eqlect.htm)和[官方案例](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/seminarvals.htm)独立实现：经营与现金衔接、增长/再投资/增量回报、稳定期和价格条件反推。Toyota、Amazon、Amgen用于学习复算，未证明预测准确度提升。包内不依赖课程文件或桌面Skill，不携带历史美国参数作为默认值；源材料与学习核对保存在项目经验目录。
