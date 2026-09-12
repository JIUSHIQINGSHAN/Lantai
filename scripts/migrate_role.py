"""
迁移脚本：将 MemoryItem 的 lane 迁移为 CognitiveRole
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select

from lantai.models.tables import CognitiveRole, MemoryItem
from lantai.storage.db import engine


def map_lane_to_role(lane: str) -> CognitiveRole:
    lane_map = {
        "fact": CognitiveRole.OBSERVATION,
        "experience": CognitiveRole.EXPERIENCE,
        "preference": CognitiveRole.BELIEF,
        "rule": CognitiveRole.RULE,
        "skill": CognitiveRole.SKILL,
        "general": CognitiveRole.OBSERVATION,
        "chat": CognitiveRole.OBSERVATION,
    }
    return lane_map.get(lane, CognitiveRole.OBSERVATION)


def main():
    print("开始迁移 MemoryItem 的 lane -> role ...")
    with Session(engine) as session:
        items = session.exec(select(MemoryItem)).all()
        count = 0
        for item in items:
            old_role = item.role
            new_role = map_lane_to_role(item.lane)
            if old_role != new_role:
                item.role = new_role
                session.add(item)
                count += 1
        session.commit()
        print(f"成功迁移 {count} 条记忆项。")


if __name__ == "__main__":
    main()
