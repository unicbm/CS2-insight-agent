# 完美世界社区 Demo 兼容性、共享解析链路与 demoparser pawn handle 缺陷

> 状态：已在 CS2 Insight Agent 应用层实现兼容性保护；完整 pawn 轨迹恢复仍需要修复 `demoparser2`。
>
> 本文记录 2026-07-15 对 FACEIT 与完美世界社区平台 Demo 的真实样本验证、性能基准、高置信根因分析和后续修复建议。

---

## 1. 摘要

本轮工作解决了两个相互关联、但必须分层处理的问题：

1. **多玩家分析重复扫描同一 Demo**：旧链路按玩家重复调用原生解析器。现在先把击杀、回合、经济、空间采样和公共索引物化成共享中间表示，再由每个玩家执行轻量过滤与聚合。
2. **部分完美世界 Demo 的玩家字段缺失**：应用层已经可以从 `CCSPlayerController.m_iTeamNum` 无额外扫描地恢复队伍身份，从而修复 10 人名单、5v5 分组、回合、比分和经济链路。

10 人全量分析的顶层 `DemoParser` 调用数从 **145 次降到 16 次**。两场 FACEIT 样本相对旧批量路径达到 **4.69× 与 4.81×**；相对最初逐玩家串行路径达到 **10.77× 与 11.02×**。

完美世界 `xiaozhen.dem` 进一步暴露了一个底层问题：当前测试环境中的 `demoparser2 0.41.4` 在建立玩家元数据时使用 `handle & 0x7FF`，把 pawn entity handle 截成 11 位。该行为与四名高 entity ID 玩家缺失 pawn 属性、坐标和精确生死状态高度吻合。

应用层能够安全恢复**队伍身份**，但无法通过现有 Python API 按正确 entity ID 重新查询 pawn，因此不能伪造或完整恢复这四名玩家的轨迹。当前实现对此采用保守降级：保留可靠结果，抑制依赖未知空间状态的标签。

---

## 2. 验证样本

### 2.1 完美世界社区平台

| 文件 | 地图 | 回合数 | 10 人结果 | 原始 `team_num` 缺失 |
| --- | --- | ---: | ---: | ---: |
| `yiji.dem` | Ancient | 23 | 10/10 | 0/230 |
| `milaji.dem` | Mirage | 29 | 10/10 | 0/290 |
| `xiaozhen.dem` | Inferno | 24 | 10/10 | 96/240 |
| `shaer.dem` | Dust2 | 24 | 10/10 | 0/240 |

样本 SHA-256：

```text
yiji.dem     49F8388CE4B5EC8570E02AE23221BFCF232A6B0173ADAC5A3CB321C1323A7532
milaji.dem   EE475FC9E27C0FE1CD78E6B213FB7E4144B77FD959062B6DE0703547BAC7291D
xiaozhen.dem 712159641F733B1A0FB7201A00550D6333B9BFA8E270D6E0F49F791D20F53237
shaer.dem    41D040A284FF647144DFDFEE334ED959B22551CE2F2D377D29E00EFDE4F3EE16
```

另有一场较早提供的完美世界样本用于旧、新路径性能对比。

### 2.2 FACEIT 社区平台

性能与回归对比主要使用以下两场带完整事件内容的样本：

- `1-0657af6e-c796-4fdd-bc77-42f4c29add00-1-1.dem`
- `1-14a6b379-f805-441f-9219-8874faad39d1-1-1.dem`

FACEIT 样本用于验证正常 Demo 不受完美平台 fallback 影响；抽样的有效 pawn `team_num` 与 controller team 之间没有冲突。

---

## 3. 多玩家共享中间表示

### 3.1 旧链路

旧的多玩家入口本质上是：

```text
for player in selected_players:
    parse the same demo again
    build the same round/event/spatial facts again
    keep only this player's output
```

10 人分析时，公共事件、回合边界、名单、比分和空间数据被重复解析。旧实现记录到的顶层原生 API 调用为：

| API | 10 人调用数 |
| --- | ---: |
| `parse_header` | 21 |
| `parse_event` | 64 |
| `parse_events` | 5 |
| `parse_ticks` | 53 |
| `parse_player_info` | 2 |
| **合计** | **145** |

这里统计的是顶层 API 调用，不代表每次调用成本完全相同；它仍能准确反映重复进入 Rust 解析链路的问题。

### 3.2 新链路

新实现分为四个阶段：

```text
Demo
  → 一次解析公共事件、回合和经济快照
  → 一次建立 SharedDemoFacts 与公共索引
  → 汇总所有目标玩家需要的空间 tick，统一 parse_ticks
  → 每个玩家只做 DataFrame 过滤、索引查询和 Python 聚合
```

共享内容包括：

- `player_death`、`weapon_fire`、`player_hurt`；
- 回合开始、冻结结束、回合结束和比赛开始事件；
- 投掷物、拆包、下包、爆炸、致盲等批量事件；
- 回合经济快照及目标玩家逐回合阵营；
- 名单、Steam ID、user ID、观战槽位和服务器信息；
- 比分、回合结束 tick、炸弹爆炸 tick；
- 致盲、手雷落点和玩家时间线事件索引；
- 所有目标玩家所需空间 tick 的并集。

正常 FACEIT Demo 上，1 人和 10 人分析都只需 **16 次**顶层解析器调用：

| API | 当前调用数 |
| --- | ---: |
| `parse_header` | 1 |
| `parse_event` | 4 |
| `parse_events` | 5 |
| `parse_ticks` | 5 |
| `parse_player_info` | 1 |
| **合计** | **16** |

经济快照还携带 `steamid` 和 `user_id`，在 tick 精确一致时直接复用于：

- 队伍身份到逐回合阵营的映射；
- 玩家名到观战槽位的映射；
- 全员名单构建。

快照 tick 不一致或必要 ID 无效时会回退到原解析路径，避免用错误时刻的数据换取表面上的调用数下降。

---

## 4. 性能基准

### 4.1 FACEIT：生产隔离 worker

旧路径数据是修改前保存的历史单次基线；新路径每场运行 3 次独立 worker，表中为中位数。新 worker 计时包含子进程启动和 10 人分析，不包含计时前的名单发现。由于新旧重复次数不同，倍数用于量级判断，不应视为严格的同批次统计实验。

| Demo | 旧逐玩家串行（历史单次） | 旧批量路径（历史单次） | 新共享 IR（3 次中位数） | 相对旧批量 | 相对旧串行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FACEIT 0657 | 43.642s | 19.004s | 4.052s | 4.69× | 10.77× |
| FACEIT 14a6 | 71.484s | 31.172s | 6.487s | 4.81× | 11.02× |

较早的完美世界样本从旧逐玩家串行的 **47.895s** 降到新批量的 **5.343s**，约 **8.96×**。

### 4.2 四个新完美样本：进程内单次 10 人分析

以下计时在名单发现后开始，属于进程内单次运行，用于兼容性烟测；不与上面的独立 worker 中位数混为同一统计口径。

| 文件 | 耗时 | 回合完整性 | 每份结果的 `all_players` |
| --- | ---: | ---: | ---: |
| `yiji.dem` | 4.377s | 23/23 | 10 |
| `milaji.dem` | 6.240s | 29/29 | 10 |
| `xiaozhen.dem` | 5.705s | 24/24 | 10 |
| `shaer.dem` | 5.292s | 24/24 | 10 |

正常 FACEIT 样本还执行了“共享快照开启”与“强制回退旧解析”的深比较。规范化随机生成的 `clip_id` 后，10 名玩家结果没有差异。

---

## 5. `xiaozhen.dem` 的字段缺失

### 5.1 表面现象

`xiaozhen.dem` 中：

- `parse_player_info()` 返回空结果；
- 24 个冻结结束快照共 240 个玩家行；
- 其中 96 行的标准 pawn `team_num` 缺失；
- 缺失集中在 4 名玩家，每人覆盖全场 24 个回合；
- 在本次全场抽取的冻结、击杀与空间请求快照中，这 4 名玩家的 `X/Y/Z`、pawn health、life state、body cell/offset 等字段均无法解析。

受影响玩家：

- `生气的木木`
- `# PikC.#`
- `她不爱我了吗`
- `Alā’al Dinنيدلا ءلاع`

与此同时，这些玩家的 controller 字段仍然存在：

- `CCSPlayerController.m_hPlayerPawn`
- `CCSPlayerController.m_iTeamNum`
- `CCSPlayerController.m_bPawnIsAlive`
- `CCSPlayerController.m_iPawnHealth`

`CCSPlayerController.m_iTeamNum` 在 10 人和所有回合上完整，并与正常玩家的有效 pawn `team_num` 一致，因此可以作为队伍身份的确定性 fallback。

### 5.2 entity handle 证据

在该 Demo 中，四名玩家的 handle 与解析器 entity ID 如下：

| 玩家 | `m_hPlayerPawn` | `handle & 0x3FFF` | parser `entity_id` | 差值 |
| --- | ---: | ---: | ---: | ---: |
| 生气的木木 | 16271434 | 2122 | 74 | 2048 |
| # PikC.# | 2459724 | 2124 | 76 | 2048 |
| 她不爱我了吗 | 3557454 | 2126 | 78 | 2048 |
| Alā’al Din… | 15076444 | 3164 | 1116 | 2048 |

四个错误 ID 都满足：

```text
parser entity_id == m_hPlayerPawn & 0x7FF
expected pawn index under the 14-bit rule == m_hPlayerPawn & 0x3FFF
```

其余六名玩家的真实 pawn ID 都小于 2048，因此 11-bit 与 14-bit 结果恰好相同，没有暴露问题。

---

## 6. demoparser 高置信根因

测试环境安装的是 `demoparser2 0.41.4`。截至官方仓库提交
[`ba39cc44`](https://github.com/LaihoE/demoparser/commit/ba39cc44cd5abfd7f34df2b3c0a7dd3630048311)，玩家元数据仍按以下方式取得 pawn entity ID：

```rust
let player_entid = match self.prop_controller.special_ids.player_pawn {
    Some(id) => match self.get_prop_from_ent(&id, entity_id) {
        Ok(Variant::U32(handle)) => Some((handle & 0x7FF) as i32),
        // ...
    },
    // ...
};
```

源码位置：[`collect_data.rs`](https://github.com/LaihoE/demoparser/blob/ba39cc44cd5abfd7f34df2b3c0a7dd3630048311/src/parser/src/second_pass/collect_data.rs#L1173-L1178)。

同一文件的 inventory 路径已经使用 `(1 << 14) - 1` 提取 entity ID，说明代码库内部目前存在 11-bit 与 14-bit 两套假设。

错误的玩家元数据 ID 随后会被用于：

- 读取 `CCSPlayerPawn.*` 属性；
- 合成 `X/Y/Z`；
- 获取 `is_alive`、health、yaw、pitch；
- 可能影响带 player 扩展属性的事件与 pawn 的关联。

这些证据高度指向：解析器先把 handle 截断，再去错误的实体槽读取 pawn 数据。不过目前尚未用 patched demoparser 从 2122、2124、2126、3164 四个实体实际读回字段；最终闭环仍需确认改用 14-bit mask 后能恢复 `X/Y/Z`、life state 和 health。

---

## 7. 为什么 Python 应用层不能恢复完整轨迹

应用层已经知道正确的低 14 位 ID，但 stock `demoparser2` Python API 没有“按任意 entity ID 查询实体表”的接口。`parse_ticks()` 内部已经使用错误的 11-bit `player_entity_id` 完成关联，Python 收到的只是缺失值。

下列候选也不能作为可靠替代：

- controller 的 `m_vecX/Y/Z` 在样本中恒为 0；
- `CCSPlayerPawn.origin` 在抽样中不是可靠的逐玩家世界坐标；
- controller alive/health 会比真实 pawn death state 延迟更新；死亡 tick 抽样中观察到 1–5 tick 的滞后，因此不能把它当作协议保证的精确状态；
- 用击杀事件反推连续轨迹只能得到零散时刻，不能替代 tick 级 pawn 状态。

所以应用层只能恢复可靠的身份和回合链路，不能诚实地声称已经恢复四名玩家的连续轨迹。

---

## 8. 当前应用层兼容策略

### 8.1 队伍身份回填

所有需要玩家阵营的 `parse_ticks()` 调用同时请求：

```python
["team_num", "CCSPlayerController.m_iTeamNum"]
```

合并规则：

1. 标准 `team_num` 为 2 或 3 时始终优先；
2. 只有标准值缺失或非法时才使用 controller 值；
3. controller 也不是 2/3 时保持未知；
4. 返回副本，不修改共享 DataFrame；
5. fallback 与原字段在同一次 `parse_ticks()` 中取得，不增加 Demo 扫描。

该规则用于：

- 回合经济；
- 目标玩家逐回合阵营；
- 两支固定队伍换边后的 side 重建；
- 全员名单与观战槽位 fallback；
- 击杀时刻队伍查询；
- 空间快照的队伍分类；
- 雷达 POV 队伍识别和同队过滤。

`xiaozhen.dem` 的 96 个缺失 team cell 全部被恢复，最终缺失数为 0。

### 8.2 空间状态采用 unknown，而不是猜测

如果一个玩家只能从 controller 得到队伍身份，同时没有有限的 pawn `X/Y`：

- 将其标记为 pawn state unknown；
- 不把 controller 的 `is_alive=False` 当作死亡事实；
- 该 tick 不生成依赖完整人数的 1vN、2vN、3v5 或斗牛标签；
- 缺失/NaN/Inf/重合坐标的“是否面向攻击者”返回 unknown；
- unknown 不再通过 `if not facing` 被误判为“偷背身”。

当前真实复跑中，四名受影响玩家的“偷背身”与 1vN/斗牛类空间敏感标签计数均为 0。

这种策略会牺牲少量可能为真的空间标签，但不会把缺数据包装成确定结论。

### 8.3 雷达保守降级

controller team fallback 可以避免 POV 队伍未知时整个雷达时间线直接返回空数组。雷达仍遵守：

- 只输出与 POV 同队的玩家；
- 只有有限 `X/Y` 的玩家才会进入画面，非法高度和朝向会安全归零；
- 不为缺 pawn 的 POV 或队友制造 `(0, 0)` 坐标；
- 不使用 controller alive/health 伪造精确状态。

因此 `xiaozhen.dem` 的缺 pawn POV 可以看到有真实坐标的同队玩家，但自身雷达点仍可能缺失。这是有边界的部分可用，而不是完整轨迹恢复。

缺 pawn POV 的 40 帧真实烟测中，每帧输出 2 名具有有限 `X/Y` 的真实队友，未输出敌方，也没有伪造 POV 点。

---

## 9. 建议的 demoparser 修复

建议在 demoparser 中定义统一的 entity index mask，例如：

```rust
const ENTITY_INDEX_MASK: u32 = (1 << 14) - 1;
```

玩家元数据路径至少应改为：

```rust
Ok(Variant::U32(handle)) => Some((handle & ENTITY_INDEX_MASK) as i32)
```

同时需要按语义审计剩余的 `0x7FF`，重点包括：

- `collect_data.rs` 中 grenade owner、active weapon、C4 owner；
- `game_events.rs` 中 pawn handle 与玩家元数据；
- `parser.rs` 中 user command 的 `pawn_entity_handle`。

不建议不加区分地全局替换：应确认每一种 handle 的编码规则，并为 entity ID 大于 2047 的情况添加测试。

### 9.1 建议的底层回归用例

1. controller pawn handle 的低 14 位大于 2047；
2. `player_entity_id == handle & 0x3FFF`；
3. 对应玩家 `X/Y/Z`、life state、health 可读取；
4. 普通低 ID Demo 结果保持不变；
5. death tick 上 pawn life state 不被 controller 延迟状态覆盖；
6. event attacker/victim 与 pawn metadata 仍正确关联；
7. usercmd、weapon、grenade owner 的高 entity ID 分别覆盖。

### 9.2 集成顺序

建议拆成两个独立变更集：

1. **CS2 Insight Agent PR**：共享 IR、缓存链路、controller team fallback、unknown 空间语义和测试；
2. **demoparser PR**：14-bit handle 修复与 Rust 回归测试；发布/构建可用 wheel 后，再在 CS2 Insight Agent 中更新并固定依赖。

这样应用层性能重构不会被底层依赖发布节奏阻塞，也能单独审查解析器语义变化。

---

## 10. 验证状态

### 后端

- 共享 IR、批量 worker、Demo 库缓存、完美 team fallback 与空间 unknown 发布相关定向测试：32/32 通过；
- 全后端：165 通过，2 个既有失败；
- 既有失败位于 `test_fade_config_resolve.py`，均为 OBS transition enabled 配置继承预期，与本次 Demo 解析改动无关；
- `python -m compileall -q backend/app` 通过；
- `git diff --check` 通过。

### 前端

- 生产构建通过；
- 相关 UI/工具测试通过；
- 全套前端测试仍有 3 个既有 locale 文案断言失败，与本次解析链路无关。

### 真实 Demo

- 两场 FACEIT 10 人分析均完整；
- 四场新完美样本均返回 10/10 玩家和完整回合；
- `xiaozhen.dem` 的队伍、名单、比分、经济和事件时间线恢复；
- 四名缺 pawn 玩家不再产生由 unknown 空间状态触发的假阳性标签；
- 四名玩家的连续 pawn 轨迹仍明确标记为底层解析器未解决项。

---

## 11. 当前边界：语音

这些社区 Demo 含有语音数据，demoparser 底层也有 voice message 收集路径，但 CS2 Insight Agent 当前分析器并未把语音内容纳入共享中间表示或业务输出。

因此本轮性能数字覆盖的是事件、回合、经济、名单、时间线和空间采样链路，**不包含语音解码、说话人映射或音频证据分析**。后续如果引入语音，应单独设计：

- voice packet 一次提取；
- tick 到说话人/Steam ID 的稳定映射；
- 音频解码与缓存边界；
- 多玩家结果共享同一份语音时间线；
- 隐私、存储和导出策略。

在这些能力实现之前，不应把“Demo 含语音”描述为“应用已经解析语音”。

---

## 12. 结论

共享 IR 已经把多玩家分析从“按玩家重复解析 Demo”改成“公共事实解析一次、玩家侧轻量消费”，10 人性能显著超过原先 3 人 2.0–2.4× 的目标。

完美平台兼容问题也已被拆清：

- `team_num` 缺失可以在应用层用 controller team 安全、低成本恢复；
- pawn 轨迹缺失的高置信根因是 demoparser 的 11-bit handle mask，仍需 patched parser 完成最终闭环；
- 应用层目前选择诚实降级，不伪造坐标、生死或空间标签；
- 完整解决需要独立修复 demoparser 的 14-bit entity handle，并用高 entity ID Demo 做回归。
