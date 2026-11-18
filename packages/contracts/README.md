# 契约目录

M0 在此建立：

```text
contracts/
  openapi/
  events/
  websocket/
  rpc/
  errors/
  examples/
```

规则：

- schema 是接口实现的源。
- 示例必须能通过 schema 校验。
- 事件名称包含版本。
- 删除字段或改变含义需要升版本。
- 新增字段必须在旧消费者中保持可选。
- 契约测试失败时，不能通过放宽所有字段类型规避问题。
