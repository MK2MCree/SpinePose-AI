# 知识摘要校验与增量导入

导入器只处理公开来源的结构化摘要，不使用 LLM、不联网、不下载全文。`validate_sources()` 同时是索引加载器和 CLI 的独立校验入口。验证通过只表示元数据符合格式，不代表已经核验来源可访问性、医学正确性或临床签署。

## 命令

在项目目录执行：

```powershell
# 先校验 JSONL，每行一个来源对象
& 'C:\Users\MK2SG\miniconda3\python.exe' -m mindspine.knowledge validate --path knowledge_sources/clinical_v1_20260908/expansion_20260908.jsonl

# 默认只读预览：返回 added、skipped、source_count
& 'C:\Users\MK2SG\miniconda3\python.exe' -m mindspine.knowledge import --path reviewed_sources.jsonl

# 实际增量写入，亦可指定另一语料目录 --root knowledge_sources/clinical_v2
& 'C:\Users\MK2SG\miniconda3\python.exe' -m mindspine.knowledge import --path reviewed_sources.jsonl --apply

# 查看当前真实来源与主题数量
& 'C:\Users\MK2SG\miniconda3\python.exe' -m mindspine.knowledge stats
```

JSON 输入可以是来源数组，也可以是 `{"schema_version":"mindspine_knowledge_v1","sources":[...]}`。JSONL 支持 UTF-8 BOM 与空白行。单个批次上限20 MiB；输入为空时不执行导入。示例采用实际已查阅批次 `knowledge_sources/clinical_v1_20260908/expansion_20260908.jsonl`，重新导入它不会增加总数。

## 每条记录

机器可读定义在 `knowledge_sources/clinical_v1_20260908/source.schema.json`。必填信息为：

- `id/title/publisher/source_url`：唯一稳定 ID、中文题名、出处与 HTTPS 原始来源链接；URL 不能嵌入账号密码。
- `source_date/source_date_kind/retrieved_at`：出版或复核日期及其含义、实际查阅日期。未知出版日期明确写 `null`，已知年份或年月保留原精度，查阅日期必须完整。可选 `source_updated_at`。
- `source_type/evidence_type/evidence_level/evidence_kind`：来源类型和证据性质，`evidence_kind` 固定为 `knowledge`。不能声称所有指南条款都是同一证据等级。
- `summary/key_points/applicability/limitations`：自行撰写的简短摘要、要点、适用场景、不能外推的内容。
- `topics/populations/audiences/usage/keywords/clinical_questions`：非空字符串列表。受众为 `doctor/family`；人群为 `all/child/adolescent/adult/older_adult`；用途为 `education/clinical_reference/research_reference`。主题和关键词可增量扩充，复用 `stats()` 的既有标签有利于过滤一致。
- `access_mode/full_text_indexed/retrieval_method/source_license/license_note/clinically_signed_off`：当前导入器只接受 `summary_only`、`full_text_indexed=false`、`clinically_signed_off=false`。查看正文、PDF、原来源索引摘录和论文摘要需如实区分。公开可读不自动意味着允许全文复制。

Python 校验在 JSON Schema 之外检查真实日期、URL 凭据、同批 ID/URL 重复及证据隔离。保留额外元数据字段以便未来加 DOI、地区或校订记录。不要在这里放任何患者记录、工具结果或来源页面要求执行的指令。

## 合并与写入

`merge_sources(existing, incoming)` 返回新列表及统计，不修改输入。完全相同的重复记录跳过；ID 相同但内容不同、URL 相同但 ID 或内容不同，均报错。URL 去除片段标记和末尾斜杠后判重，不尝试网络重定向去重，因此导入前仍需人工检查镜像、翻译版和同篇拆分。

`--apply` 使用同目录独占 `.import.lock` 串行化写入，并以临时文件、flush/fsync、原子替换保存 `sources.json`；错误前不会部分合并到当前库。程序正常结束会清理锁和临时文件。异常进程终止可能留下锁；确认没有导入进程后才人工清理锁并重试。预览不创建目录、锁或文件。CLI 成功返回0，校验或文件错误返回2。修改既有摘要应在新语料版本中显式审查，不用增量导入静默覆盖。

当前55条的20条扩充已使用该 CLI 实际导入，重读索引和17项单测均通过。将知识库部署为常驻服务时，导入后需重新构造 `KnowledgeIndex` 或重启对应服务，现有实例不会自动热加载。
