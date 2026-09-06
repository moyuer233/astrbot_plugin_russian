# astrbot_plugin_russian

[![AstrBot](https://img.shields.io/badge/AstrBot-插件-blue)](https://docs.astrbot.app/) [![License](https://img.shields.io/badge/License-MIT-green)](#致谢)

AstrBot 群聊**俄罗斯轮盘**决斗小游戏。

移植自 [HibiKier/nonebot_plugin_russian](https://github.com/HibiKier/nonebot_plugin_russian)（MIT License），游戏机制、概率计算与文案与原版保持一致。

## 玩法介绍

向 7 格弹巢中装填 1~6 发实弹，随机排布。发起者装弹下注，任意群友（或被 @ 指定的对象）接受对决后，双方轮流对准自己扣下扳机——直到某一声枪响，中弹者输掉全部赌注。

- 💰 金币体系：每日签到领金币，对决赢取对方赌注
- 🎲 真实轮盘概率：每枪中弹概率按剩余实弹/空仓实时计算
- 🔫 支持连开多枪，心跳加速
- ⏱️ 超时机制：装弹无人接受自动作废；对决中对方超时未开枪可强制结算获胜
- 🏆 五大排行榜：金币 / 胜场 / 败场 / 欧洲人 / 慈善家
- 💾 数据持久化，无需配置数据库

## 指令列表

| 指令 | 说明 |
| --- | --- |
| `/装弹 [子弹数] [金额] [@对象]` | 发起决斗。子弹数 1~6 必填；金额默认 200；@对象为空则所有群友都可接受 |
| `/接受对决` | 接受当前决斗（示例：`/装弹 1 10`） |
| `/拒绝对决` | 拒绝被指定的决斗 |
| `/开枪 [N]` | 开 1 枪或连开 N 枪（轮流开枪，超时未开枪对方可用 `/结算` 获胜） |
| `/结算` | 对方超时未开枪时，强行结束对决并获胜 |
| `/轮盘签到` | 每日签到，随机获得金币 |
| `/我的战绩` | 查看胜/败场次与金币流水 |
| `/我的金币` | 查看当前金币 |
| `/轮盘昵称 [昵称]` | 设置决斗显示昵称（QQ 官方接口等平台无法自动获取昵称时使用） |
| `/金币排行` `/胜场排行` `/败场排行` `/欧洲人排行` `/慈善家排行` | 各类排行榜 |
| `/俄罗斯轮盘帮助` | 查看帮助 |

> 同一时间每个群只能有一场对决。
> 若 AstrBot 的 `wake_prefix` 为空，则无需 `/` 前缀即可触发指令。

## 安装

- **插件市场 / WebUI**：在 AstrBot WebUI「插件市场」中搜索 `russian` 安装
- **手动安装**：将本仓库克隆或下载至 `AstrBot/data/plugins/astrbot_plugin_russian/`，然后重载插件

```bash
git clone https://github.com/qwqZYLqwq/astrbot_plugin_russian.git data/plugins/astrbot_plugin_russian
```

无第三方依赖。

## 配置

安装后可在 WebUI「插件管理」中配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `max_bet_gold` | 1000 | 单场对决赌注上限 |
| `default_bet_gold` | 200 | 未指定金额时的默认赌注 |
| `sign_gold_min` / `sign_gold_max` | 1 / 100 | 每日签到金币范围 |
| `timeout` | 30 | 对决行动超时时间（秒） |
| `bot_name` | 本裁判 | 结算文案中的裁判名称 |

玩家数据持久化于 `data/astrbot_plugin_russian/russian_data.json`。

## 平台适配说明

| 平台 | At 支持 | 昵称显示 |
| --- | --- | --- |
| aiocqhttp（QQ 个人号） | ✅ 原生 @ | ✅ 实时群名片 |
| QQ 官方接口 | ❌（回复自带强制 @） | 需 `/轮盘昵称` 设置 |
| 其他平台 | 降级为 @昵称 文本 | 平台昵称或 `/轮盘昵称` |

> QQ 官方接口为平台限制：入站消息仅含 openid（无昵称）、出站不支持 At 消息段、无法解析 @普通群友，因此该平台上"装弹 @指定对象"会退化为公开对决。

## 致谢

- 原插件：[nonebot_plugin_russian](https://github.com/HibiKier/nonebot_plugin_russian) by **HibiKier**（MIT License）
- 昵称获取方法参考：[astrbot_plugin_roulette](https://github.com/Zhalslar/astrbot_plugin_roulette) by **Zhalslar**

## License

MIT License
