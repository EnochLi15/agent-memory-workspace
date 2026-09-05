# 模型连通性与本地向量

2026-09-05 16:25（Asia/Shanghai）复核：所配置网关的模型列表和实际生成请求均返回HTTP 200。详情见[连通性记录](../reports/model-connectivity.json)。认证信息只保存在本地被Git忽略的`.env`，不在交付包中。

`/v1/models`返回以下13个名称：

```text
gpt-5.6
gpt-5.6-sol
gpt-5.6-terra
gpt-5.6-luna
gpt-5.5
gpt-5.4
gpt-5.4-mini
gpt-5.3-codex-spark
codex-auto-review
gpt-5.2
gpt-image-1
gpt-image-1.5
gpt-image-2
```

其中实际测试并用于本次生成、重排和普通Answer的是`gpt-5.4-mini`；最小连通性请求返回`CONNECTED`，耗时1679ms。列表中的其它名称没有逐一做功能测试，不能据名称认定端点权限、能力或底层权重。网关列出的名称中没有专用embedding名称。

本地Ollama端点为`http://127.0.0.1:11434`，已安装`nomic-embed-text:latest`，约274MB，输出768维；固定模型digest为`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`。复核生成3条有效向量耗时88ms，相关文本余弦相似度0.889，无关文本0.354。服务使用实际批量向量接口和查询/文档前缀，不将不同向量空间混写。

另已安装本地`qwen3:14b` Q4_K_M（约9.3GB）用于LoCoMo Judge。它不参与embedding，评测完成后已卸载其内存驻留；模型文件仍在本机。它与未量化的正式Qwen3-14B环境不具有已验证的等价性。

本地embedding已导出为`delivery/local-embedding.tar`，并在独立新模型目录实际导入成功。首次Metal冷启动遇到编译等待，随后同版本CPU回退生成3条768维向量通过；原失败与成功记录都保留。模型导入、增强模式、CPU回退及离线服务启动步骤见[部署说明](DEPLOYMENT.md)。

运行中的增强服务位于`http://127.0.0.1:8088`，容器访问本地模型的路径已实际验证，见[部署复核](../reports/deployed-service-final.json)。模型安装与导入操作无需在每次服务启动时重复执行。
