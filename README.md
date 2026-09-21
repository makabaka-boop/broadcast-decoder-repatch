# 直播车解码端口重排服务

解码端口故障后，在保证重要信号优先接入的前提下不牺牲画质。服务为纯后端 API
（Python 3.12 + FastAPI + PostgreSQL），核心指派算法为仓库内自行实现的字典序
最优二分图匹配，并附带一个 C++ 加速核心（同一算法，镜像构建时编译）。

## 目标（字典序最优）

- **基线** `baseline`：依次最大化
  1. 已接入信号的重要度总和；
  2. 已使用兼容边的质量总和。
- **重排** `rearrangement`：工程师引用某条基线并声明故障端口，依次最大化
  1. 已接入信号的重要度总和；
  2. 质量总和；
  3. 相对该基线保留的原配对数。

未故障端口允许换线；每个信号、每个端口在一次结果中至多使用一次；故障端口不
参与重排。完全同值时任选一种，但同一输入的结果是确定的（规范化排序 + 严格
比较），记录读取不会漂移。

### 算法

指派是带权二分图匹配（边可选，非满匹配）。将各层目标编码进单个整数权重，使
通用最大权匹配返回字典序最优解：

- 基线：`重要度 * 10^9 + 质量`
- 重排：`重要度 * 10^12 + 质量 * 1000 + 是否保留旧配对(0/1)`

乘数严格大于所有更低优先级项可能贡献的总和（匹配至多 400 条边）。匹配使用位
势/匈牙利法（O(V³)），把矩形可选匹配通过双侧补零虚拟顶点化为方阵赋值问题：
真实行/真实列为负权重或“不可达”，涉及虚拟顶点的单元代价为 0。生产路径调用
`app/_assignment_native.cpp`（400×400、50000 边及多种最坏结构化图均在 1 秒
内），纯 Python 实现 `_hungarian_py` 语义完全一致，作为无编译环境时的回退。

贪心（例如先把高画质边锁给次重要信号，导致唯一高重要度信号落空）或优先保留旧
配对而降低质量的方案，都会被全局最优纠正。

## 数据约束

快照只接受普通 JSON 类型：

- 信号、端口各不超过 400；兼容边不超过 50000。
- 重要度 ∈ [1, 1000000]，质量 ∈ [0, 1000000]，必须是整数（`true`/`1.0`
  等不接受）。
- id 为非空字符串或整数；信号/端口 id 不得重复。
- 重复边、未知 signal/port 引用、越界、缺字段、多余字段、非法 JSON：
  **整体 422，不产生任何记录**。
- 重排声明的故障端口必须存在、不得重复，否则 **422 且不留记录**。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/health` | 健康检查 |
| POST | `/snapshots` | 保存设备快照，返回 id |
| GET  | `/snapshots/{id}` | 读取快照 |
| POST | `/snapshots/{id}/baselines` | 基于快照计算并保存基线 |
| GET  | `/baselines/{id}` | 读取基线 |
| POST | `/baselines/{id}/rearrangements` | 引用基线 + 故障端口，计算重排并保存 |
| GET  | `/rearrangements/{id}` | 读取重排 |

创建快照：

```json
POST /snapshots
{
  "signals": [{"id": "cam-1", "importance": 100}],
  "ports":   [{"id": "dec-1"}],
  "edges":   [{"signal_id": "cam-1", "port_id": "dec-1", "quality": 80}]
}
```

重排（`failed_ports` 必填，可为空数组表示无故障）：

```json
POST /baselines/{baseline_id}/rearrangements
{ "failed_ports": ["dec-1"] }
```

基线/重排均返回目标值与 `pairs`（`{signal_id, port_id}`）；重排还返回
`preserved_pairs` 与 `changes` 变更清单，每项为：

- `{"signal_id", "from_port", "to_port", "type"}`
- `type` 为 `switched`（换线）、`disconnected`（断开）、`connected`（新接入）。

基线与重排接口在 2 秒内返回（含目标值、配对；重排另含变更清单）。

## 运行

```bash
# 宿主端口由 API_PORT 覆盖，默认 8000
API_PORT=8080 docker compose up --build
```

服务：

- `db`：PostgreSQL 16，保存快照、基线、重排；
- `api`：FastAPI（uvicorn），容器内监听 8000；
- `verify`：**一次性服务**，启动后运行 `pytest -q` 并退出（不使用固定响应或
  占位实现）。

```bash
docker compose build verify api
docker compose run --rm verify     # 运行端到端测试
```

数据库连接由 `DATABASE_URL` 指定，compose 已内置指向 `db`。

## 持久化

三张表：`snapshots`（原始 JSONB 快照）、`baselines`（目标值 + pairs）、
`rearrangements`（目标值、故障端口、pairs、changes）。所有写操作在单事务内
完成；非法请求在任何插入之前被拒绝，因此不会留下半成品记录。

## 本地开发

```bash
pip install -r requirements.txt
# 可选：编译 C++ 加速核心（需要 g++ 与 Python 头文件）
pip install -e .
pytest -q
```
