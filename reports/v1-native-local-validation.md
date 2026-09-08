# 无 Docker 本地运行验证

日期：2026-09-07。验证环境：macOS arm64、Node 24.18.0。Linux 原生命令路径已提供，本次未做 Linux 实机验证。

改动：将镜像构建分离为 `make docker-build`；`make build` 只编译源码。增加工作区 `make local`、`make local-dev`、`make local-debug`，固定模式配置、私有连接加载和独立数据目录。service 自身提供 `npm start/dev/debug`，无 eval 运行时依赖。开发脚本先停止服务再编译全部源码与 Worker，失败时保持停止，后续修正自动恢复；调试使用本机 inspector 和已有 TypeScript source maps。

验证结果：

- 全部 `make test` 通过：service 430、eval Node 24、eval Python 17、实验脚本 Python 14、交付门禁 Python 10、本地配置 Node 3，共498项。
- PATH 中放置一调用即失败的 Docker 占位命令，原生构建和 HTTP contract 仍通过。
- HTTP contract 验证写入、检索、幂等、租户隔离、更新、遗忘、邻居保留及接口约束。
- 独立写入后停止并重启进程，同一数据目录仍可检索已提交事实。
- debug 模式 inspector 可发现目标；修改源码触发重新编译与服务重启。
- 注入临时 TypeScript 类型错误后服务停止；修正后恢复健康。临时源码及生成文件均已清理。
- SIGTERM 停止启动器及其服务后 HTTP 端口关闭。全部测试进程已清理。
- 原生增强模式亦成功启动并返回健康状态；未执行远端模型请求，健康检查不代表完整增强写入通过。

原生进程验证使用临时数据目录，没有操作历史评测数据库；没有调用远端模型、追加问答评测或更改已有分数。核心业务代码及判定规则未修改。增强模式的端到端模型质量仍沿用原有未完成边界，本报告不替代该验收。

入口及配置规则见 `docs/LOCAL-DEVELOPMENT.md`。本机详细测试输出保存在 `/tmp/agent-memory-native-tests.log`；临时运行记录在 `/var/folders/4d/d9w3x_8d4fn9vkp6dnqg9bqr0000gn/T/memory-native-zlpt2vhh`，这些路径仅为本次本机记录，不属于交付运行依赖。
