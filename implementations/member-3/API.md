# 本地接口说明

默认基址 `http://127.0.0.1:3033`。请求和响应使用JSON。错误形式：`{"error":{"code":"...","message":"可理解的中文原因","details":{}}}`。非法输入通常返回400，资源不存在404，跨源写入拒绝403，超大请求413。修改均在单进程内串行完成，并通过SQLite事务持久化。

## 查询与实时数据

| 方法 | 路径 | 返回 |
| --- | --- | --- |
| GET | `/health` | status、version、tick |
| GET | `/api/snapshot` | 全量地图、实体、最近200事件、统计、预约 |
| GET | `/api/stream` | SSE `event: snapshot`；连接及重新连接均发送完整快照 |
| GET | `/api/skus`、`locations`、`inventory`、`ledger`、`orders`、`tasks`、`agvs`、`events` | 对应实体数组；events为完整持久化日志 |
| GET | `/api/events/page` | 完整历史事件筛选分页；`{items,total,offset,limit,types,tick}` |
| GET | `/api/{collection}/{id}` | 单条记录 |
| GET | `/api/stats` | 统计 |
| GET | `/api/maps` | 所有不可变历史地图 |
| GET | `/api/replay` | `{seed,frames}`，每帧含实际车辆/任务/订单/库存/事件和对应地图版本 |
| GET | `/metrics` | Prometheus文本格式基础指标；未部署Prometheus服务 |

组合查询参数：`status`、`priority`、`code`（包含匹配）、`agv_id`、`task_id`、`type`、`from_tick`、`to_tick`、`from_date`、`to_date`。时间窗口两端包含，日期为 `YYYY-MM-DD` 或 UTC ISO8601。只对适用实体字段筛选。示例：`/api/tasks?status=completed&priority=5&agv_id=UUID`。

`/api/events/page`支持上述适用的事件条件，另支持`search`（类型、聚合ID及payload的不区分大小写关键词）、`offset`（默认0，非负整数）、`limit`（默认40，1～200）。items按持久化插入顺序倒序排列；total是筛选后的总数，types是完整日志中的类型集合，tick是查询时实时tick。agv_id/task_id匹配事件聚合ID或payload中的相关ID；时间窗口起点晚于终点、非法分页均返回400。`/api/events`仍返回正序完整数组以兼容原接口。服务端目前全量读取日志再筛选；分页限制网络响应量，不表示数据库已按索引分页。

地图改变时，`map.routes.revalidated`的payload包含`map_version`、`changed_cells`、`replanned_agv_ids`、`preserved_agv_ids`。只重规划受影响路径及其停车依赖，无关任务计划和版本保持不变；随后`map.blocked`或`map.unblocked`记录操作格点。

## 资源接口

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| POST | `/api/skus` | 新增商品 |
| PUT/PATCH | `/api/skus/{id}` | 编辑商品，可带version防止旧写入 |
| DELETE | `/api/skus/{id}` | 无库存/订单引用才可删除 |
| POST | `/api/locations` | 新增库位 |
| PUT/PATCH | `/api/locations/{id}` | 编辑库位并生成新地图版本 |
| POST | `/api/inventory` | 库存登记与流水 |
| POST | `/api/orders` | 草稿出库订单 |
| POST | `/api/orders/{id}/allocate` | 原子预占 |
| POST | `/api/orders/{id}/release` | 自动预占后幂等生成任务 |
| POST | `/api/orders/{id}/cancel` | 仅draft/allocated允许取消并释放预占 |
| POST | `/api/tasks/{id}/cancel` | 仅pending/assigned允许取消 |
| POST | `/api/tasks/{id}/priority` | 更新优先级，正文含priority |

资源写入按表中方法、路径和操作匹配。未知组合在处理正文前拒绝。待派任务的取货面全部被封时，首次进入阻塞状态产生 `task.blocked` 事件，原因码为 `inaccessible_pickup`。

商品字段：`code,name,spec?,batch_rule`；库位字段：`code,zone,x,y,type,capacity`；库存字段：`sku_id,location_id,qty,batch?,expire_at?`；订单字段：`code,requester,priority,lines:[{sku_id,qty}],drop_location_id?`。枚举、字段与UUID语义遵守公共数据契约。

## 统一命令与JSON-RPC

`POST /api/command` 正文 `{action,...参数}`；成功返回 `{result,snapshot}`。前端使用此入口，并共用资源接口的同一业务校验。

| action | 额外参数 |
| --- | --- |
| sku.create/update/delete | 对应资源字段；更新/删除含id |
| location.create/update | 对应资源字段；更新含id |
| inventory.add | 库存字段 |
| order.create/allocate/release/cancel | 创建为订单字段，其他为id |
| task.cancel/priority | id；priority动作还需priority（1～10） |
| sim.start/pause/step | 无；运行中拒绝单步，先暂停 |
| sim.speed | speed：1、4、8 |
| sim.reset | seed：0～4294967295，可省略；会清空当前场景 |
| inject.fault/offline/recover | agv_id |
| inject.block/unblock | 非负整数x,y；拒绝占用格、回岗位、非法节点类型 |
| inject.urgent | 自动创建priority10订单并释放 |
| demo.orders | 自动创建6张样例订单并释放 |
| demo.conflict | 两台已回岗空闲车真实在线竞争同一目标并安全绕行/等待 |
| demo.deadlock | 显式等待图注入，需3台已回岗空闲车；真实车辆回退 |

`task.transition`始终拒绝。坐标、路径、完成事件不能经外部命令任意覆盖，旧计划也没有覆盖当前车辆事实的入口。

`POST /rpc` 使用JSON-RPC 2.0，如 `{"jsonrpc":"2.0","id":1,"method":"sim.step","params":{}}`；method支持上述action，另支持 `snapshot`。返回 `result` 或标准 `error`；无id通知返回204，无响应正文。

## 指标口径

吞吐量=已完成任务数÷已推进tick×3600（任务/仿真小时）；平均任务耗时=completed_tick-created_tick的均值；利用率=有任务或在充电的tick数÷总tick；返岗空驶不计忙碌。瓶颈Top8按进入格点次数排序。取消数仅计cancelled，失败单独保留状态。冲突数计预约失败/显式竞争，非物理碰撞次数。
