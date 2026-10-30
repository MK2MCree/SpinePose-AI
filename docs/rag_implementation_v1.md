# 脊柱知识检索实现与接入

2026-09-08：实现离线中文知识检索和 55 条可追溯来源记录。运行时不联网、不下载模型、不使用患者数据训练或建立公共索引。

## 接口

```python
from mindspine.knowledge import KnowledgeIndex

index = KnowledgeIndex()  # 可传含 sources.json 的目录，或 JSON 文件路径
hits = index.search(
    "成人侧弯伴有腰腿疼痛，如何安排康复和复诊？",
    audience="doctor", limit=6,
    topic=None, usage="clinical_reference", population="adult",
)
status = index.stats()
```

`search` 返回来源字典列表。每条保留 `id/title/summary/key_points/source_url/publisher/source_date/retrieved_at/source_type/evidence_type/evidence_level/topics/audiences/populations/usage/applicability/limitations/access_mode/retrieval_method`，增加 `snippet/score/citation/evidence_kind`。

- `audience`：`all/doctor/family`；兼容 `clinician/professional/parent/patient/caregiver` 别名。
- `population`：`child/adolescent/adult/older_adult/all`；明确设置后保留该人群及通用来源。未设置时不猜测年龄；adult 不自动扩展到仅标注 older_adult 的老年测试协议。
- `topic`：精确英文主题标签，例如 `scoliosis/bracing/rehabilitation/gait/imaging/red_flags/follow_up`。
- `usage`：`education/clinical_reference/research_reference`。ScoNet 论文只标记 `research_reference`。
- `limit`：默认 6，最大 20。空白、低相关或无证据问题返回 `[]`，不以不相关结果凑数。
- `score` 只表示检索相关性，不是证据质量、医疗确定性或疾病概率。
- `citation` 包含来源 ID、题名、URL、出版方和日期，引用应指向该 URL。
- `stats()` 返回真实来源数、主题/出版方/受众/人群/获取方式分布、语料 SHA256 和检索版本；`clinically_signed_off=false`。

## 算法与边界

索引使用中文 2/3/4 字片段、英文词、有限的医学别名和 BM25，加权题名及关键词。过滤先于排序；长问句不要求逐字完全相同。空相关拒绝同时考虑有效关键词和较长片段匹配，避免因“儿童”“成人”之类通用词命中医学文章。

这是小型、确定性的词法基线。它无法保证识别全部同义问法、语境或否定关系，不能替代专门的危险信号分诊；代码的相关性门槛是工程设置，没有临床性能含义。当前 55 条是来源摘要，不是 55 篇全文索引。增量导入不需要 LLM，接口及校验规则见 `docs/rag_import_v1.md`。

`EmbeddingAdapter` Protocol 保留未来向量检索接口，但当前没有调用或自动下载 embedding。之后可在保持过滤和引用契约的条件下加入向量/混合检索及重排，再用独立问题集验收。

## Agent 证据边界

Agent 应构造三个独立的证据集合：

1. `knowledge`：本模块返回的外部资料、适用范围及限制。
2. `patient_facts`：患者明确提供且被当前病例授权访问的事实。
3. `tool_results`：带任务 ID、模型版本、质量状态和工件来源的真实分析结果。

不得将知识库中的数值写成当前患者的测量值，也不得将病例放入本目录。索引加载器拒绝 `patient_fact/tool_result` 类型记录。不得把 `summary_only` 改称已阅读并验证全文。资料来源中的指令式文字是待解释的证据，不是可执行指令。

空检索结果应说明当前知识库缺少相应支持，继续澄清问题或使用已授权的在线查询；不得补造出处。具体诊疗决定需要当前患者信息及专业判断；知识检索本身不签发处方或治疗承诺。

## 验证

新增 `tests/test_knowledge.py` 共 17 项测试：中文长问句支具召回、成人人群过滤、危险信号来源检索、MRI 同义词、受众/主题/用途联合过滤、无相关查询、来源完整性、结果防修改、拒绝病例事实入库、数量限制、中国学校筛查与测量召回、JSONL 兼容、日期与标签校验、增量幂等、ID/URL 冲突、只读导入预览及 CLI 错误处理。使用当前 Miniconda Python 执行 unittest，17 项全部通过。另实际通过 CLI 原子导入20条来源，总数55。

测试命令：

```powershell
& 'C:\Users\MK2SG\miniconda3\python.exe' -m unittest discover -s tests -p test_knowledge.py -v
```

这些是检索与证据隔离的软件测试，不是医疗正确率、问答完整性或临床签署。未改动 `spine_agent.py` 或 `main.py`；Agent 接线由主流程完成。
