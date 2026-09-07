# V1 候选部署与交接

当前仍为候选版本。service `b398a63`、eval `b8ae27a` 已通过干净递归克隆、两种模式部署、重启、备份恢复和兼容镜像回退；7502秒托管运行已有验证。当前候选10题小集及20项存储检查也已通过；37题长背景验收失败，完整评测及最终归档仍需完成。历史归档与旧镜像说明见 [历史部署记录](DEPLOYMENT-HISTORICAL.md)，不能用旧报告证明当前版本已完成。

## 配置与启动

增强配置只有一份：`configs/release-enhanced.env`。离线配置为 `configs/release-offline.env`。Compose 从所选文件读取行为开关，只从本地 `.env` 读取连接地址、凭据和部署参数；旧实验开关不会覆盖发布配置。`service/.env.example` 是增强配置的独立服务模板。

宿主机构建需要 Node 24.18.0、Python 3 和 Docker；运行 service 镜像不依赖 eval。当前容器实测平台为 Docker Linux arm64，宿主机 macOS arm64。没有将 amd64 标为已验证。

```sh
nvm use
make init
make build
# 已有 .env 时保留原文件；首次部署才复制：
cp -n .env.example .env
# 在 .env 填写可从容器访问的 MEMORY_LLM_BASE_URL 和 MEMORY_LLM_API_KEY。
make up
make contract
make down
```

默认构建标签为 `comp-agent-memory-service:v1-candidate`，不是正式版本。增强模式默认使用独立卷 `comp-agent-memory-enhanced-v1`，不会接管历史卷。`MEMORY_PORT` 默认8088；`MEMORY_SERVICE_IMAGE` 和 `MEMORY_VOLUME` 可以指定镜像及已有兼容快照。不要将增强与离线配置指向同一卷。

运行无模型的离线模式：

```sh
make up-offline
make contract
make down
```

离线模式使用独立卷 `comp-agent-memory-offline-v1`、规则提取与词法检索。它支持已验证的简单明确事实、更新和遗忘；复杂语义不等价于增强模式。不能安全绑定的操作应返回错误。不要用离线成绩代表增强模式。`make down` 保留数据，不使用 `down -v` 作为正常停止命令。

## 本地 embedding 与模型

增强写入固定使用 gpt-5.5，默认辅助模型为 gpt-5.4-mini；全部模型阶段共用有界预算。embedding 使用宿主机 Ollama 的 `nomic-embed-text:latest`，768维，固定 digest：

`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`

先在宿主机安装或导入该模型，再启动增强服务。服务不会在请求中下载模型。Docker Desktop 默认使用 `http://host.docker.internal:11434`；其它环境需设置可达的 `DOCKER_EMBEDDING_BASE_URL`。本地 LLM 也必须使用容器可达地址；容器内的127.0.0.1指向容器自身。已有模型、固定归档导入和相关冷启动限制见 [模型配置](MODEL-SETUP.md) 与历史部署记录。

当前增强格式为 `dual-source-v10-s1`，离线为 `dual-source-v2-s1`。`s1` 将擦除事实、操作记录和遗忘标记的范围正文投影为规范化摘要，活动事实的合法范围保留。无 `s1` 的旧库不能由当前版本打开；旧版本也拒绝新格式。摘要用于匹配，不表示加密或磁盘安全擦除。更换 embedding 模型或来源格式需要新数据目录和完整合法操作历史重新灌入（含遗忘指令），不在旧向量空间上混写。模型可用性不由 `/health` 证明：该接口验证进程和存储工作线程，实际模型连通性由完整写入演示验证。

## 可执行闭环演示

`make contract` 是现有独立 HTTP 演示与验收入口：创建独立演示用户，写入经理/门禁码/城市，验证立即检索与幂等，更新城市并验证旧值不再作为当前值，遗忘门禁码并验证经理仍保留，同时检查用户隔离、错误请求、路由白名单和证据条数。

该命令不产生最终问答或判分，也不读取服务数据库。完整写入→检索→回答→判分使用固定小集和独立 eval；存储层遗忘另有只读检查。不要把 HTTP 演示当作全量准确率。

## 日志与故障定位

```sh
docker compose logs --tail 100 memory-service
```

普通 HTTP 日志含 `id`、`request_id`（写入）、哈希租户标识、阶段、模式、状态、耗时和错误码；不记录消息正文、查询、请求头和凭据。健康轮询成功不反复刷日志。Docker日志按10MB、3份轮转。

模型阶段、传输错误、用量与恢复记录位于卷内 `/data/model-audit.jsonl`。仅在明确诊断时开启私有 `MEMORY_MODEL_TRACE`，其中会含模型输入输出，不能放进常规交付包。业务日志不能替代物理磁盘擦除证明。

| 错误 | 处理边界 |
| --- | --- |
| WRITE_CONTINUATION_PENDING | 同进程内使用完全相同ID和载荷有界重试，最多3次；不换ID补成功 |
| EXTRACTION_UNAVAILABLE / VERIFICATION_UNAVAILABLE | 检查模型审计中的连接、超时、输出协议原因；不将失败写入当作完成 |
| EVIDENCE_VALIDATION / OPERATION_* | 来源、语义或操作目标未通过；检查隔离诊断，不能放宽遗忘及邻居检查 |
| SOURCE_FORMAT | 当前配置与数据不兼容；恢复匹配配置/镜像/快照，或使用新卷重新灌入 |
| STORAGE / WORKER_EXIT | 检查卷权限、空间和工作线程；失败事务不能作为已提交记录 |
| DEADLINE | 请求超过截止时间；通过原请求身份核对回执，不盲目换ID |

服务进程重启会失去未提交准备的内存模型响应缓存；这类请求明确终止，不宣称支持跨进程继续。已提交请求的回执和记忆会持久保留。未知数据版本及无版本非空数据在打开租户数据库时拒绝；不会自动迁移或假装兼容。

## 备份、恢复和升级

先正常停止服务，再备份完整数据目录，包括尚未检查点落盘的WAL文件。不要只复制某个正在写入的 `memory.sqlite`。下面以默认增强卷为例，备份目录保存在宿主机；同时记录镜像ID、配置和备份SHA256。

```sh
mkdir -p backups
backup_name="memory-$(date +%Y%m%d-%H%M%S).tar.gz"
docker compose stop
docker run --rm --network none --user 0:0 --entrypoint tar \
  -v comp-agent-memory-enhanced-v1:/data:ro -v "$PWD/backups:/backup" \
  comp-agent-memory-service:v1-candidate -czf "/backup/$backup_name" -C /data .
shasum -a 256 "backups/$backup_name"
docker compose start
```

恢复到新卷，保留原卷作为回退点。为避免覆盖已有数据，先确认目标卷不存在；不要将未检查的归档解压到在线卷。

```sh
restore_volume="memory-restored-$(date +%Y%m%d-%H%M%S)"
docker volume create "$restore_volume"
docker run --rm --network none --user 0:0 --entrypoint tar \
  -v "$restore_volume:/data" -v "$PWD/backups:/backup:ro" \
  comp-agent-memory-service:v1-candidate -xzf "/backup/$backup_name" -C /data .
MEMORY_VOLUME_EXTERNAL=true MEMORY_VOLUME="$restore_volume" docker compose up -d --wait
```

使用与快照相匹配的配置及镜像。离线恢复同时指定 `MEMORY_CONFIG_FILE=configs/release-offline.env`。检查原用户的当前状态、被遗忘值和旧请求回执，再切换正式流量。`make contract` 会创建新演示用户，不能单独证明旧用户恢复正确。

升级前保留旧镜像ID、配置和停机快照；先用新镜像在快照副本上验证，再更换部署。回退时同时恢复旧镜像与兼容快照，不能只换镜像后让旧代码读取未知的新格式。当前版本没有通用自动迁移器，格式变更需要新卷重新灌入。已执行的两种模式备份读回证据分别见 [离线](../reports/v1-offline-backup-restore-b398a63.json) 和 [增强](../reports/v1-enhanced-backup-restore-b398a63.json)。

## 长评测运行

复用 `scripts/run-experiment.py --detach` 启动独立进程会话，`--status` 查询真实进程身份和退出状态。原结果不覆盖、失活记录不当作运行中，未知退出原因保留unknown。macOS的空闲睡眠防护不能保证阻止合盖或强制睡眠。完整评测应放在保持唤醒的宿主机运行。

当前候选的[干净递归克隆验证](../reports/v1-clean-clone-b398a63-acceptance.json) 使用 service `b398a63`、eval `b8ae27a`，镜像为 `comp-agent-memory-service:v1-b398a63`（Linux arm64）。407项服务测试、24项Node评测器测试、17项Python测试、12项运行管理和10项交付工具测试通过。初次评测器测试遇到合盖休眠超时，原始失败日志保留；源码及测试时限未改，在临时防空闲休眠下复核通过。`caffeinate -i` 不能阻止合盖或强制休眠，长评测需要保持宿主机唤醒。

两种模式各12项HTTP契约检查通过，并以同一原用户完成重启、备份恢复及[兼容镜像回退](../reports/v1-compatible-rollback-b398a63.json)。增强模式三次合法写入无需HTTP续跑，未出现降级，分别耗时12.9秒、11.5秒和12.6秒；此结果只覆盖简单部署演示，不能替代长背景验收。临时凭据副本和验证容器已移除，数据卷及停机快照保留。当前发布清单仍为 `draft_pending_gates`，后续按 [V1交付计划](29-V1交付收敛计划.md) 验收。

## V1 发布验收与打包

本版使用 [发布清单](../configs/v1-release.json) 指定源代码、验收证据、完整评测和运行归档。清单中的未来报告路径与计划运行ID不表示任务已完成。以下命令只有在对应证据真实完成后才能通过；不要用历史 `delivery-readiness-audit.json` 为本版背书。

```sh
python3 scripts/audit-delivery-readiness.py \
  --release configs/v1-release.json --output reports/v1-delivery-readiness.json
```

提交源代码与报告后运行 `python3 scripts/bundle.py`，记录它输出的新快照目录。将该目录填入下方 `snapshot_dir`；`bundle.py` 本身也会验证相邻 bare 仓库的递归克隆。输出归档路径必须是未使用的新路径。

```sh
snapshot_dir="delivery/实际的新快照目录"
python3 scripts/package-delivery.py --snapshot "$snapshot_dir" \
  --release configs/v1-release.json --readiness reports/v1-delivery-readiness.json \
  --output delivery/agent-memory-v0.1.0.tar.gz
python3 scripts/verify-delivery.py --archive delivery/agent-memory-v0.1.0.tar.gz \
  --output delivery/v1-archive-verification.json
```

V1打包只选择本次验收绑定的文件，保留凭据及Git可达历史扫描，排除运行数据库、私有模型原文追踪和无关实验目录。独立验证会实际读取归档、检查逐文件哈希，并从包内递归克隆三个仓库。归档验证不替代部署与功能测试：还需按包内说明加载镜像，并在独立卷启动后执行HTTP契约及旧用户恢复检查。正式交接需要这两类证据同时通过。

旧版脚本不带 `--release` 的调用仅用于复核历史交付，仍保留旧实验矩阵要求，不属于本版发布路径。


## 当前可接手的候选源码快照

`delivery/v1-b398a63/candidate-source-832ec2d.tar.gz` 是非正式V1的源码交接快照，固定根仓库`832ec2d`、service `b398a63`及eval `b8ae27a`。外部同名`.sha256`文件用于核对归档，包内`SOURCE-INVENTORY.json`逐文件绑定内容。已实际读回3381个文件、从归档递归克隆并检查三个仓库Git完整性；配置凭据与可达源码历史扫描通过。详见[源码交接验证](../reports/v1-candidate-source-handoff.json)。该验证报告是在归档完成后生成，因此不包含在上述固定快照中。

解压后按包内`CANDIDATE-README.md`操作。快照包含三个bare仓库；运行镜像和本地embedding归档单独提供，身份见[运行归档清单](../reports/v1-runtime-bundles.json)。源码离线克隆不等于依赖离线安装，运行镜像平台仍仅Linux arm64。该快照不替代正式打包/readiness门槛，也不证明长背景、全量或最终包部署通过。


## BigModel 候选接入（2026-09-07）

用户提供的新接口已完成最小连通性检查。当前本地`.env`选择`configs/v1-bigmodel-enhanced.env`，base为`https://open.bigmodel.cn/api/coding/paas/v4`；写入/核验/修复使用`glm-5.3`，辅助及独立评测Answer/Judge使用`glm-5.3-flash`，本地nomic embedding保持原digest。新机器需自行填写密钥并显式设置`MEMORY_CONFIG_FILE=configs/v1-bigmodel-enhanced.env`。Responses接口测试通过，服务仍沿用Chat Completion。此前正在运行的服务未自动重启或切换。

采用官方文档中的`json_object`模式，保留本地严格结构与语义校验。首次`json_schema`小集因非法JSON导致一次遗忘写入失败，结果独立保留。配置调整后的第二轮两边各5次写入、10次检索均成功，候选20项存储检查通过；机器计分候选8/10（9题已判、1题judge_error），原文5/10。判分解析失败的原始响应未被现有评测器保留，不能确定其具体尾随字符或把它直接归因于流式解析实现。没有补判或覆盖旧分数。助手复核不是人工校准，短参考造成的判分争议不计入修正成绩。

对应[兼容性结果](../reports/v1-bigmodel-compatibility.json)、[首轮失败](../reports/v1-small-bigmodel-01-results.json)、[第二轮结果](../reports/v1-small-bigmodel-02-results.json)。这些是新的候选证据，不替代旧发布清单中的提交/模型身份；完整小集判分、长背景、全量与正式发布仍未通过。按用户要求收敛，本轮后未启动长背景或全量测试。


最新用户调整：恢复有效的BigModel凭据（仅本地保存），所有远程阶段统一`glm-5.2`，仍用Chat Completion与`json_object`。一次真实流式JSON调用通过。当前100题候选运行使用`configs/v1-public-100-glm52-run.json`及`configs/v1-public-100-glm52.json`，不再安排1000题，也不额外跑100题原文基线。此前GLM-5.3/Flash的分数不用于证明GLM-5.2已通过验收。LoCoMo保留现有refined-python评分实现、模型改为GLM-5.2；MemOps保留现有rubric评分实现。两者均为明确标注模型的公开代理评测。
