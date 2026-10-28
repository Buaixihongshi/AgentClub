"""Recognize only wholly social messages; mixed/unknown requests keep task rules.

This is a deliberately narrow fast path, not a general intent classifier. A
greeting somewhere in a request must never suppress the rest of that request.
"""
import re
from typing import Iterable, Optional

from backend.app.domain.models import AgentProfile
from backend.app.domain.agent_registry import get_agent_registry, strip_leading_addressees


_SOCIAL_UTTERANCE = re.compile(
    r"(?:"
    r"(?:大家|各位|你们|你俩|你|您)?(?:好[呀啊哇]?|早(?:上好)?|下午好|晚上好|晚安|"
    r"在(?:吗|嘛|不在|不)|最近怎么样|最近好吗|还好吗)"
    r"|hello|hi|hey"
    r"|(?:请)?(?:大家|各位|你们|你俩|三位|每人)?(?:各用一句话|各自|一起|都|各)?"
    r"(?:打个招呼|打个照面|聊聊天|闲聊(?:一下)?|随便聊聊|说句话|介绍(?:一下)?自己)"
    r"|陪我聊聊|不想聊工作"
    r"|(?:我)?(?:今天|最近|这几天|刚刚)?(?:真的|有点|好|很|太|特别)?"
    r"(?:累(?:死了|坏了|了)?|开心|难过|烦(?:死了|躁)?|无聊|郁闷|失落|紧张|焦虑|不开心|没精神)"
    r"|(?:我)?今天忙了一天|(?:我)?感觉什么都没做成"
    r")(?:了|吧|呀|啊|呢)?",
    re.IGNORECASE,
)


def is_casual_turn(content: str, agents: Optional[Iterable[AgentProfile]] = None) -> bool:
    if agents is None:
        agents = get_agent_registry().all()
    text = strip_leading_addressees(content, agents)
    utterances = [re.sub(r"\s+", "", part) for part in re.split(r"[,，。.!！?？;；\n]+", text)]
    utterances = [part for part in utterances if part]
    return bool(utterances) and all(_SOCIAL_UTTERANCE.fullmatch(part) for part in utterances)


CASUAL_HEADER = "【日常交流】"


def is_casual_prompt(prompt: str) -> bool:
    """The host-owned second line, not a marker in user/history text."""
    return prompt.split("\n", 2)[1:2] == [CASUAL_HEADER]


CASUAL_RULES = CASUAL_HEADER + "\n" + (
    "回应对方此刻的话，结合已经发生的对话决定说什么；一句话足够时可以只说一句。\n"
    "倾诉时先理解对方的处境，不急着给建议；可以自然表达关心、好奇和认同。\n"
    "除非用户询问，不例行介绍岗位、列工作原则或总结流程；不用固定开场或结束语。\n"
    "保持事实与身份准确，不编造现实经历、共同记忆或已执行的动作。"
    "本次只回应聊天，不创建文件或启动工作；任务是否结束由宿主记录。"
)
