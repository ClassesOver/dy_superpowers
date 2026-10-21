# 遥测服务位置

Jaeger/OTLP collector 与插件代码目录隔离，但由同一个 `dy_superpowers` Git 项目统一管理。请在本目录启动服务；插件内 MCP 只作为 OTLP 客户端连接 collector。

```bash
docker compose -f compose.yml up -d
```

外部端口：Jaeger UI `33047`，OTLP/gRPC `33048`，OTLP/HTTP `33049`。容器内部仍使用 `16686`、`4317`、`4318`。MCP 默认向 `http://127.0.0.1:33049/v1/traces` 导出 OTLP/HTTP trace。

可在启动插件前设置 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 覆盖该地址；不要把远程 collector 的令牌或密码提交到仓库。
