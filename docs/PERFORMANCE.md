# 性能测量方法

主时延使用独立 eval 的 `scripts/performance.mjs`，通过真实 HTTP 灌入5000个事实、执行16并发/160次检索，并同时轮询 health。它不读取服务数据库。已完成的资源争用测量见 `reports/performance-v6-contended.json`，并行评测结束后还需补充没有其它实验运行时的测量。

主线程事件循环与 Worker 调度诊断使用显式启用的 `service/scripts/performance-preload.mjs`。它只用于基准进程，不被生产镜像导入。使用 Node 的 [Worker preload](https://nodejs.org/api/worker_threads.html#launching-worker-threads-from-preload-scripts) 机制，当前服务的 ESM Worker 已通过双任务排队校准，结果见 `reports/performance-instrumentation-probe.json`。

```sh
cd service
MEMORY_MODE=offline PORT=8086 MEMORY_DATA_DIR=.data/perf-instrumented \
MEMORY_PERF_AUDIT_DIR=../artifacts/perf-instrumented \
node --import ./scripts/performance-preload.mjs dist/server.js
```

待 health ready 后，从另一终端在 eval 目录执行 `PERF_BASE_URL=http://127.0.0.1:8086 node scripts/performance.mjs`。记录分文件保存在指定诊断目录，不记录用户文本或密钥。

`dispatch_wait_ms` 包含消息传输与等待 Worker 回调开始的时间，不能把它当成纯数据库执行时间；`handler_ms` 是同步处理器运行时间。主线程事件循环延迟按1秒窗口记录 p50/p95/p99/max，采样分辨率10ms；不同窗口的分位数不能直接平均后冒充整体分位数。单独报告这次带诊断开销的运行，并以未启用诊断的运行作为主时延数据。
