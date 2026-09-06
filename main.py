# 俄罗斯轮盘 - AstrBot 插件
# 移植自 HibiKier/nonebot_plugin_russian (MIT License)
# https://github.com/HibiKier/nonebot_plugin_russian

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.api.star import StarTools
except Exception:
    StarTools = None


HELP_TEXT = """俄罗斯轮盘帮助：
开启游戏：/装弹 [子弹数] ?[金额](默认200金币) ?[@对象](指定决斗对象，为空则所有群友都可接受决斗)
    示例：/装弹 1 10
接受对决：/接受对决 或 /拒绝对决
开始对决：/开枪 ?[子弹数](默认1)（轮流开枪，根据子弹数量连开N枪，超时未开枪另一方可使用'结算'命令结束对决并胜利）
结算：/结算（当某一方超时未开枪，可使用该命令强行结束对决并胜利）
每日签到：/轮盘签到
我的战绩：/我的战绩
我的金币：/我的金币
设置昵称：/轮盘昵称 [昵称]（QQ 官方机器人等无法自动获取昵称的平台，可设置决斗显示名）
排行榜：/金币排行 /胜场排行 /败场排行 /欧洲人排行 /慈善家排行
【注：同一时间群内只能有一场对决】
【提示：若 wake_prefix 为空则无需 / 前缀】"""

# 弹巢容量
DRUM_SIZE = 7


def is_number(s) -> bool:
    """判断字符串是否为数字"""
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def random_bullet(num: int) -> List[int]:
    """随机子弹排列：7 格弹巢，1=实弹 0=空仓"""
    bullet_lst = [0] * DRUM_SIZE
    for i in random.sample(range(DRUM_SIZE), num):
        bullet_lst[i] = 1
    return bullet_lst


def make_result(event: AstrMessageEvent, msg):
    """把 str 或消息链包装成可发送/可 yield 的 MessageEventResult"""
    if isinstance(msg, str):
        return event.plain_result(msg)
    return event.chain_result(msg)


async def emit(event: AstrMessageEvent, msg):
    """直接发送一条消息（用于 handler 中间发送）"""
    await event.send(make_result(event, msg))


def can_at(event: AstrMessageEvent) -> bool:
    """当前平台是否支持 At 消息段（aiocqhttp / QQ 个人号）"""
    try:
        return event.get_platform_name() == "aiocqhttp"
    except Exception:
        return False


async def get_name(event: AstrMessageEvent, user_id, group_id=None) -> str:
    """获取指定用户的昵称（方法参考 astrbot_plugin_roulette）

    - aiocqhttp：get_group_member_info（群名片优先）→ get_stranger_info
    - 其他平台：QQ 官方接口等不提供昵称，返回空字符串
      （由玩家数据中的自定义昵称兜底）
    """
    user_id = str(user_id)
    if not (can_at(event) and user_id.isdigit()):
        return ""
    try:
        gid = str(group_id) if group_id is not None else event.get_group_id()
        bot = getattr(event, "bot", None)
        if bot is None:
            return ""
        if gid and gid.isdigit():
            try:
                info = await bot.get_group_member_info(
                    group_id=int(gid), user_id=int(user_id)
                )
                nickname = (info.get("card") or info.get("nickname") or "").strip()
                return nickname or user_id
            except Exception as e:
                # 群成员不存在（retcode 1200）时直接返回 ID，不再查陌生人
                if "1200" in str(e) or "不存在" in str(e):
                    return user_id
        # 非群聊场景或群查询异常：查陌生人信息
        try:
            info = await bot.get_stranger_info(user_id=int(user_id))
            return (info.get("nickname") or "").strip() or user_id
        except Exception:
            return user_id
    except Exception:
        return user_id


def get_at_id(event: AstrMessageEvent) -> str:
    """获取消息中 @ 的目标（排除机器人自身），参考 astrbot_plugin_roulette"""
    try:
        self_id = str(event.get_self_id())
    except Exception:
        self_id = ""
    for seg in event.get_messages():
        if isinstance(seg, Comp.At) and str(seg.qq) != self_id:
            return str(seg.qq)
    return ""


def mention(event: AstrMessageEvent, uid, name: str = "") -> list:
    """构造 @某人 的消息段列表。

    - aiocqhttp（QQ 个人号）：使用原生 At 消息段
    - 其他平台：昵称可用时输出 @昵称 文本；昵称不可用时返回空列表
      （QQ 官方接口回复消息本身已带强制 @ 提醒，无需冗余输出裸 ID）
    """
    uid = str(uid)
    if can_at(event) and uid.isdigit():
        return [Comp.At(qq=uid)]
    if name and name != uid:
        return [Comp.Plain(f"@{name} ")]
    return []


def mention_chain(
    event: AstrMessageEvent, prefix: str, uid, name: str = "", suffix: str = ""
) -> list:
    """构造 [前缀文本, @某人, 后缀文本] 消息链（自动降级）"""
    return [Comp.Plain(prefix)] + mention(event, uid, name) + [Comp.Plain(suffix)]


def with_sender(event: AstrMessageEvent, msg) -> list:
    """消息前加 @发送者（对应原版 at_sender=True，自动降级）"""
    if isinstance(msg, str):
        msg = [Comp.Plain(msg)]
    return mention(event, event.get_sender_id(), "") + list(msg)


class RussianManager:
    """俄罗斯轮盘游戏管理器（移植自原版 data_source.py 的 RussianManager）"""

    def __init__(self, data_dir: Path, cfg: Dict[str, Any]):
        self._player_data: Dict[str, Dict[str, dict]] = {}
        # 进行中的对局，仅内存，不落盘
        self._current: Dict[str, dict] = {}
        self.max_bet_gold: int = cfg["max_bet_gold"]
        self.sign_gold: Tuple[int, int] = cfg["sign_gold"]
        self.default_bet: int = cfg["default_bet"]
        self.timeout: int = cfg["timeout"]
        self.bot_name: str = cfg["bot_name"]

        data_dir.mkdir(parents=True, exist_ok=True)
        self.file = data_dir / "russian_data.json"
        try:
            if self.file.exists():
                self._player_data = json.loads(
                    self.file.read_text(encoding="utf-8")
                )
        except Exception as e:
            logger.error(f"俄罗斯轮盘：读取数据文件失败: {e}")

    # ---------------- 基础数据 ----------------

    def _gid(self, event: AstrMessageEvent) -> str:
        return str(event.get_group_id() or event.unified_msg_origin)

    def save(self):
        """保存玩家数据"""
        try:
            self.file.write_text(
                json.dumps(self._player_data, ensure_ascii=False, indent=4),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"俄罗斯轮盘：保存数据失败: {e}")

    def init_user(self, event: AstrMessageEvent) -> dict:
        """初始化/获取发送者的玩家数据"""
        gid, uid = self._gid(event), event.get_sender_id()
        name = event.get_sender_name() or uid
        group = self._player_data.setdefault(gid, {})
        user = group.get(uid)
        if user is None:
            user = {
                "user_id": uid,
                "group_id": gid,
                "nickname": name,
                "name_custom": False,
                "gold": 0,
                "make_gold": 0,
                "lose_gold": 0,
                "win_count": 0,
                "lose_count": 0,
                # 以日期代替原版的每日 0 点重置 is_sign 标记
                "last_sign_date": "",
            }
            group[uid] = user
        elif name and name != uid and not user.get("name_custom"):
            # 平台提供了有效昵称且用户未自定义时才更新
            user["nickname"] = name
        return user

    def display_name(self, event: AstrMessageEvent) -> str:
        """获取发送者的显示名：自定义昵称 > 平台昵称 > 用户 ID"""
        user = self.init_user(event)
        return user.get("nickname") or event.get_sender_id()

    async def display_name_async(self, event: AstrMessageEvent) -> str:
        """获取发送者的显示名（异步版：先查平台 API 实时昵称，失败再回退玩家数据）

        平台为 aiocqhttp 时可实时获取群名片；QQ 官方接口等平台
        由玩家数据中的自定义昵称（/轮盘昵称）兜底。
        """
        uid = event.get_sender_id()
        name = await get_name(event, uid, self._gid(event))
        if name and name != uid:
            # 平台拿到了实时昵称，回写玩家数据（未自定义时）
            user = self.init_user(event)
            if not user.get("name_custom"):
                user["nickname"] = name
            return user.get("nickname") or name
        return self.display_name(event)

    def set_name(self, event: AstrMessageEvent, name: str) -> str:
        """设置自定义昵称（QQ 官方接口等平台拿不到昵称时使用）"""
        user = self.init_user(event)
        user["nickname"] = name
        user["name_custom"] = True
        self.save()
        # 若正身处对局中，同步更新对局内显示名
        game = self._current.get(self._gid(event)) or {}
        if game.get("p1") == user["user_id"]:
            game["player1"] = name
        elif game.get("p2") == user["user_id"]:
            game["player2"] = name
        return f"已将你的决斗昵称设置为：{name}"

    def get_user_data(self, event: AstrMessageEvent) -> dict:
        return self.init_user(event)

    def get_current_index(self, event: AstrMessageEvent) -> int:
        game = self._current.get(self._gid(event)) or {}
        return game.get("index", 0)

    # ---------------- 签到 ----------------

    def sign(self, event: AstrMessageEvent) -> str:
        user = self.init_user(event)
        today = datetime.now().strftime("%Y-%m-%d")
        if user["last_sign_date"] == today:
            return "贪心的人是不会有好运的..."
        gold = random.randint(self.sign_gold[0], self.sign_gold[1])
        user["gold"] += gold
        user["make_gold"] += gold
        user["last_sign_date"] = today
        self.save()
        return (
            random.choice(["这是今天的钱，祝你好运...", "今天可别输光光了."])
            + f"\n你获得了 {gold} 金币"
        )

    # ---------------- 群成员信息 ----------------

    async def get_member_name(self, event: AstrMessageEvent, user_id: str) -> str:
        """获取指定群友的昵称：平台 API > 玩家自定义昵称 > 空"""
        name = await get_name(event, user_id, self._gid(event))
        if name and name != user_id:
            return name
        # 兜底：玩家数据中记录的昵称（自定义昵称或历史昵称）
        user = self._player_data.get(self._gid(event), {}).get(str(user_id))
        if user and user.get("nickname") and user["nickname"] != user_id:
            return user["nickname"]
        return ""

    # ---------------- 对局流程 ----------------

    async def check_current_game(self, event: AstrMessageEvent) -> Optional[str]:
        """检查当前是否有决斗存在（含惰性超时清理）"""
        gid = self._gid(event)
        self.init_user(event)
        game = self._current.get(gid)
        if not game or not game.get("p1"):
            return None
        overtime = time.time() - game["time"] > self.timeout
        if not game.get("p2"):
            # 装弹后无人接受
            if overtime:
                self._current[gid] = {}
            else:
                return (
                    f"现在是 {game['player1']} 发起的对决\n"
                    f"请等待比赛结束后再开始下一轮..."
                )
            return None
        # 对决进行中
        if not overtime:
            return f"{game['player1']} 和 {game['player2']}的对决还未结束！"
        await emit(event, "决斗已过时，强行结算...")
        await self.end_game(event)
        return None

    async def ready_game(
        self, event: AstrMessageEvent, at_id: Optional[str], money: int, bullet_num: int
    ) -> list:
        """发起游戏：生成对局状态并返回装弹提示消息链"""
        gid = self._gid(event)
        uid = event.get_sender_id()
        player1_name = await self.display_name_async(event)
        self._current[gid] = {
            "p1": uid,
            "player1": player1_name,
            "p2": None,
            "player2": "",
            "at": at_id,
            "next": uid,
            "money": money,
            "bullet": random_bullet(bullet_num),
            "bullet_num": bullet_num,
            "null_bullet_num": DRUM_SIZE - bullet_num,
            "index": 0,
            "time": time.time(),
        }
        prob = str(float(bullet_num) / DRUM_SIZE * 100)[:5]
        base = (
            ("咔 " * bullet_num)[:-1]
            + f"，装填完毕\n挑战金额：{money}\n第一枪的概率为：{prob}%\n"
        )
        if at_id:
            at_name = await self.get_member_name(event, at_id) or "这位勇士"
            self._current[gid]["at_name"] = at_name
            return mention_chain(
                event,
                base + f"{player1_name} 向 ",
                at_id,
                at_name,
                f" 发起了决斗！请 {at_name} 在{self.timeout}秒内回复‘接受对决’或‘拒绝对决’，超时此次决斗作废！",
            )
        return [
            Comp.Plain(
                base
                + f"若{self.timeout}秒内无人接受挑战则此次对决作废"
                + "【首次游玩请发送 /俄罗斯轮盘帮助 来查看命令】"
            )
        ]

    async def accept(self, event: AstrMessageEvent):
        """接受决斗请求"""
        gid, uid = self._gid(event), event.get_sender_id()
        self.init_user(event)
        game = self._current.get(gid)
        if not game or not game.get("p1"):
            return "目前没有进行的决斗，请发送 /装弹 开启决斗吧！"
        if game.get("p2"):
            if uid in (game["p1"], game["p2"]):
                return "你已经身处决斗之中了啊，给我认真一点啊！"
            return "已经有人接受对决了，你还是乖乖等待下一场吧！"
        if game["p1"] == uid:
            return "请不要自己枪毙自己！换人来接受对决..."
        if game["at"] and game["at"] != uid:
            return mention_chain(
                event,
                "这场对决是邀请 ",
                game["at"],
                game.get("at_name", ""),
                " 的，不要捣乱！",
            )
        if time.time() - game["time"] > self.timeout:
            self._current[gid] = {}
            return "这场对决邀请已经过时了，请重新发起决斗..."
        if self.get_user_data(event)["gold"] < game["money"]:
            if game["at"] == uid:
                self._current[gid] = {}
                return "你的金币不足以接受这场对决！对决还未开始便结束了，请重新装弹！"
            return "你的金币不足以接受这场对决！"
        player2_name = await self.display_name_async(event)
        game["p2"] = uid
        game["player2"] = player2_name
        game["time"] = time.time()
        return mention_chain(
            event,
            f"{player2_name}接受了对决！\n请 ",
            game["p1"],
            game["player1"],
            " 先开枪！",
        )

    async def refuse(self, event: AstrMessageEvent):
        """拒绝决斗请求"""
        gid, uid = self._gid(event), event.get_sender_id()
        self.init_user(event)
        game = self._current.get(gid)
        if not game or not game.get("p1"):
            return "你要拒绝啥？明明都没有人发起对决的说！"
        if not game["at"]:
            return "这是一场公开对决，无法被拒绝，等待超时自动作废吧！"
        if uid != game["at"]:
            return "又不是找你决斗，你拒绝什么啊！气！"
        p1 = game["p1"]
        p1_name = game["player1"]
        refuser = await self.display_name_async(event)
        self._current[gid] = {}
        return mention_chain(
            event, "", p1, p1_name, f"\n{refuser} 拒绝了你的对决！"
        )

    def settlement(self, event: AstrMessageEvent) -> Tuple[str, bool]:
        """结算检测，返回 (消息, 是否执行结算)"""
        gid, uid = self._gid(event), event.get_sender_id()
        self.init_user(event)
        game = self._current.get(gid)
        if not game or not game.get("p1") or not game.get("p2"):
            return "比赛并没有开始...无法结算...", False
        if uid not in (game["p1"], game["p2"]):
            return "吃瓜群众不要捣乱！黄牌警告！", False
        if time.time() - game["time"] <= self.timeout:
            return (
                f"{game['player1']} 和 {game['player2']} 比赛并未超时，请继续比赛...",
                False,
            )
        win_name = (
            game["player1"] if game["next"] == game["p2"] else game["player2"]
        )
        return f"这场对决是 {win_name} 胜利了", True

    async def shot(self, event: AstrMessageEvent, count: int) -> Optional[Union[str, list]]:
        """开枪！！！内部发送所有消息，返回值（若非 None）由 handler 兜底发送"""
        gid, uid = self._gid(event), event.get_sender_id()
        game = self._current.get(gid)

        # ---- 开枪前合法性检查 ----
        if not game or "time" not in game:
            return "目前没有进行的决斗，请发送 /装弹 开启决斗吧！"
        if time.time() - game["time"] > self.timeout:
            if not game.get("p2"):
                self._current[gid] = {}
                return "这场对决已经过时了，请重新装弹吧！"
            await emit(event, "决斗已过时，强行结算...")
            await self.end_game(event)
            return None
        if not game.get("p1"):
            return "没有对决，也还没装弹呢，请先输入 /装弹 吧！"
        if game["p1"] == uid and not game.get("p2"):
            return "baka，你是要枪毙自己嘛笨蛋！"
        if not game.get("p2"):
            return "请这位勇士先发送 /接受对决 来站上擂台..."
        if game["next"] != uid:
            if uid not in (game["p1"], game["p2"]):
                nickname = await self.display_name_async(event)
                return random.choice(
                    [
                        f"不要打扰 {game['player1']} 和 {game['player2']} 的决斗啊！",
                        f"给我好好做好一个观众！不然{self.bot_name}就要生气了",
                        f"不要捣乱啊baka{nickname}！",
                    ]
                )
            nickname = (
                game["player1"] if game["next"] == game["p1"] else game["player2"]
            )
            return f"你的左轮不是连发的！该 {nickname} 开枪了"

        # ---- 开枪判定 ----
        current_index = game["index"]
        _tmp = game["bullet"][current_index : current_index + count]
        flag = _tmp.index(1) + 1 if 1 in _tmp else -1

        if flag == -1:
            # 存活
            next_uid = game["p1"] if uid == game["p2"] else game["p2"]
            next_name = game["player1"] if next_uid == game["p1"] else game["player2"]
            # 下一枪中弹概率 = 剩余实弹 / (剩余空仓 + 剩余实弹)
            x = str(
                float(game["bullet_num"])
                / float(game["null_bullet_num"] - count + game["bullet_num"])
                * 100
            )[:5]
            _msg = f"连开{count}枪，" if count > 1 else ""
            await emit(
                event,
                mention_chain(
                    event,
                    _msg
                    + random.choice(
                        [
                            "呼呼，没有爆裂的声响，你活了下来",
                            "虽然黑洞洞的枪口很恐怖，但好在没有子弹射出来，你活下来了",
                            f'{"咔 " * count}，你没死，看来运气不错',
                        ]
                    )
                    + f"\n下一枪中弹的概率：{x}%\n轮到 ",
                    next_uid,
                    next_name,
                    " 了",
                ),
            )
            game["null_bullet_num"] -= count
            game["next"] = next_uid
            game["time"] = time.time()
            game["index"] += count
            return None

        # 中弹死亡
        await emit(
            event,
            mention_chain(
                event,
                " ",
                uid,
                await self.display_name_async(event),
                random.choice(
                    [
                        '"嘭！"，你直接去世了',
                        "眼前一黑，你直接穿越到了异世界...(死亡)",
                        "终究还是你先走一步...",
                    ]
                )
                + f"\n第 {current_index + flag} 发子弹送走了你...",
            ),
        )
        win_name = game["player1"] if uid == game["p2"] else game["player2"]
        await asyncio.sleep(0.5)
        await emit(event, f"这场对决是 {win_name} 胜利了")
        await self.end_game(event)
        return None

    async def end_game(self, event: AstrMessageEvent):
        """游戏结束结算"""
        gid = self._gid(event)
        game = self._current.get(gid)
        if not game or not game.get("p1") or not game.get("p2"):
            return
        # 败者 = 当前轮到开枪的一方
        if game["next"] == game["p1"]:
            win_id, lose_id = game["p2"], game["p1"]
            win_name, lose_name = game["player2"], game["player1"]
        else:
            win_id, lose_id = game["p1"], game["p2"]
            win_name, lose_name = game["player1"], game["player2"]
        rand = random.randint(0, 5)
        gold = game["money"]
        fee = int(gold * rand / 100)
        fee = 1 if fee < 1 and rand != 0 else fee

        group = self._player_data.setdefault(gid, {})
        win_user = group.setdefault(
            win_id,
            {
                "user_id": win_id,
                "group_id": gid,
                "nickname": win_name,
                "gold": 0,
                "make_gold": 0,
                "lose_gold": 0,
                "win_count": 0,
                "lose_count": 0,
                "last_sign_date": "",
            },
        )
        lose_user = group.setdefault(
            lose_id,
            {
                "user_id": lose_id,
                "group_id": gid,
                "nickname": lose_name,
                "gold": 0,
                "make_gold": 0,
                "lose_gold": 0,
                "win_count": 0,
                "lose_count": 0,
                "last_sign_date": "",
            },
        )
        win_user["gold"] += gold - fee
        win_user["make_gold"] += gold - fee
        win_user["win_count"] += 1
        lose_user["gold"] -= gold
        lose_user["lose_gold"] += gold
        lose_user["lose_count"] += 1
        self.save()

        bullet_str = "".join("__ " if x == 0 else "| " for x in game["bullet"])
        logger.info(
            f"俄罗斯轮盘：胜者：{win_name} - 败者：{lose_name} - 金币：{gold}"
        )
        self._current[gid] = {}
        await emit(
            event,
            "结算：\n"
            f"\t胜者：{win_name}\n"
            f"\t赢取金币：{gold - fee}\n"
            f"\t累计胜场：{win_user['win_count']}\n"
            f"\t累计赚取金币：{win_user['make_gold']}\n"
            "-------------------\n"
            f"\t败者：{lose_name}\n"
            f"\t输掉金币：{gold}\n"
            f"\t累计败场：{lose_user['lose_count']}\n"
            f"\t累计输掉金币：{lose_user['lose_gold']}\n"
            "-------------------\n"
            f"哼哼，{self.bot_name}从中收取了 {rand}%({fee}金币) 作为手续费！\n"
            f"子弹排列：{bullet_str.strip()}",
        )

    # ---------------- 排行榜 ----------------

    def rank(self, group_id: str, type_: str) -> str:
        """获取排行榜"""
        group = self._player_data.get(str(group_id)) or {}
        configs = {
            "gold_rank": ("gold", "\t金币排行榜"),
            "win_rank": ("win_count", "\t胜场排行榜"),
            "lose_rank": ("lose_count", "\t败场排行榜"),
            "make_gold": ("make_gold", "\t赢取金币排行榜"),
            "lose_gold": ("lose_gold", "\t输掉金币排行榜"),
        }
        key, title = configs.get(type_, ("gold", "\t金币排行榜"))
        users = sorted(
            group.values(), key=lambda u: u.get(key, 0), reverse=True
        )[:10]
        if not users:
            return title + "\n暂时还没有任何数据哦~"
        lines = [
            f"{u.get('nickname', u.get('user_id', '?'))}：{u.get(key, 0)}"
            for u in users
        ]
        return title + "\n" + "\n".join(lines)


@register(
    "astrbot_plugin_russian",
    "HibiKier(原版) / AstrBot 移植",
    "群聊俄罗斯轮盘决斗小游戏：装弹、轮流开枪、金币赌注与排行榜（移植自 nonebot_plugin_russian）",
    "1.0.0",
    "https://github.com/qwqZYLqwq/astrbot_plugin_russian",
)
class RussianPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        config = config or {}
        sign_min = int(config.get("sign_gold_min", 1))
        sign_max = int(config.get("sign_gold_max", 100))
        if sign_min > sign_max:
            sign_min, sign_max = sign_max, sign_min
        cfg = {
            "max_bet_gold": int(config.get("max_bet_gold", 1000)),
            "default_bet": int(config.get("default_bet_gold", 200)),
            "sign_gold": (sign_min, sign_max),
            "timeout": int(config.get("timeout", 30)),
            "bot_name": str(config.get("bot_name", "本裁判")),
        }
        # 持久化数据存放于 data 目录下，避免插件更新时被覆盖
        if StarTools is not None:
            try:
                data_dir = StarTools.get_data_dir("astrbot_plugin_russian")
            except Exception:
                data_dir = Path("data") / "astrbot_plugin_russian"
        else:
            data_dir = Path("data") / "astrbot_plugin_russian"
        self.manager = RussianManager(data_dir, cfg)
        logger.info("俄罗斯轮盘插件已加载")

    async def terminate(self):
        """插件被卸载/停用时保存数据"""
        self.manager.save()

    # ---------------- 指令 ----------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("俄罗斯轮盘帮助", alias={"轮盘帮助"})
    async def help_cmd(self, event: AstrMessageEvent):
        """查看俄罗斯轮盘帮助"""
        yield event.plain_result(HELP_TEXT)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘签到")
    async def sign_cmd(self, event: AstrMessageEvent):
        """每日轮盘签到领取金币"""
        msg = self.manager.sign(event)
        yield event.chain_result(with_sender(event, msg))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("轮盘昵称", alias={"决斗昵称", "设置昵称"})
    async def set_name_cmd(self, event: AstrMessageEvent):
        """设置决斗显示昵称（QQ 官方接口等无法获取昵称的平台）"""
        args = event.message_str.split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            current = self.manager.display_name(event)
            yield event.plain_result(
                f"你当前的决斗昵称：{current}\n使用 /轮盘昵称 [昵称] 来设置（不超过 20 字）"
            )
            return
        name = args[1].strip()[:20]
        msg = self.manager.set_name(event, name)
        yield event.chain_result(with_sender(event, msg))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("俄罗斯轮盘", alias={"装弹", "俄罗斯转盘"})
    async def load_cmd(self, event: AstrMessageEvent):
        """装弹发起决斗：装弹 [子弹数] [金额] [@对象]"""
        args = event.message_str.split()[1:]
        if args and args[0] == "帮助":
            yield event.plain_result(HELP_TEXT)
            return

        # 解析 @ 对象（参考 astrbot_plugin_roulette 的 get_at_id）
        at_id = get_at_id(event) or None

        # 解析参数：第一个 1~6 的数字为子弹数，之后的正整数为赌注
        bullet_num, money = None, self.manager.default_bet
        for a in args:
            if not is_number(a):
                continue
            n = int(float(a))
            if bullet_num is None and 1 <= n <= 6:
                bullet_num = n
            elif 0 < n <= self.manager.max_bet_gold:
                money = n

        if bullet_num is None:
            yield event.plain_result(
                "请在指令后加上装填子弹的数量（1~6 颗）！\n"
                "格式：/装弹 [子弹数] [金额] [@对象]\n示例：/装弹 1 10"
            )
            return

        # 检查群内是否有未结束的对决
        tip = await self.manager.check_current_game(event)
        if tip:
            yield event.plain_result(tip)
            return

        # 检查金币
        user = self.manager.get_user_data(event)
        if money > user["gold"]:
            yield event.chain_result(
                with_sender(event, "你没有足够的钱支撑起这场挑战")
            )
            return

        chain = await self.manager.ready_game(event, at_id, money, bullet_num)
        yield event.chain_result(chain)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("接受对决", alias={"接受决斗", "接受挑战"})
    async def accept_cmd(self, event: AstrMessageEvent):
        """接受决斗"""
        msg = await self.manager.accept(event)
        yield event.chain_result(with_sender(event, msg))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("拒绝对决", alias={"拒绝决斗", "拒绝挑战"})
    async def refuse_cmd(self, event: AstrMessageEvent):
        """拒绝决斗"""
        msg = await self.manager.refuse(event)
        yield event.chain_result(with_sender(event, msg))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("开枪", alias={"咔", "嘭", "嘣"})
    async def shot_cmd(self, event: AstrMessageEvent):
        """开枪 ?[子弹数](默认1)"""
        args = event.message_str.split()[1:]
        count = 1
        if args and is_number(args[0]):
            count = int(float(args[0]))
            remaining = DRUM_SIZE - self.manager.get_current_index(event)
            if count > remaining:
                yield event.plain_result(
                    f"你不能开{count}枪，大于剩余的子弹数量，"
                    f"剩余子弹数量：{remaining}"
                )
                return
        ret = await self.manager.shot(event, count)
        if ret is not None:
            yield make_result(event, ret)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("结算")
    async def settlement_cmd(self, event: AstrMessageEvent):
        """当某一方超时未开枪，强行结束对决并胜利"""
        msg, should_end = self.manager.settlement(event)
        if should_end:
            await emit(event, with_sender(event, msg))
            await self.manager.end_game(event)
            return
        yield event.chain_result(with_sender(event, msg))

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("我的战绩")
    async def record_cmd(self, event: AstrMessageEvent):
        """查看我的战绩"""
        user = self.manager.get_user_data(event)
        yield event.chain_result(
            with_sender(
                event,
                "俄罗斯轮盘\n"
                f"胜利场次：{user['win_count']}\n"
                f"失败场次：{user['lose_count']}\n"
                f"赚取金币：{user['make_gold']}\n"
                f"输掉金币：{user['lose_gold']}",
            )
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command("我的金币")
    async def my_gold_cmd(self, event: AstrMessageEvent):
        """查看我的金币"""
        gold = self.manager.get_user_data(event)["gold"]
        yield event.chain_result(
            with_sender(event, f"你还有 {gold} 枚金币")
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.command(
        "金币排行",
        alias={
            "胜场排行",
            "胜利排行",
            "败场排行",
            "失败排行",
            "欧洲人排行",
            "慈善家排行",
        },
    )
    async def rank_cmd(self, event: AstrMessageEvent):
        """查看各类排行榜"""
        cmd = event.message_str.split()[0] if event.message_str.split() else ""
        if "金币" in cmd:
            type_ = "gold_rank"
        elif "胜场" in cmd or "胜利" in cmd:
            type_ = "win_rank"
        elif "败场" in cmd or "失败" in cmd:
            type_ = "lose_rank"
        elif "欧洲人" in cmd:
            type_ = "make_gold"
        else:
            type_ = "lose_gold"
        yield event.plain_result(
            self.manager.rank(self.manager._gid(event), type_)
        )
