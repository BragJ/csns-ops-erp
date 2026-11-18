import hashlib
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.orm import Session, selectinload

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    require_operator_or_admin,
    sign_payload,
    verify_password,
)
from database import Base, SessionLocal, engine, get_db
from models import (
    BeamForecast,
    DutyShift,
    Event,
    EventHistory,
    LogTarget,
    NavItem,
    PatrolSlot,
    SpectrometerAssist,
    SpectrometerAssistCategory,
    EpicsAlarmPV,
    ShiftHandover,
    ShiftLog,
    ShiftRecord,
    ShiftTodo,
    SopExecution,
    User,
    utcnow,
)


BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
STAGES = ("pending", "progress", "escalated", "closed")
STAGE_NAMES = {
    "pending": "1. 待处理",
    "progress": "2. 排查排故中",
    "escalated": "3. 专业组介入",
    "closed": "4. 彻底闭环",
}
DUTY_ROSTER = ["王征", "王健威", "周科", "黄淯辉", "张文翔", "曾智蓉", "叶培钊", "钟嘉充", "徐锦鸿", "汪静"]
DAY_SLOT_TIMES = ["09:00", "12:00", "15:00"]
NIGHT_SLOT_TIMES = ["18:00", "21:00", "00:00", "03:00", "06:00"]
NAV_CATEGORIES = {"target_control", "spectrometer_run", "sample_env", "video_monitor"}
ASSIST_CATEGORY_SEEDS = [
    ("sample", "谱仪换样", "🧪", "谱仪低温换样板块：GPPD、高压谱仪等低温恒温器 CCR06 / CCR09 样品更换与双人复核。"),
    ("anis", "大气谱仪", "🎯", "大气中子谱仪板块：散射室电动屏蔽铅门启闭、样品台多维对准及准直器工况调整。"),
    ("other", "其它协助", "🛠️", "其它协作板块：谱仪 Run 异常重新起动、真空泵组保障、水系统辐射剂量核验等。"),
]
ASSIST_SEEDS = [
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门全开到位", "夜班", "2", "2026/10/22", "王健威、王征", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门闭合复核", "白班", "1", "2026/10/23", "汪静", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "1", "2026/10/24", "钟嘉充", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "2", "2026/10/25", "王健威", ""),
    ("other", "协助谱仪重新起run", "成像谱仪", "", "夜班", "1", "2026/10/26", "叶培钊", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "1", "2026/10/29", "周科", ""),
    ("other", "协助真空泵组运行", "高能非弹谱仪", "", "夜班", "3", "2026/10/29", "徐锦鸿", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "2", "2026/10/30", "王征、王健威", ""),
    ("other", "协助检查水系统辐射剂量监测曲线", "实验公用系统", "", "白班", "1", "2026/10/30", "王健威", ""),
    ("anis", "协助谱仪工作", "大气谱仪", "", "白班", "", "2026/10/31", "张文翔、曾智蓉", ""),
    ("sample", "协助CCR09换样", "高压谱仪", "", "夜班", "1", "2026/10/31", "叶培钊、钟嘉充", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "3", "2026/11/1", "徐锦鸿、汪静", ""),
    ("sample", "协助CCR09换样", "高压谱仪", "", "白班", "1", "2026/11/1", "徐锦鸿、汪静", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门全开到位", "夜班", "N", "2026/11/1", "王健威", ""),
    ("other", "现场查看样品环境温度", "高压谱仪", "", "夜班", "1", "2026/11/1", "王征", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门闭合复核", "白班", "2", "2026/11/2", "周科", ""),
    ("sample", "协助CCR09换样", "高压谱仪", "", "白班", "1", "2026/11/2", "黄清辉", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "白班", "1", "2026/11/2", "黄清辉", ""),
    ("sample", "协助CCR09换样", "高压谱仪", "", "白班", "1", "2026/11/3", "叶培钊", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门全开到位", "白班", "2", "2026/11/3", "钟嘉充", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门闭合复核", "夜班", "5", "2026/11/3", "徐锦鸿、汪静", ""),
    ("anis", "独立更换样品、调整样品台、准直器工况", "大气谱仪", "准直器 2# 对应工况", "夜班", "1", "2026/11/3", "徐锦鸿、汪静", ""),
    ("sample", "协助CCR06换样", "GPPD谱仪", "", "夜班", "1", "2026/11/5", "叶培钊、钟嘉充", ""),
    ("anis", "协助开关散射室屏蔽门", "大气谱仪", "屏蔽门全开到位", "夜班", "N", "2026/11/6", "王健威、王征", ""),
    ("sample", "协助CCR09换样", "高压谱仪", "", "夜班", "1", "2026/11/6", "王征", ""),
]


def as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


ASSIST_PARAM_BACKFILL = (
    ("协助开关散射室屏蔽门", "屏蔽门全开到位"),
    ("独立更换样品、调整样品台、准直器工况", "准直器 2# 对应工况"),
)


def ensure_assist_param_column(db: Session) -> None:
    """为既有 SQLite 库平滑补列，并一次性回填历史工况参数与班次归属。"""
    columns = {column["name"] for column in inspect(engine).get_columns("spectrometer_assists")}
    if "param_condition" not in columns:
        db.execute(text("ALTER TABLE spectrometer_assists ADD COLUMN param_condition VARCHAR(255) NOT NULL DEFAULT ''"))
        db.commit()
    if "created_shift_id" not in columns:
        db.execute(
            text("ALTER TABLE spectrometer_assists ADD COLUMN created_shift_id INTEGER REFERENCES duty_shifts(id) ON DELETE SET NULL")
        )
        db.commit()

    for action, param in ASSIST_PARAM_BACKFILL:
        db.execute(
            text(
                "UPDATE spectrometer_assists SET param_condition = :param "
                "WHERE spectrometer = :spec AND action_content = :action "
                "AND (param_condition IS NULL OR param_condition = '')"
            ),
            {"param": param, "spec": "大气谱仪", "action": action},
        )

    # 历史记录统一归属到最近已封班的班次，确保普通值班员无法追溯篡改。
    closed_shift_id = db.scalar(
        select(DutyShift.id).where(DutyShift.status == "closed").order_by(DutyShift.id.desc())
    )
    if closed_shift_id is not None:
        db.execute(
            text(
                "UPDATE spectrometer_assists SET created_shift_id = :shift_id "
                "WHERE created_shift_id IS NULL"
            ),
            {"shift_id": closed_shift_id},
        )
    db.commit()


def ensure_shift_todo_columns(db: Session) -> None:
    """为既有 SQLite 库补齐待办滚动留存字段，历史数据按创建班次时间回填。"""
    columns = {column["name"] for column in inspect(engine).get_columns("shift_todos")}
    if "completed_by" not in columns:
        db.execute(text("ALTER TABLE shift_todos ADD COLUMN completed_by VARCHAR(80)"))
        db.commit()
    if "carried_over" not in columns:
        db.execute(text("ALTER TABLE shift_todos ADD COLUMN carried_over BOOLEAN NOT NULL DEFAULT 0"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_shift_todos_carried_over ON shift_todos (carried_over)"))
        db.commit()
    if "created_at" not in columns:
        db.execute(text("ALTER TABLE shift_todos ADD COLUMN created_at DATETIME"))
        db.execute(
            text(
                "UPDATE shift_todos SET created_at = COALESCE("
                "(SELECT planned_start FROM duty_shifts WHERE duty_shifts.id = shift_todos.shift_id), due_at)"
                " WHERE created_at IS NULL"
            )
        )
        db.commit()

    db.execute(
        text(
            "UPDATE shift_todos SET completed_by = COALESCE(NULLIF(completed_by, ''), created_by)"
            " WHERE is_done = 1 AND completed_by IS NULL"
        )
    )
    db.commit()

    recalibrate_todo_timestamps(db)


def ensure_shift_history_schema(db: Session) -> None:
    """为既有库补齐班次历史台账字段，并把历史班次回填进 shift_records。"""
    log_columns = {column["name"] for column in inspect(engine).get_columns("shift_logs")}
    if "shift_date" not in log_columns:
        db.execute(text("ALTER TABLE shift_logs ADD COLUMN shift_date VARCHAR(20)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_shift_logs_shift_date ON shift_logs (shift_date)"))
        db.commit()
    if "shift_type" not in log_columns:
        db.execute(text("ALTER TABLE shift_logs ADD COLUMN shift_type VARCHAR(10)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_shift_logs_shift_type ON shift_logs (shift_type)"))
        db.commit()

    # 老流水若缺少日期/班次，从所属班次与其发生时间戳推导补全
    db.execute(
        text(
            "UPDATE shift_logs SET shift_date = COALESCE("
            "(SELECT d.shift_date FROM duty_shifts d WHERE d.id = shift_logs.shift_id), substr(occurred_at, 1, 10))"
            " WHERE shift_date IS NULL OR shift_date = ''"
        )
    )
    db.execute(
        text(
            "UPDATE shift_logs SET shift_type = COALESCE("
            "(SELECT CASE d.shift_type WHEN 'night' THEN '夜班' ELSE '白班' END FROM duty_shifts d WHERE d.id = shift_logs.shift_id), '白班')"
            " WHERE shift_type IS NULL OR shift_type = ''"
        )
    )
    db.commit()

    # 把已存在的班次同步进主档案，已归档的交接班次保留 is_handed_over 标记
    shifts = db.scalars(select(DutyShift).order_by(DutyShift.id)).all()
    for shift in shifts:
        handed_over = db.scalar(select(ShiftHandover).where(ShiftHandover.shift_id == shift.id)) is not None
        record = sync_shift_record(db, shift, handover=handed_over)
        if shift.leader_name or shift.operator_name:
            record.shift_leader = shift.leader_name or record.shift_leader
            record.shift_operator = shift.operator_name or record.shift_operator
            record.shift_student = shift.student_name or record.shift_student
            record.beam_mode = shift.beam_mode or record.beam_mode
    db.commit()


def recalibrate_todo_timestamps(db: Session) -> None:
    """校准被迁移批量写入的完成时间（仅针对明显的批量污染行）。

    早期补列时，一批历史已完成行的 done_at 被写成同一分钟内连续的时刻，
    而它们的业务约定时间却横跨多日。这类“同批密集写入 + 业务跨度极大”的
    特征即为迁移污染，此时把 done_at 回落到业务约定时间；正常单条完成记录
    不受影响，历史审计语义得以保留。
    """
    rows = db.scalars(select(ShiftTodo).where(ShiftTodo.is_done.is_(True))).all()
    batches: dict[datetime, list[ShiftTodo]] = {}
    for todo in rows:
        if todo.done_at is None:
            continue
        key = todo.done_at.replace(second=0, microsecond=0)
        batches.setdefault(key, []).append(todo)

    changed = False
    for todos in batches.values():
        if len(todos) < 3:
            continue
        due_times = [todo.due_at for todo in todos if todo.due_at is not None]
        if len(due_times) < 3 or (max(due_times) - min(due_times)) <= timedelta(days=3):
            continue
        for todo in todos:
            if todo.due_at is None:
                continue
            if abs(todo.done_at - todo.due_at) > timedelta(days=3):
                todo.done_at = todo.due_at
                changed = True
    if changed:
        db.commit()


def iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


TODO_RETENTION_DAYS = 7


def todo_retention_anchor(todo: ShiftTodo) -> Optional[datetime]:
    """待办留存锚点：完成时间优先，其次创建时间，最后回落业务约定时间。

    历史行补列时 done_at 可能被批量写成迁移当天（见 recalibrate_todo_timestamps），
    该函数会在启动时先行修正，因此这里可以安全地以完成时间为准，
    既避免旧记录误驻留，也不会误伤“跨班继承后才完成”的近期记录。
    """
    for value in (todo.done_at, todo.created_at, todo.due_at):
        if value is not None:
            return value
    return None


def todo_is_visible(todo: ShiftTodo, now: Optional[datetime] = None) -> bool:
    """滚动留存窗口：未完成待办永久保留，已完成仅保留最近一周（7 天）。"""
    if not todo.is_done:
        return True
    anchor = todo_retention_anchor(todo)
    if anchor is None:
        return False
    cutoff = (now or utcnow()) - timedelta(days=TODO_RETENTION_DAYS)
    return anchor >= cutoff


def list_visible_todos(db: Session, shift_id: Optional[int] = None) -> list[ShiftTodo]:
    """跨班拉取待办清单：待执行在前（按约定时间升序），已完成在后（按完成时间倒序）。"""
    now = utcnow()
    items = [item for item in db.scalars(select(ShiftTodo)).all() if todo_is_visible(item, now)]
    pending = sorted((item for item in items if not item.is_done), key=lambda item: item.due_at)
    done = sorted(
        (item for item in items if item.is_done),
        key=lambda item: todo_retention_anchor(item) or datetime.min,
        reverse=True,
    )
    return pending + done


def display_time(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "--"


def display_minute(value: Optional[datetime]) -> str:
    return value.strftime("%H:%M") if value else "--"


def shift_is_day(shift: DutyShift) -> bool:
    return shift.shift_type == "day"


def shift_window(shift_date: date, shift_type: str) -> tuple[datetime, datetime]:
    if shift_type == "day":
        return datetime.combine(shift_date, datetime.min.time()).replace(hour=8), datetime.combine(
            shift_date, datetime.min.time()
        ).replace(hour=17, minute=30)
    return datetime.combine(shift_date, datetime.min.time()).replace(hour=17, minute=30), datetime.combine(
        shift_date + timedelta(days=1), datetime.min.time()
    ).replace(hour=8)


def slot_datetime(shift: DutyShift, slot_time: str) -> datetime:
    hour, minute = (int(part) for part in slot_time.split(":"))
    candidate = datetime.combine(shift.shift_date, datetime.min.time()).replace(hour=hour, minute=minute)
    if not shift_is_day(shift) and hour < 8:
        candidate += timedelta(days=1)
    return candidate


def generate_slots(db: Session, shift: DutyShift) -> None:
    times = DAY_SLOT_TIMES if shift_is_day(shift) else NIGHT_SLOT_TIMES
    for slot_time in times:
        db.add(
            PatrolSlot(
                shift_id=shift.id,
                slot_key=slot_time,
                planned_at=slot_datetime(shift, slot_time),
                status="pending",
                result="待到点巡查",
            )
        )


def create_following_shift(db: Session, previous: DutyShift, leader_name: str) -> DutyShift:
    if shift_is_day(previous):
        next_date = previous.shift_date
        next_type = "night"
    else:
        next_date = previous.shift_date + timedelta(days=1)
        next_type = "day"
    start, end = shift_window(next_date, next_type)
    following = DutyShift(
        shift_date=next_date,
        shift_type=next_type,
        planned_start=start,
        planned_end=end,
        leader_name=leader_name,
        operator_name=previous.operator_name,
        student_name=previous.student_name,
        beam_mode=previous.beam_mode,
        status="active",
    )
    db.add(following)
    db.flush()
    generate_slots(db, following)
    return following


def seed_database(db: Session) -> DutyShift:
    admin = db.scalar(select(User).where(User.username == "zhouke"))
    if admin is None:
        db.add(
            User(
                username="zhouke",
                password_hash=hash_password("zhouke2026"),
                real_name="周科",
                dept="靶站谱仪运行组",
                role="admin",
                status="active",
            )
        )
        db.flush()

    if db.scalar(select(DutyShift).where(DutyShift.status == "active")) is None:
        today = utcnow().date()
        start, end = shift_window(today, "day")
        shift = DutyShift(
            shift_date=today,
            shift_type="day",
            planned_start=start,
            planned_end=end,
            leader_name="王征",
            operator_name="王健威",
            student_name="张超月",
            beam_mode="供束打靶",
            status="active",
        )
        db.add(shift)
        db.flush()
        generate_slots(db, shift)
        db.add_all(
            [
                ShiftTodo(shift_id=shift.id, due_at=start, content="按规程准时接班，核验加速器 200kW@25Hz 供束打靶状态", is_done=True, done_at=start, created_by="王健威"),
                ShiftTodo(shift_id=shift.id, due_at=start.replace(hour=14, minute=30), content="配合 SANS 谱仪科学家进行样品室剂量检测并借出便携辐射仪", is_done=False, created_by="王健威"),
                ShiftTodo(shift_id=shift.id, due_at=start.replace(hour=20, minute=0), content="靶站大厅天车吊装协调监护 (需提前通知水冷组与谱仪避让)", is_done=False, created_by="王健威"),
                ShiftTodo(shift_id=shift.id, due_at=(start + timedelta(days=1)).replace(hour=2, minute=0), content="配合大气中子谱仪关闭 11# 快门 (Shutter)，复核限位信号", is_done=False, created_by="王健威"),
            ]
        )
        db.add_all(
            [
                ShiftLog(shift_id=shift.id, occurred_at=start.replace(hour=16, minute=45), category="运行报警", target="多物理谱仪 (MPI)", content="重水保护气缓冲罐压力微幅攀升，已越过预警限 0.12 MPa，现场复核微漏点。", severity="重要", status="跟进中", author_name="王健威"),
                ShiftLog(shift_id=shift.id, occurred_at=start.replace(hour=15, minute=0), category="现场巡查", target="靶站综合大厅", content="15:00 现场例行巡检完成：未见异常，主回路温度压力平稳。", severity="一般", status="已闭环", author_name="王健威"),
                ShiftLog(shift_id=shift.id, occurred_at=start.replace(hour=14, minute=10), category="运行报警", target="低温样品环境 CCR", content="CCR-05 换样抽真空超 20 分钟保持在 72 Pa (>50 Pa 门槛)，启动止损拦截，未开分子泵。", severity="紧急", status="待处理", author_name="王健威"),
                ShiftLog(shift_id=shift.id, occurred_at=start, category="待办琐事", target="靶站中控室", content="接班完毕。加速器 200kW, 25Hz 供束打靶，谱仪正常实验。", severity="一般", status="已闭环", author_name="王征"),
            ]
        )

    if db.scalar(select(Event).limit(1)) is None:
        shift = db.scalar(select(DutyShift).order_by(DutyShift.id.desc()))
        events = [
            Event(event_no="EV-2026-001", title="CCR-05 换样抽速慢于规程 (>50Pa 门槛阻断)", device="低温CCR-05 / SANS", priority="紧急", reporter_name="王健威", stage="pending", notes="抽气20分钟仍处72 Pa，触发Stop-Work安全红线，未强启分子泵。现场检查密封法兰螺栓。", created_shift_id=shift.id if shift else None),
            Event(event_no="EV-2026-002", title="多物理谱仪重水保护气缓冲罐微幅渗漏", device="MPI重水系统", priority="重要", reporter_name="王征", stage="progress", notes="压力达到0.12 MPa高限，已调整一次背压阀，正在排查减压阀后管道接头。", created_shift_id=shift.id if shift else None),
            Event(event_no="EV-2026-003", title="大气中子谱仪屏蔽门行程限位跳变偶发误报", device="Atmospheric屏蔽门", priority="一般", reporter_name="王健威", stage="escalated", notes="已交由设备机械与联锁专业组处理，专业组将于明日清晨排班检修微动开关。", created_shift_id=shift.id if shift else None),
            Event(event_no="EV-2026-004", title="SANS 样品室 LakeShore 336 测温传感器通道偶发断线", device="LakeShore-336", priority="重要", reporter_name="王健威", stage="closed", notes="已重新压接航空插头引脚，经过 12 小时连续测温未再出现跳变，已闭环归档。", created_shift_id=shift.id if shift else None, closed_at=utcnow()),
            Event(event_no="EV-2026-005", title="靶站 2 号主循环冷水泵轴承振动偏高预警", device="靶站一次循环泵", priority="一般", reporter_name="王征", stage="closed", notes="专业水冷组到场加注润滑脂后振动幅值回落至合格区间，闭环。", created_shift_id=shift.id if shift else None, closed_at=utcnow()),
        ]
        db.add_all(events)
        db.flush()
        for event in events:
            closed_notes = {
                "EV-2026-004": "重新压接航空插头并做 12 小时连续测温，通道读数稳定，确认缺陷消除。",
                "EV-2026-005": "水冷组加注润滑脂并复测轴承振动，幅值回落至合格区间。",
            }
            for stage, operator_name, note in [
                ("pending", event.reporter_name, event.notes),
                *([("closed", event.reporter_name, closed_notes[event.event_no])] if event.stage == "closed" else []),
            ]:
                db.add(EventHistory(event_id=event.id, from_stage=None, to_stage=stage, operator_name=operator_name, note=note))
        db.flush()

    closed_events = db.scalars(
        select(Event).where(Event.stage == "closed").order_by(Event.id)
    ).all()
    for event in closed_events:
        if event.closed_at is None:
            event.closed_at = event.created_at
        if not db.scalar(select(EventHistory).where(EventHistory.event_id == event.id).limit(1)):
            db.add(
                EventHistory(
                    event_id=event.id,
                    from_stage=None,
                    to_stage="closed",
                    operator_name=event.reporter_name,
                    note=event.notes,
                    acted_at=event.closed_at,
                )
            )

    if db.scalar(select(NavItem).limit(1)) is None:
        db.add_all(
            [
                NavItem(name="SANS 小角散射谱仪控制台", description="EPICS OPI 实验采集、样品架运动与真空状态", target_path="opi://spectrometers/sans_main_view.bob", icon="ph-atom", categories=["spectrometer_run", "sample_env"], sort_order=10),
                NavItem(name="MPI 多物理谱仪综合主控界面", description="高压充气站、中子准直器、环境参数总览及工位视频", target_path="opi://spectrometers/mpi_overview.bob", icon="ph-intersect", categories=["spectrometer_run", "sample_env", "video_monitor"], sort_order=20),
                NavItem(name="Atmospheric 大气中子谱仪监视屏", description="辐射剂量、束流准直、谱仪快门控制状态", target_path="opi://spectrometers/atm_shield_view.bob", icon="ph-cloud-sun", categories=["spectrometer_run", "video_monitor"], sort_order=30),
                NavItem(name="靶站一次水冷回路中央监视", description="冷水机温度、靶体冷却水压差与电导率监测", target_path="http://monitor.csns.ihep.ac.cn/target_water", icon="ph-drop", categories=["target_control"], sort_order=40),
                NavItem(name="质子打靶功率与束流趋势大屏", description="打靶功率 (kW)、质子束截面分布与重频波形", target_path="http://monitor.csns.ihep.ac.cn/beam_trend", icon="ph-chart-line-up", categories=["target_control"], sort_order=50),
                NavItem(name="样品环境低温与强磁总览中枢", description="LakeShore 控制器阵列、超导磁体液氦液位", target_path="opi://sample_env/cryo_overview.bob", icon="ph-snowflake", categories=["sample_env", "target_control"], sort_order=60),
            ]
        )

    if db.scalar(select(LogTarget).limit(1)) is None:
        db.add_all(
            [
                LogTarget(name="SANS (小角散射)", is_system=True, sort_order=10),
                LogTarget(name="MR (反射物理谱仪)", is_system=True, sort_order=20),
                LogTarget(name="靶站水冷回路", is_system=True, sort_order=30),
            ]
        )

    if db.scalar(select(BeamForecast).where(BeamForecast.is_active.is_(True))) is None:
        db.add(
            BeamForecast(
                forecast_type="例行周维护",
                starts_at=datetime(2026, 11, 9, 8, 0),
                ends_at=datetime(2026, 11, 9, 16, 0),
                note="周一例行消缺：谱仪真空系统检漏、冷水机滤网清洗与配电例行检查。",
                is_active=True,
            )
        )

    if db.scalar(select(SpectrometerAssistCategory).limit(1)) is None:
        db.add_all(
            [
                SpectrometerAssistCategory(
                    category_id=category_id,
                    name=name,
                    badge=badge,
                    description=description,
                    sort_order=(index + 1) * 10,
                    is_system=True,
                )
                for index, (category_id, name, badge, description) in enumerate(ASSIST_CATEGORY_SEEDS)
            ]
        )
        db.flush()

    if db.scalar(select(SpectrometerAssist).limit(1)) is None:
        db.add_all(
            [
                SpectrometerAssist(
                    category_id=category_id,
                    action_content=action,
                    spectrometer=spectrometer,
                    param_condition=param_condition,
                    shift_type=shift_type,
                    count_val=count_val,
                    op_date=op_date,
                    operators=operators,
                    notes=notes,
                    created_at=datetime(2026, 10, 22, 8, 0) + timedelta(hours=index * 6),
                )
                for index, (
                    category_id,
                    action,
                    spectrometer,
                    param_condition,
                    shift_type,
                    count_val,
                    op_date,
                    operators,
                    notes,
                ) in enumerate(ASSIST_SEEDS)
            ]
        )

    db.commit()
    return db.scalar(select(DutyShift).where(DutyShift.status == "active").order_by(DutyShift.id.desc()))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_assist_param_column(db)
        ensure_shift_todo_columns(db)
        seed_database(db)
        # 种子数据落库后再回填班次主档案，确保首启时历史班次同样具备 shift_records 元数据
        ensure_shift_history_schema(db)
        if db.scalar(select(EpicsAlarmPV).limit(1)) is None:
            try:
                from scripts.import_pv_alarms import import_pv_alarms

                import_pv_alarms(db=db, verbose=False)
            except (FileNotFoundError, ValueError) as exc:
                print(f"[pv-alarm] 启动自动导入跳过: {exc}")
    yield


app = FastAPI(title="CSNS Ops-ERP API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 离线静态资源挂载：/static 前缀在全部业务路由之前注册，避免被通配路由遮挡
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class LoginPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=6, max_length=128)


class RegisterPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    real_name: str = Field(min_length=1, max_length=80)
    dept: str = Field(default="", max_length=160)
    password: str = Field(min_length=6, max_length=128)


class RolePayload(BaseModel):
    role: Literal["admin", "operator", "viewer"]
    status: str = "active"


class UserUpdatePayload(BaseModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=80)
    real_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    dept: Optional[str] = Field(default=None, max_length=160)
    role: Optional[Literal["admin", "operator", "viewer"]] = None
    status: Optional[Literal["active", "pending", "disabled"]] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class ShiftPayload(BaseModel):
    shift_type: Optional[Literal["day", "night"]] = None
    leader_name: Optional[str] = Field(default=None, max_length=80)
    operator_name: Optional[str] = Field(default=None, max_length=80)
    student_name: Optional[str] = Field(default=None, max_length=80)
    beam_mode: Optional[Literal["供束打靶", "停机维护"]] = None


class CheckinPayload(BaseModel):
    checker_name: str = Field(min_length=1, max_length=80)
    result: str = Field(default="现场巡查完成，靶站大厅及各重点区域未见异常。", max_length=500)


class AnomalyPayload(BaseModel):
    checker_name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=2000)


class TodoCreatePayload(BaseModel):
    due_at: datetime
    content: str = Field(min_length=1, max_length=500)


class TodoUpdatePayload(BaseModel):
    is_done: bool


class LogCreatePayload(BaseModel):
    category: str = Field(min_length=1, max_length=30)
    target: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=2000)
    severity: Literal["紧急", "重要", "一般"] = "一般"
    status: Literal["待处理", "跟进中", "已闭环"] = "待处理"
    author_name: Optional[str] = Field(default=None, max_length=80)
    shift_date: Optional[str] = Field(default=None, max_length=20)
    shift_type: Optional[str] = Field(default=None, max_length=10)
    occurred_at: Optional[str] = Field(default=None, max_length=20)


class LogStatusPayload(BaseModel):
    status: Literal["待处理", "跟进中", "已闭环"]


class LogUpdatePayload(BaseModel):
    """单条运行流水的管理员更正载荷（历史台账补录/修订）。"""

    category: Optional[str] = Field(default=None, min_length=1, max_length=30)
    target: Optional[str] = Field(default=None, min_length=1, max_length=160)
    content: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    severity: Optional[Literal["紧急", "重要", "一般"]] = None
    status: Optional[Literal["待处理", "跟进中", "已闭环"]] = None
    author_name: Optional[str] = Field(default=None, max_length=80)
    occurred_at: Optional[str] = Field(default=None, max_length=20)


class ShiftRecordRevisePayload(BaseModel):
    """管理员修订班次主档案（人员配置与工况）。"""

    shift_date: str = Field(min_length=8, max_length=20)
    shift_type: str = Field(min_length=2, max_length=10)
    shift_leader: Optional[str] = Field(default=None, max_length=50)
    shift_operator: Optional[str] = Field(default=None, max_length=50)
    shift_student: Optional[str] = Field(default=None, max_length=50)
    beam_mode: Optional[str] = Field(default=None, max_length=200)


class ForecastPayload(BaseModel):
    forecast_type: str = Field(min_length=1, max_length=80)
    starts_at: datetime
    ends_at: datetime
    note: str = Field(default="", max_length=2000)


class EventCreatePayload(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    device: str = Field(min_length=1, max_length=160)
    priority: Literal["紧急", "重要", "一般"] = "重要"
    reporter_name: Optional[str] = Field(default=None, max_length=80)
    stage: Literal["pending", "progress", "escalated"] = "pending"
    notes: str = Field(min_length=1, max_length=4000)


class EventTransitionPayload(BaseModel):
    note: str = Field(min_length=1, max_length=4000)
    operator_name: Optional[str] = Field(default=None, max_length=80)


class NavCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    target_path: str = Field(min_length=1, max_length=500)
    icon: str = Field(default="ph-compass-tool", max_length=80)
    categories: list[str] = Field(min_length=1)


class LogTargetCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    sort_order: Optional[int] = Field(default=None, ge=0, le=100000)


class HandoverPayload(BaseModel):
    next_leader_name: str = Field(min_length=1, max_length=80)


class SopSubmitPayload(BaseModel):
    template_code: str = Field(min_length=1, max_length=40)
    template_title: str = Field(min_length=1, max_length=240)
    steps: list[str] = Field(min_length=1)
    leader_name: str = Field(min_length=1, max_length=80)
    operator_name: str = Field(min_length=1, max_length=80)


class AssistCreatePayload(BaseModel):
    category_id: str = Field(min_length=1, max_length=60)
    action_content: str = Field(min_length=1, max_length=500)
    spectrometer: str = Field(min_length=1, max_length=160)
    param_condition: str = Field(default="", max_length=255)
    shift_type: Literal["白班", "夜班"]
    count_val: str = Field(default="1", max_length=20)
    op_date: str = Field(min_length=8, max_length=10)
    operators: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=1000)


class AssistUpdatePayload(BaseModel):
    category_id: str = Field(min_length=1, max_length=60)
    action_content: str = Field(min_length=1, max_length=500)
    spectrometer: str = Field(min_length=1, max_length=160)
    param_condition: str = Field(default="", max_length=255)
    shift_type: Literal["白班", "夜班"]
    count_val: str = Field(default="1", max_length=20)
    op_date: str = Field(min_length=8, max_length=10)
    operators: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=1000)


class AssistCategoryCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    badge: str = Field(default="📌", max_length=16)
    description: str = Field(default="", max_length=500)


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.real_name,
        "dept": user.dept,
        "role": user.role,
        "status": user.status,
        "regDate": user.registered_at.date().isoformat(),
    }


def shift_dict(shift: DutyShift) -> dict:
    return {
        "id": shift.id,
        "shift_date": shift.shift_date.isoformat(),
        "shift_type": shift.shift_type,
        "planned_start": iso(shift.planned_start),
        "planned_end": iso(shift.planned_end),
        "leader_name": shift.leader_name,
        "operator_name": shift.operator_name,
        "student_name": shift.student_name,
        "beam_mode": shift.beam_mode,
        "status": shift.status,
    }


def slot_dict(slot: PatrolSlot) -> dict:
    return {
        "id": slot.id,
        "slot_key": slot.slot_key,
        "time": slot.slot_key,
        "planned_at": iso(slot.planned_at),
        "status": slot.status,
        "result": slot.result,
        "anomaly_detail": slot.anomaly_detail,
        "checker": slot.checker_name or "--",
        "checked_at": display_minute(slot.checked_at) if slot.checked_at else "--",
    }


def todo_dict(todo: ShiftTodo) -> dict:
    return {
        "id": todo.id,
        "shiftId": todo.shift_id,
        "time": todo.due_at.strftime("%Y-%m-%d %H:%M"),
        "due_at": iso(todo.due_at),
        "text": todo.content,
        "done": todo.is_done,
        "doneAt": todo.done_at.strftime("%Y-%m-%d %H:%M") if todo.done_at else "",
        "createdBy": todo.created_by or "",
        "completedBy": todo.completed_by or "",
        "carriedOver": bool(todo.carried_over),
        "createdAt": iso(todo.created_at),
    }


def log_dict(log: ShiftLog) -> dict:
    return {
        "id": log.id,
        "time": display_time(log.occurred_at),
        "occurred_at": iso(log.occurred_at),
        "shiftDate": log.shift_date or (log.shift.shift_date.isoformat() if log.shift else None),
        "shiftType": log.shift_type or (shift_type_label(log.shift.shift_type) if log.shift else None),
        "category": log.category,
        "target": log.target,
        "content": log.content,
        "level": log.severity,
        "status": log.status,
        "author": log.author_name,
    }


def history_dict(history: EventHistory) -> dict:
    return {
        "id": history.id,
        "time": display_minute(history.acted_at),
        "acted_at": iso(history.acted_at),
        "operator": history.operator_name,
        "stage": STAGE_NAMES.get(history.to_stage, history.to_stage),
        "stage_key": history.to_stage,
        "note": history.note,
    }


def event_dict(event: Event) -> dict:
    closer = next(
        (item.operator_name for item in reversed(event.history) if item.to_stage == "closed"),
        "",
    )
    return {
        "id": event.event_no,
        "event_id": event.id,
        "title": event.title,
        "device": event.device,
        "priority": event.priority,
        "reporter": event.reporter_name,
        "time": display_minute(event.created_at),
        "created_at": iso(event.created_at),
        "closed_at": iso(event.closed_at),
        "closer": closer,
        "col": event.stage,
        "notes": event.notes,
        "history": [history_dict(item) for item in event.history],
    }


def nav_dict(item: NavItem) -> dict:
    return {
        "id": str(item.id),
        "name": item.name,
        "desc": item.description,
        "path": item.target_path,
        "icon": item.icon,
        "categories": item.categories or [],
    }


def log_target_dict(item: LogTarget) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "is_system": item.is_system,
        "sort_order": item.sort_order,
        "created_at": iso(item.created_at),
    }


def assist_category_dict(item: SpectrometerAssistCategory) -> dict:
    return {
        "id": item.category_id,
        "name": item.name,
        "badge": item.badge,
        "desc": item.description,
        "sort_order": item.sort_order,
        "is_system": item.is_system,
    }


def assist_dict(item: SpectrometerAssist) -> dict:
    return {
        "id": item.id,
        "category": item.category_id,
        "action": item.action_content,
        "spec": item.spectrometer,
        "param_condition": item.param_condition or "",
        "shift": item.shift_type,
        "count": item.count_val,
        "date": item.op_date,
        "operators": item.operators,
        "notes": item.notes,
        "created_shift_id": item.created_shift_id,
        "created_at": iso(item.created_at),
    }


def forecast_dict(forecast: BeamForecast) -> dict:
    return {
        "id": forecast.id,
        "type": forecast.forecast_type,
        "startTime": forecast.starts_at.strftime("%Y-%m-%dT%H:%M"),
        "endTime": forecast.ends_at.strftime("%Y-%m-%dT%H:%M"),
        "note": forecast.note,
    }


def get_active_shift(db: Session, lock: bool = False) -> DutyShift:
    statement = select(DutyShift).where(DutyShift.status == "active").order_by(DutyShift.id.desc())
    if lock:
        statement = statement.with_for_update()
    shift = db.scalar(statement)
    if shift is None:
        raise HTTPException(status_code=404, detail="当前没有活动班次")
    return shift


SHIFT_TYPE_LABELS = {"day": "白班", "night": "夜班"}
SHIFT_TYPE_KEYS = {"白班": "day", "夜班": "night", "day": "day", "night": "night"}
HISTORICAL_WRITE_DENIED = "权限不足：历史班次及已交接归档记录仅允许系统管理员修改"


def shift_type_label(shift_type: str) -> str:
    return SHIFT_TYPE_LABELS.get(shift_type, "夜班" if shift_type == "night" else "白班")


def shift_type_key(value: str) -> str:
    return SHIFT_TYPE_KEYS.get(value, "day")


def shift_record_dict(record: ShiftRecord) -> dict:
    return {
        "id": record.id,
        "shift_date": record.shift_date,
        "shift_type": record.shift_type,
        "shift_leader": record.shift_leader,
        "shift_operator": record.shift_operator,
        "shift_student": record.shift_student,
        "beam_mode": record.beam_mode,
        "is_handed_over": bool(record.is_handed_over),
        "revision_version": int(record.revision_version or 1),
        "last_revised_by": record.last_revised_by,
        "last_revised_at": record.last_revised_at,
    }


def sync_shift_record(db: Session, shift: DutyShift, handover: bool = False) -> ShiftRecord:
    """把当晚班次同步进 shift_records 主档案（以日期 + 班次为唯一键）。"""
    date_str = shift.shift_date.isoformat()
    label = shift_type_label(shift.shift_type)
    record = db.scalar(
        select(ShiftRecord).where(ShiftRecord.shift_date == date_str, ShiftRecord.shift_type == label)
    )
    if record is None:
        record = ShiftRecord(
            shift_date=date_str,
            shift_type=label,
            shift_leader=shift.leader_name,
            shift_operator=shift.operator_name,
            shift_student=shift.student_name or "",
            beam_mode=shift.beam_mode,
            is_handed_over=handover,
            revision_version=1,
        )
        db.add(record)
        db.flush()
        return record

    record.shift_leader = shift.leader_name
    record.shift_operator = shift.operator_name
    record.shift_student = shift.student_name or ""
    record.beam_mode = shift.beam_mode
    # 归档态与本次同步的班次实例保持一致：
    # 活动批次必须解除归档（同日可能另有已封班的旧批次），已封班批次则延续 / 置位归档标记
    if handover:
        record.is_handed_over = True
    elif shift.status == "active":
        record.is_handed_over = False
    elif shift.status == "closed":
        record.is_handed_over = bool(record.is_handed_over) or db.scalar(
            select(ShiftHandover).where(ShiftHandover.shift_id == shift.id)
        ) is not None
    return record


def get_shift_record(db: Session, shift_date: str, label: str) -> Optional[ShiftRecord]:
    return db.scalar(
        select(ShiftRecord).where(ShiftRecord.shift_date == shift_date, ShiftRecord.shift_type == label)
    )


def find_duty_shift(db: Session, shift_date: str, label: str) -> Optional[DutyShift]:
    """按业务日期 + 班次中文标签定位值班批次。"""
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        return None
    return db.scalar(
        select(DutyShift)
        .where(DutyShift.shift_date == target, DutyShift.shift_type == shift_type_key(label))
        .order_by(DutyShift.id.desc())
    )


def shift_is_locked(db: Session, shift: Optional[DutyShift] = None, shift_date: Optional[str] = None,
                    label: Optional[str] = None) -> bool:
    """判断目标班次是否已属于历史（早于今日）或已提交交接班。

    只有“正在执行（status=active）的当班批次”才不受日期比较影响：
    跨零点夜班的业务日期天然早于今日，不能仅因日历日期被判为历史归档。
    """
    if shift is not None:
        shift_date = shift_date or shift.shift_date.isoformat()
        label = label or shift_type_label(shift.shift_type)
    if not shift_date:
        return False
    label = label or "白班"
    record = get_shift_record(db, shift_date, label)
    if record is not None and record.is_handed_over:
        return True
    duty = shift
    if duty is None or duty.shift_date.isoformat() != shift_date or shift_type_label(duty.shift_type) != label:
        duty = find_duty_shift(db, shift_date, label)
    if duty is not None and duty.status == "active":
        return False
    try:
        target = date.fromisoformat(shift_date)
    except ValueError:
        return False
    # 未来日期不属于有效值班台账，同样视为只允许管理员（补录建档）写入
    return target != utcnow().date()


def assert_shift_writable(db: Session, user: User, shift: Optional[DutyShift] = None,
                          shift_date: Optional[str] = None, label: Optional[str] = None) -> bool:
    """历史/已封班记录的写操作守卫。

    返回 True 表示该写操作属于管理员对历史台账的修订，需要递增修订版本号。
    """
    # 显式指定的目标日期优先：避免把“活动批次”的身份套用到另一天的补录上
    if shift is not None and shift_date and shift.shift_date.isoformat() != shift_date:
        shift = None
    locked = shift_is_locked(db, shift=shift, shift_date=shift_date, label=label)
    if not locked:
        return False
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=HISTORICAL_WRITE_DENIED)
    return True


def ensure_shift_record(db: Session, shift_date: str, label: str,
                        duty: Optional[DutyShift] = None) -> ShiftRecord:
    """确保指定日期与班次存在主档案；缺失时按班次或默认口径建档。"""
    record = get_shift_record(db, shift_date, label)
    if record is not None:
        return record
    if duty is None:
        try:
            target_date = date.fromisoformat(shift_date)
        except ValueError:
            target_date = None
        if target_date is not None:
            duty = db.scalar(
                select(DutyShift)
                .where(DutyShift.shift_date == target_date, DutyShift.shift_type == shift_type_key(label))
                .order_by(DutyShift.id.desc())
            )
    if duty is not None:
        return sync_shift_record(db, duty)
    record = ShiftRecord(shift_date=shift_date, shift_type=label, revision_version=1)
    db.add(record)
    db.flush()
    return record


def bump_shift_revision(db: Session, shift_date: str, label: str, user: User,
                        duty: Optional[DutyShift] = None) -> Optional[ShiftRecord]:
    """管理员修订历史台账后递增版本号并记录修订留痕。"""
    record = ensure_shift_record(db, shift_date, label, duty)
    record.revision_version = int(record.revision_version or 1) + 1
    record.last_revised_by = user.real_name
    record.last_revised_at = utcnow().strftime("%Y-%m-%d %H:%M")
    return record


def register_admin_revision(db: Session, changed: bool, user: User,
                            shift: Optional[DutyShift] = None,
                            shift_date: Optional[str] = None,
                            label: Optional[str] = None) -> None:
    """守卫命中后统一登记修订留痕（仅管理员对历史/已交接批次生效）。"""
    if not changed:
        return
    if shift is not None:
        shift_date = shift_date or shift.shift_date.isoformat()
        label = label or shift_type_label(shift.shift_type)
    if not shift_date:
        return
    bump_shift_revision(db, shift_date, label or "白班", user, duty=shift)


def ensure_historical_shift(db: Session, shift_date: str, label: str) -> DutyShift:
    """为尚未建档的历史日期补齐一个已封班班次，便于管理员补录与修订留痕。"""
    key = shift_type_key(label)
    target_date = date.fromisoformat(shift_date)
    start, end = shift_window(target_date, key)
    previous = db.scalar(
        select(DutyShift).where(DutyShift.shift_date <= target_date).order_by(
            DutyShift.shift_date.desc(), DutyShift.id.desc()
        )
    )
    shift = DutyShift(
        shift_date=target_date,
        shift_type=key,
        planned_start=start,
        planned_end=end,
        leader_name=previous.leader_name if previous else "周科",
        operator_name=previous.operator_name if previous else "王健威",
        student_name=previous.student_name if previous else "",
        beam_mode=previous.beam_mode if previous else "供束打靶",
        status="closed",
        started_at=start,
        ended_at=end,
    )
    db.add(shift)
    db.flush()
    sync_shift_record(db, shift, handover=True)
    return shift


def resolve_shift_for_log(db: Session, shift_date: Optional[str], label: Optional[str]) -> DutyShift:
    """按日期 + 班次定位对应的 duty_shift 记录；缺档的历史日期自动补建封班批次。"""
    if shift_date and label:
        normalized = label if label in ("白班", "夜班") else shift_type_label(shift_type_key(label))
        key = shift_type_key(normalized)
        try:
            target_date = date.fromisoformat(shift_date)
        except ValueError:
            target_date = None
        if target_date is not None:
            found = db.scalar(
                select(DutyShift)
                .where(DutyShift.shift_date == target_date, DutyShift.shift_type == key)
                .order_by(DutyShift.id.desc())
            )
            if found is not None:
                return found
            active = db.scalar(select(DutyShift).where(DutyShift.status == "active").order_by(DutyShift.id.desc()))
            if active is not None and active.shift_date == target_date and active.shift_type == key:
                return active
            return ensure_historical_shift(db, shift_date, normalized)
    return get_active_shift(db)


def append_log(
    db: Session,
    shift: Optional[DutyShift],
    user: Optional[User],
    category: str,
    target: str,
    content: str,
    severity: str,
    log_status: str,
    author_name: str,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
) -> ShiftLog:
    if shift is None:
        shift = get_active_shift(db)
    log = ShiftLog(
        shift_id=shift.id,
        shift_date=shift.shift_date.isoformat(),
        shift_type=shift_type_label(shift.shift_type),
        category=category,
        target=target,
        content=content,
        severity=severity,
        status=log_status,
        author_user_id=user.id if user else None,
        author_name=author_name,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(log)
    return log


def next_event_no(db: Session, year: int) -> str:
    prefix = f"EV-{year}-"
    existing = db.scalars(select(Event.event_no).where(Event.event_no.like(f"{prefix}%"))).all()
    max_number = 0
    for event_no in existing:
        try:
            max_number = max(max_number, int(event_no.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return f"{prefix}{max_number + 1:03d}"


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ops-erp", "time": iso(utcnow())}


@app.post("/api/v1/auth/login")
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号尚未通过管理员审核")
    user.last_login_at = utcnow()
    db.commit()
    return {"access_token": create_access_token(user), "token_type": "bearer", "user": user_dict(user), "expires_in": 28800}


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterPayload, db: Session = Depends(get_db)):
    username = payload.username.strip()
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="该工号/用户名已存在")
    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        real_name=payload.real_name.strip(),
        dept=payload.dept.strip(),
        role="viewer",
        status="pending",
    )
    db.add(user)
    db.commit()
    return {"message": "注册成功，初始权限为只读观察员，请等待管理员审批", "user": user_dict(user)}


@app.get("/api/v1/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_dict(user)


@app.get("/api/v1/users")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [user_dict(item) for item in db.scalars(select(User).order_by(User.id)).all()]


@app.patch("/api/v1/users/{user_id}")
def update_user(user_id: int, payload: UserUpdatePayload, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    data = payload.model_dump(exclude_unset=True)
    if "username" in data:
        username = data["username"].strip()
        if not username:
            raise HTTPException(status_code=422, detail="用户名不能为空")
        duplicate = db.scalar(select(User).where(User.username == username, User.id != user_id))
        if duplicate:
            raise HTTPException(status_code=409, detail="该用户名已被其他账号占用")
        user.username = username
    if "real_name" in data:
        real_name = data["real_name"].strip()
        if not real_name:
            raise HTTPException(status_code=422, detail="姓名不能为空")
        user.real_name = real_name
    if "dept" in data:
        user.dept = data["dept"].strip()
    if "role" in data:
        user.role = data["role"]
    if "status" in data:
        user.status = data["status"]
    if data.get("password"):
        user.password_hash = hash_password(data["password"])

    self_losing_admin = user.id == admin.id and (user.role != "admin" or user.status != "active")
    if self_losing_admin:
        raise HTTPException(status_code=409, detail="不能取消当前登录管理员自身的权限或停用该账号")
    if user.role != "admin" or user.status != "active":
        if db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.status == "active")) == 0:
            db.rollback()
            raise HTTPException(status_code=409, detail="系统必须保留至少一名可用的管理员账号")

    db.commit()
    return user_dict(user)


@app.delete("/api/v1/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="不能删除当前登录的管理员账号")
    if user.role == "admin" and user.status == "active":
        remaining = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == "admin", User.status == "active", User.id != user.id
            )
        )
        if not remaining:
            raise HTTPException(status_code=409, detail="系统必须保留至少一名可用的管理员账号")
    db.delete(user)
    db.commit()


@app.get("/api/v1/shifts/current")
def current_shift(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shift = get_active_shift(db)
    record = sync_shift_record(db, shift)
    db.commit()
    payload = shift_dict(shift)
    payload.update(
        {
            "shift_label": shift_type_label(shift.shift_type),
            "is_handed_over": bool(record.is_handed_over),
            "revision_version": int(record.revision_version or 1),
            "last_revised_by": record.last_revised_by,
            "last_revised_at": record.last_revised_at,
            "is_locked": shift_is_locked(db, shift=shift, shift_date=record.shift_date,
                                         label=record.shift_type),
        }
    )
    return payload


@app.get("/api/v1/shift-data")
def shift_data(
    shift_date: str = Query(alias="date"),
    shift: str = Query(default="白班"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按日期 + 班次回溯历史台账：所有角色均可查看。"""
    try:
        parsed = date.fromisoformat(shift_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="date 必须为 YYYY-MM-DD 格式")
    label = shift if shift in ("白班", "夜班") else shift_type_label(shift_type_key(shift))
    key = shift_type_key(label)

    record = get_shift_record(db, shift_date, label)
    duty = db.scalar(
        select(DutyShift)
        .where(DutyShift.shift_date == parsed, DutyShift.shift_type == key)
        .order_by(DutyShift.id.desc())
    )

    slots = []
    if duty is not None:
        slots = [slot_dict(item) for item in sorted(duty.patrol_slots, key=lambda value: value.planned_at)]

    logs = [
        log_dict(item)
        for item in db.scalars(
            select(ShiftLog)
            .where(ShiftLog.shift_date == shift_date, ShiftLog.shift_type == label)
            .order_by(ShiftLog.occurred_at.desc())
        ).all()
    ]

    is_empty = record is None and duty is None and not logs
    locked = shift_is_locked(db, shift=duty, shift_date=shift_date, label=label)
    meta = shift_record_dict(record) if record is not None else {
        "id": None,
        "shift_date": shift_date,
        "shift_type": label,
        "shift_leader": duty.leader_name if duty else "",
        "shift_operator": duty.operator_name if duty else "",
        "shift_student": duty.student_name if duty else "",
        "beam_mode": duty.beam_mode if duty else "",
        "is_handed_over": False,
        "revision_version": 1,
        "last_revised_by": None,
        "last_revised_at": None,
    }
    meta["is_locked"] = locked
    meta["is_today"] = bool(duty is not None and duty.status == "active")
    meta["is_empty"] = is_empty

    return {
        "record": meta,
        "duty_shift": shift_dict(duty) if duty is not None else None,
        "patrol_slots": slots,
        "logs": logs,
        "is_empty": is_empty,
    }


@app.post("/api/v1/shift-records/revise")
def revise_shift_record(payload: ShiftRecordRevisePayload, user: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    """管理员修订班次主档案（人员/工况）：历史或已交接批次自动递增修订版本号。"""
    label = payload.shift_type if payload.shift_type in ("白班", "夜班") else shift_type_label(
        shift_type_key(payload.shift_type)
    )
    try:
        duty = resolve_shift_for_log(db, payload.shift_date, label)
        record = ensure_shift_record(db, payload.shift_date, label, duty=duty)
        data = payload.model_dump(exclude_unset=True)
        changed = False
        for field in ("shift_leader", "shift_operator", "shift_student", "beam_mode"):
            value = data.get(field)
            if value is not None and value != getattr(record, field):
                setattr(record, field, value)
                changed = True
        if duty is not None and duty.shift_date.isoformat() == payload.shift_date:
            for duty_field, record_field in (
                ("leader_name", "shift_leader"),
                ("operator_name", "shift_operator"),
                ("student_name", "shift_student"),
                ("beam_mode", "beam_mode"),
            ):
                value = data.get(record_field)
                if value is not None:
                    setattr(duty, duty_field, value)
        if changed and shift_is_locked(db, shift=duty, shift_date=payload.shift_date, label=label):
            bump_shift_revision(db, payload.shift_date, label, user, duty=duty)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return shift_record_dict(record)


@app.get("/api/v1/shifts/{shift_id}/patrol-slots")
def list_patrol_slots(shift_id: int, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shift = db.get(DutyShift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="班次不存在")
    return [slot_dict(item) for item in sorted(shift.patrol_slots, key=lambda value: value.planned_at)]


@app.patch("/api/v1/shifts/{shift_id}")
def update_shift(shift_id: int, payload: ShiftPayload, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    shift = db.get(DutyShift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="班次不存在")
    try:
        revision = assert_shift_writable(db, _user, shift=shift)
        data = payload.model_dump(exclude_unset=True)
        for field in ("leader_name", "operator_name", "student_name", "beam_mode"):
            if field in data and data[field] is not None:
                setattr(shift, field, data[field])
        shift_type = data.get("shift_type")
        if shift_type and shift_type != shift.shift_type:
            if db.scalar(select(ShiftHandover).where(ShiftHandover.shift_id == shift.id)):
                raise HTTPException(status_code=409, detail="已归档交接的班次不可切换班型")
            shift.shift_type = shift_type
            start, end = shift_window(shift.shift_date, shift_type)
            shift.planned_start, shift.planned_end = start, end
            for slot in list(shift.patrol_slots):
                db.delete(slot)
            db.flush()
            generate_slots(db, shift)
        sync_shift_record(db, shift)
        register_admin_revision(db, revision, _user, shift=shift)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return shift_dict(shift)


@app.post("/api/v1/shifts/{shift_id}/switch-type")
def switch_shift_type(shift_id: int, payload: ShiftPayload, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    if payload.shift_type is None:
        raise HTTPException(status_code=422, detail="必须指定 shift_type")
    return update_shift(shift_id, payload, _user, db)


@app.get("/api/v1/shifts/{shift_id}/todos")
def list_todos(shift_id: int, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 跨班滚动视图：不再按 shift_id 硬隔离，未闭环待办全量继承
    return [todo_dict(item) for item in list_visible_todos(db)]


@app.post("/api/v1/shifts/{shift_id}/todos", status_code=status.HTTP_201_CREATED)
def create_todo(shift_id: int, payload: TodoCreatePayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    if db.get(DutyShift, shift_id) is None:
        raise HTTPException(status_code=404, detail="班次不存在")
    todo = ShiftTodo(
        shift_id=shift_id,
        due_at=as_naive_utc(payload.due_at),
        content=payload.content.strip(),
        created_by=user.real_name,
        carried_over=False,
        created_at=utcnow(),
    )
    db.add(todo)
    db.commit()
    return todo_dict(todo)


@app.patch("/api/v1/todos/{todo_id}")
def update_todo(todo_id: int, payload: TodoUpdatePayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    todo = db.get(ShiftTodo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    todo.is_done = payload.is_done
    if payload.is_done:
        todo.done_at = utcnow()
        todo.completed_by = user.real_name
    else:
        todo.done_at = None
        todo.completed_by = None
        todo.carried_over = False
    db.commit()
    return todo_dict(todo)


@app.delete("/api/v1/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(todo_id: int, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    todo = db.get(ShiftTodo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    db.delete(todo)
    db.commit()


@app.get("/api/v1/shift-logs")
def list_shift_logs(
    shift_id: Optional[int] = Query(default=None),
    category: Optional[str] = Query(default=None),
    log_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if shift_id is None:
        shift_id = get_active_shift(db).id
    statement = select(ShiftLog).where(ShiftLog.shift_id == shift_id)
    if category and category != "ALL":
        statement = statement.where(ShiftLog.category == category)
    if log_status and log_status != "ALL":
        statement = statement.where(ShiftLog.status == log_status)
    return [log_dict(item) for item in db.scalars(statement.order_by(ShiftLog.occurred_at.desc()).limit(limit)).all()]


@app.post("/api/v1/shift-logs", status_code=status.HTTP_201_CREATED)
def create_shift_log(payload: LogCreatePayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    shift = resolve_shift_for_log(db, payload.shift_date, payload.shift_type)
    try:
        revision = assert_shift_writable(db, user, shift=shift)
        log = append_log(
            db,
            shift,
            user,
            payload.category,
            payload.target.strip(),
            payload.content.strip(),
            payload.severity,
            payload.status,
            payload.author_name or user.real_name,
            "manual",
        )
        # 管理员为历史班次补录时，业务时间需落在该班次日期内，保留补录语境的真实时间戳
        target_date = payload.shift_date or shift.shift_date.isoformat()
        log.shift_date = target_date
        log.shift_type = payload.shift_type or shift_type_label(shift.shift_type)
        occurred_at = parse_business_time(payload.occurred_at, target_date)
        if occurred_at is not None:
            log.occurred_at = occurred_at
        register_admin_revision(db, revision, user, shift=shift, shift_date=log.shift_date, label=log.shift_type)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return log_dict(log)


def parse_business_time(value: Optional[str], default_date: str) -> Optional[datetime]:
    """解析补录时间：支持 YYYY-MM-DD HH:mm / HH:mm，缺省日期取目标班次日期。"""
    if not value:
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(text, "%H:%M")
        base = datetime.strptime(default_date, "%Y-%m-%d")
        return base.replace(hour=parsed.hour, minute=parsed.minute)
    except ValueError:
        return None


@app.patch("/api/v1/shift-logs/{log_id}")
def update_shift_log(log_id: int, payload: LogStatusPayload, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    log = db.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="流水不存在")
    try:
        revision = assert_shift_writable(db, _user, shift=log.shift, shift_date=log.shift_date,
                                        label=log.shift_type)
        log.status = payload.status
        register_admin_revision(db, revision, _user, shift=log.shift, shift_date=log.shift_date,
                                label=log.shift_type)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return log_dict(log)


@app.put("/api/v1/shift-logs/{log_id}")
def update_shift_log_content(log_id: int, payload: LogUpdatePayload,
                             _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    """单条运行流水的更正入口：历史/已交接班次仅管理员可用。"""
    log = db.get(ShiftLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="流水不存在")
    try:
        revision = assert_shift_writable(db, _user, shift=log.shift, shift_date=log.shift_date,
                                        label=log.shift_type)
        data = payload.model_dump(exclude_unset=True)
        for field in ("category", "target", "content", "severity", "status", "author_name"):
            value = data.get(field)
            if value is not None:
                setattr(log, field, value)
        occurred_at = parse_business_time(data.get("occurred_at"), log.shift_date or "")
        if occurred_at is not None:
            log.occurred_at = occurred_at
        register_admin_revision(db, revision, _user, shift=log.shift, shift_date=log.shift_date,
                                label=log.shift_type)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return log_dict(log)


@app.post("/api/v1/patrol-slots/{slot_id}/checkin")
def checkin_patrol_slot(slot_id: int, payload: CheckinPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    try:
        slot = db.get(PatrolSlot, slot_id, with_for_update=True)
        if slot is None:
            raise HTTPException(status_code=404, detail="巡查槽不存在")
        revision = assert_shift_writable(db, user, shift=slot.shift)
        if slot.status == "checked":
            raise HTTPException(status_code=409, detail="该巡查槽已打卡")
        now = utcnow()
        slot.status = "checked"
        slot.result = payload.result.strip()
        slot.checker_name = payload.checker_name.strip()
        slot.checked_at = now
        slot.anomaly_detail = None
        append_log(
            db, slot.shift, user, "现场巡查", "靶站综合大厅",
            f"{slot.slot_key} 现场巡查完成：{slot.result}",
            "一般", "已闭环", slot.checker_name, "patrol_slot", str(slot.id),
        )
        register_admin_revision(db, revision, user, shift=slot.shift)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return slot_dict(slot)


@app.post("/api/v1/patrol-slots/{slot_id}/anomaly")
def report_patrol_anomaly(slot_id: int, payload: AnomalyPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    try:
        slot = db.get(PatrolSlot, slot_id, with_for_update=True)
        if slot is None:
            raise HTTPException(status_code=404, detail="巡查槽不存在")
        revision = assert_shift_writable(db, user, shift=slot.shift)
        now = utcnow()
        description = payload.description.strip()
        slot.status = "anomaly"
        slot.result = f"【异常】{description}"
        slot.anomaly_detail = description
        slot.checker_name = payload.checker_name.strip()
        slot.checked_at = now
        append_log(
            db, slot.shift, user, "运行报警", "靶站巡检发现",
            f"【巡检异常】{slot.slot_key} {description}",
            "重要", "待处理", slot.checker_name, "patrol_slot", str(slot.id),
        )
        event = Event(
            event_no=next_event_no(db, now.year),
            title=f"巡查发现：{description[:24]}",
            device="靶站大厅现场",
            priority="重要",
            reporter_user_id=user.id,
            reporter_name=slot.checker_name,
            stage="pending",
            notes=f"由 {slot.slot_key} 例行巡查打卡自动派生：{description} (巡检人: {slot.checker_name})",
            source_type="patrol_slot",
            source_patrol_slot_id=slot.id,
            created_shift_id=slot.shift_id,
        )
        db.add(event)
        db.flush()
        db.add(EventHistory(event_id=event.id, from_stage=None, to_stage="pending", operator_user_id=user.id, operator_name=slot.checker_name, note=f"巡查上报：{description}"))
        register_admin_revision(db, revision, user, shift=slot.shift)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"slot": slot_dict(slot), "event": event_dict(event)}


@app.post("/api/v1/patrol-slots/{slot_id}/reset")
def reset_patrol_slot(slot_id: int, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    slot = db.get(PatrolSlot, slot_id)
    if slot is None:
        raise HTTPException(status_code=404, detail="巡查槽不存在")
    try:
        revision = assert_shift_writable(db, _user, shift=slot.shift)
        slot.status = "pending"
        slot.result = "待到点巡查"
        slot.anomaly_detail = None
        slot.checker_name = None
        slot.checked_at = None
        register_admin_revision(db, revision, _user, shift=slot.shift)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return slot_dict(slot)


@app.get("/api/v1/events")
def list_events(
    stage: Optional[str] = Query(default=None),
    start_at: Optional[datetime] = Query(default=None),
    end_at: Optional[datetime] = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if stage is not None and stage not in STAGES:
        raise HTTPException(status_code=422, detail="无效的事件阶段")
    if start_at is not None and end_at is not None and as_naive_utc(end_at) <= as_naive_utc(start_at):
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    if (start_at is not None or end_at is not None) and user.role != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可按时间范围批量调取事件")

    statement = select(Event).options(selectinload(Event.history))
    if stage is not None:
        statement = statement.where(Event.stage == stage)
    if start_at is not None:
        statement = statement.where(Event.closed_at.is_not(None), Event.closed_at >= as_naive_utc(start_at))
    if end_at is not None:
        statement = statement.where(Event.closed_at.is_not(None), Event.closed_at <= as_naive_utc(end_at))
    order_column = Event.closed_at if stage == "closed" or start_at is not None or end_at is not None else Event.created_at
    events = db.scalars(statement.order_by(order_column.desc(), Event.id.desc())).all()
    result = [event_dict(item) for item in events]
    if result:
        digest = hashlib.sha256(
            "|".join(item["id"] for item in result).encode("utf-8")
        ).hexdigest().upper()
        for item in result:
            item["batch_audit_hash"] = digest
    return result


@app.get("/api/v1/events/{event_no}")
def get_event(event_no: str, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    event = db.scalar(
        select(Event).options(selectinload(Event.history)).where(Event.event_no == event_no)
    )
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return event_dict(event)


@app.post("/api/v1/events", status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreatePayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    try:
        shift = get_active_shift(db)
        event = Event(
            event_no=next_event_no(db, utcnow().year),
            title=payload.title.strip(),
            device=payload.device.strip(),
            priority=payload.priority,
            reporter_user_id=user.id,
            reporter_name=payload.reporter_name or user.real_name,
            stage=payload.stage,
            notes=payload.notes.strip(),
            source_type="manual",
            created_shift_id=shift.id,
        )
        db.add(event)
        db.flush()
        db.add(EventHistory(event_id=event.id, from_stage=None, to_stage=payload.stage, operator_user_id=user.id, operator_name=event.reporter_name, note=payload.notes.strip()))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return event_dict(event)


@app.post("/api/v1/events/{event_no}/transitions")
def transition_event(event_no: str, payload: EventTransitionPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    try:
        event = db.scalar(select(Event).where(Event.event_no == event_no).with_for_update())
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        current_index = STAGES.index(event.stage)
        if current_index >= len(STAGES) - 1:
            raise HTTPException(status_code=409, detail="事件已闭环，不能继续流转")
        next_stage = STAGES[current_index + 1]
        note = payload.note.strip()
        operator_name = payload.operator_name or user.real_name
        event.stage = next_stage
        if next_stage == "closed":
            event.closed_at = utcnow()
        db.add(EventHistory(event_id=event.id, from_stage=STAGES[current_index], to_stage=next_stage, operator_user_id=user.id, operator_name=operator_name, note=note))
        append_log(
            db, None, user, "待办琐事", event.device,
            f"【{event.event_no}流转至{STAGE_NAMES[next_stage]}】{note}",
            event.priority, "已闭环" if next_stage == "closed" else "跟进中", operator_name,
            "event", event.event_no,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(event)
    return event_dict(event)


@app.get("/api/v1/nav-items")
def list_nav_items(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(NavItem).where(NavItem.is_active.is_(True)).order_by(NavItem.sort_order, NavItem.id)).all()
    return [nav_dict(item) for item in items]


@app.get("/api/v1/log-targets")
def list_log_targets(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(LogTarget).order_by(LogTarget.sort_order, LogTarget.id)).all()
    return [log_target_dict(item) for item in items]


@app.get("/api/v1/epics-alarms")
def search_epics_alarms(q: Optional[str] = Query(default=None, max_length=150), db: Session = Depends(get_db)):
    statement = select(EpicsAlarmPV).order_by(EpicsAlarmPV.id)
    keyword = (q or '').strip()
    if keyword:
        pattern = f"%{keyword}%"
        statement = statement.where(
            or_(
                EpicsAlarmPV.pv_name.ilike(pattern),
                EpicsAlarmPV.description.ilike(pattern),
                EpicsAlarmPV.system_name.ilike(pattern),
                EpicsAlarmPV.subsystem.ilike(pattern),
            )
        ).limit(200)
    else:
        statement = statement.limit(15)
    return [
        {
            "id": item.id,
            "system_name": item.system_name,
            "subsystem": item.subsystem,
            "pv_name": item.pv_name,
            "description": item.description,
            "oncall_raw": item.oncall_raw,
        }
        for item in db.scalars(statement).all()
    ]


@app.get("/api/v1/epics-alarms/count")
def count_epics_alarms(db: Session = Depends(get_db)):
    return {"count": db.scalar(select(func.count()).select_from(EpicsAlarmPV)) or 0}


@app.get("/api/v1/spectrometer-assists/categories")
def list_assist_categories(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(SpectrometerAssistCategory).order_by(SpectrometerAssistCategory.sort_order, SpectrometerAssistCategory.id)
    ).all()
    return [assist_category_dict(item) for item in items]


@app.post("/api/v1/spectrometer-assists/categories", status_code=status.HTTP_201_CREATED)
def create_assist_category(
    payload: AssistCategoryCreatePayload,
    _user: User = Depends(require_operator_or_admin),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="分类名称不能为空")
    duplicate = db.scalar(
        select(SpectrometerAssistCategory).where(func.lower(SpectrometerAssistCategory.name) == name.lower())
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该业务分类已存在")
    max_order = db.scalar(select(func.max(SpectrometerAssistCategory.sort_order))) or 0
    category_id = f"cat_{utcnow().strftime('%Y%m%d%H%M%S%f')}"
    item = SpectrometerAssistCategory(
        category_id=category_id,
        name=name,
        badge=(payload.badge.strip() or "📌")[:16],
        description=payload.description.strip() or f"{name}板块相关谱仪与辅助系统现场作业台账记录。",
        sort_order=max_order + 10,
        is_system=False,
    )
    db.add(item)
    db.commit()
    return assist_category_dict(item)


@app.get("/api/v1/spectrometer-assists")
def list_spectrometer_assists(
    category: Optional[str] = Query(default=None, max_length=60),
    shift: Optional[Literal["白班", "夜班"]] = Query(default=None),
    keyword: Optional[str] = Query(default=None, max_length=200),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(SpectrometerAssist).order_by(SpectrometerAssist.id)
    if category and category != "all":
        statement = statement.where(SpectrometerAssist.category_id == category)
    if shift:
        statement = statement.where(SpectrometerAssist.shift_type == shift)
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip().lower()}%"
        statement = statement.where(
            or_(
                func.lower(SpectrometerAssist.action_content).like(pattern),
                func.lower(SpectrometerAssist.spectrometer).like(pattern),
                func.lower(SpectrometerAssist.param_condition).like(pattern),
                func.lower(SpectrometerAssist.operators).like(pattern),
                func.lower(SpectrometerAssist.notes).like(pattern),
                func.lower(SpectrometerAssist.op_date).like(pattern),
            )
        )
    return [assist_dict(item) for item in db.scalars(statement).all()]


@app.post("/api/v1/spectrometer-assists", status_code=status.HTTP_201_CREATED)
def create_spectrometer_assist(
    payload: AssistCreatePayload,
    _user: User = Depends(require_operator_or_admin),
    db: Session = Depends(get_db),
):
    category = db.scalar(
        select(SpectrometerAssistCategory).where(SpectrometerAssistCategory.category_id == payload.category_id)
    )
    if category is None:
        raise HTTPException(status_code=422, detail="协助业务分类不存在")
    try:
        parsed_date = datetime.strptime(payload.op_date.strip(), "%Y/%m/%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="操作时间格式必须为 YYYY/MM/DD") from exc
    operators = payload.operators.strip()
    if not operators:
        raise HTTPException(status_code=422, detail="请至少选择一位操作人")
    active_shift = db.scalar(select(DutyShift).where(DutyShift.status == "active").order_by(DutyShift.id.desc()))
    item = SpectrometerAssist(
        created_shift_id=active_shift.id if active_shift else None,
        category_id=category.category_id,
        action_content=payload.action_content.strip(),
        spectrometer=payload.spectrometer.strip(),
        param_condition=payload.param_condition.strip(),
        shift_type=payload.shift_type,
        count_val=payload.count_val.strip() or "1",
        op_date=parsed_date.strftime("%Y/%m/%d"),
        operators=operators,
        notes=payload.notes.strip(),
    )
    db.add(item)
    db.commit()
    return assist_dict(item)


@app.patch("/api/v1/spectrometer-assists/{assist_id}")
def update_spectrometer_assist(
    assist_id: int,
    payload: AssistUpdatePayload,
    user: User = Depends(require_operator_or_admin),
    db: Session = Depends(get_db),
):
    item = db.get(SpectrometerAssist, assist_id)
    if item is None:
        raise HTTPException(status_code=404, detail="协助记录不存在")

    if user.role != "admin":
        active_shift = db.scalar(select(DutyShift).where(DutyShift.status == "active").order_by(DutyShift.id.desc()))
        if active_shift is None or item.created_shift_id != active_shift.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该协助记录所属班次已封班归档，普通值班员无权修改，请联系管理员周科处理。",
            )

    category = db.scalar(
        select(SpectrometerAssistCategory).where(SpectrometerAssistCategory.category_id == payload.category_id)
    )
    if category is None:
        raise HTTPException(status_code=422, detail="协助业务分类不存在")
    try:
        parsed_date = datetime.strptime(payload.op_date.strip(), "%Y/%m/%d")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="操作时间格式必须为 YYYY/MM/DD") from exc
    operators = payload.operators.strip()
    if not operators:
        raise HTTPException(status_code=422, detail="请至少选择一位操作人")

    item.category_id = category.category_id
    item.action_content = payload.action_content.strip()
    item.spectrometer = payload.spectrometer.strip()
    item.param_condition = payload.param_condition.strip()
    item.shift_type = payload.shift_type
    item.count_val = payload.count_val.strip() or "1"
    item.op_date = parsed_date.strftime("%Y/%m/%d")
    item.operators = operators
    item.notes = payload.notes.strip()
    db.commit()
    return assist_dict(item)


@app.delete("/api/v1/spectrometer-assists/{assist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_spectrometer_assist(
    assist_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.get(SpectrometerAssist, assist_id)
    if item is None:
        raise HTTPException(status_code=404, detail="协助记录不存在")
    db.delete(item)
    db.commit()


@app.post("/api/v1/log-targets", status_code=status.HTTP_201_CREATED)
def create_log_target(payload: LogTargetCreatePayload, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="来源名称不能为空")
    duplicate = db.scalar(select(LogTarget).where(func.lower(LogTarget.name) == name.lower()))
    if duplicate:
        raise HTTPException(status_code=409, detail="该来源名称已存在")
    if payload.sort_order is None:
        max_order = db.scalar(select(func.max(LogTarget.sort_order))) or 0
        sort_order = max_order + 10
    else:
        sort_order = payload.sort_order
    item = LogTarget(name=name, is_system=False, sort_order=sort_order)
    db.add(item)
    db.commit()
    return log_target_dict(item)


@app.delete("/api/v1/log-targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_log_target(target_id: int, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(LogTarget, target_id)
    if item is None:
        raise HTTPException(status_code=404, detail="来源不存在")
    remaining = db.scalar(select(func.count()).select_from(LogTarget).where(LogTarget.id != target_id)) or 0
    if remaining == 0:
        raise HTTPException(status_code=409, detail="必须至少保留一个事件来源")
    db.delete(item)
    db.commit()


@app.post("/api/v1/nav-items", status_code=status.HTTP_201_CREATED)
def create_nav_item(payload: NavCreatePayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    categories = list(dict.fromkeys(payload.categories))
    invalid = [item for item in categories if item not in NAV_CATEGORIES]
    if invalid:
        raise HTTPException(status_code=422, detail=f"未知导航分类: {', '.join(invalid)}")
    item = NavItem(
        name=payload.name.strip(),
        description=payload.description.strip(),
        target_path=payload.target_path.strip(),
        icon=payload.icon.strip() or "ph-compass-tool",
        categories=categories,
        created_by=user.id,
    )
    db.add(item)
    db.commit()
    return nav_dict(item)


@app.delete("/api/v1/nav-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_nav_item(item_id: int, _user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    item = db.get(NavItem, item_id)
    if item is None or not item.is_active:
        raise HTTPException(status_code=404, detail="导航不存在")
    item.is_active = False
    db.commit()


@app.get("/api/v1/beam-forecasts/current")
def current_forecast(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    forecast = db.scalar(select(BeamForecast).where(BeamForecast.is_active.is_(True)).order_by(BeamForecast.id.desc()))
    if forecast is None:
        raise HTTPException(status_code=404, detail="尚未配置束流预告")
    return forecast_dict(forecast)


@app.put("/api/v1/beam-forecasts/current")
def update_forecast(payload: ForecastPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    starts_at = as_naive_utc(payload.starts_at)
    ends_at = as_naive_utc(payload.ends_at)
    if ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="结束时间必须晚于开始时间")
    try:
        active = db.scalars(select(BeamForecast).where(BeamForecast.is_active.is_(True))).all()
        for item in active:
            item.is_active = False
        forecast = BeamForecast(forecast_type=payload.forecast_type.strip(), starts_at=starts_at, ends_at=ends_at, note=payload.note.strip(), is_active=True, created_by=user.id)
        db.add(forecast)
        db.flush()
        append_log(
            db, None, user, "待办琐事", "加速器与靶站总控",
            f"更新束流维护预告：【{forecast.forecast_type}】{starts_at.strftime('%Y-%m-%d %H:%M')} ~ {ends_at.strftime('%Y-%m-%d %H:%M')}。备注：{forecast.note}",
            "重要", "已闭环", user.real_name, "beam_forecast", str(forecast.id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return forecast_dict(forecast)


@app.get("/api/v1/shifts/{shift_id}/handover-preview")
def handover_preview(shift_id: int, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shift = db.get(DutyShift, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="班次不存在")
    alarms = [log_dict(item) for item in db.scalars(select(ShiftLog).where(ShiftLog.shift_id == shift_id, ShiftLog.status != "已闭环").order_by(ShiftLog.occurred_at.desc())).all()]
    # 跨班继承：所有未完成待办都要在本班交接单上列明
    todos = [todo_dict(item) for item in db.scalars(select(ShiftTodo).where(ShiftTodo.is_done.is_(False)).order_by(ShiftTodo.due_at)).all()]
    return {"shift": shift_dict(shift), "unclosed_alarms": alarms, "open_todos": todos}


@app.post("/api/v1/shifts/{shift_id}/handovers")
def create_handover(shift_id: int, payload: HandoverPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    try:
        shift = db.get(DutyShift, shift_id, with_for_update=True)
        if shift is None:
            raise HTTPException(status_code=404, detail="班次不存在")
        assert_shift_writable(db, user, shift=shift)
        if db.scalar(select(ShiftHandover).where(ShiftHandover.shift_id == shift_id, ShiftHandover.status == "archived")):
            raise HTTPException(status_code=409, detail="该班次已完成交接归档")
        alarms = [log_dict(item) for item in db.scalars(select(ShiftLog).where(ShiftLog.shift_id == shift_id, ShiftLog.status != "已闭环")).all()]
        pending_todos = db.scalars(select(ShiftTodo).where(ShiftTodo.is_done.is_(False))).all()
        todos = [todo_dict(item) for item in pending_todos]
        # 严禁清空/删除备忘：仅将未完成项标记为跨班继承，供后续班次持续查看
        for item in pending_todos:
            item.carried_over = True
        handover = ShiftHandover(
            shift_id=shift.id,
            outgoing_leader_name=shift.leader_name,
            outgoing_operator_name=shift.operator_name,
            student_name=shift.student_name,
            next_leader_name=payload.next_leader_name.strip(),
            signed_by_user_id=user.id,
            unresolved_snapshot={"alarms": alarms, "todos": todos},
            status="archived",
        )
        db.add(handover)
        db.flush()
        shift.status = "closed"
        shift.ended_at = utcnow()
        append_log(
            db, shift, user, "待办琐事", "运行值班交接",
            f"电子交接班已归档：{shift.leader_name}/{shift.operator_name} → {handover.next_leader_name}；未闭环报警 {len(alarms)} 项，未完成待办 {len(todos)} 项。",
            "重要", "已闭环", user.real_name, "handover", str(handover.id),
        )
        sync_shift_record(db, shift, handover=True)
        following = create_following_shift(db, shift, handover.next_leader_name)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "id": handover.id,
        "status": handover.status,
        "signed_at": iso(handover.signed_at),
        "next_leader_name": handover.next_leader_name,
        "next_shift_id": following.id,
    }


@app.post("/api/v1/sop-executions", status_code=status.HTTP_201_CREATED)
def submit_sop(payload: SopSubmitPayload, user: User = Depends(require_operator_or_admin), db: Session = Depends(get_db)):
    steps = [step.strip() for step in payload.steps if step.strip()]
    if not steps:
        raise HTTPException(status_code=422, detail="SOP 步骤不能为空")
    try:
        shift = get_active_shift(db)
        signature = sign_payload([payload.template_code, shift.shift_date.isoformat(), payload.leader_name.strip(), payload.operator_name.strip(), *steps])
        execution = SopExecution(
            template_code=payload.template_code.strip(),
            template_title=payload.template_title.strip(),
            shift_id=shift.id,
            leader_name=payload.leader_name.strip(),
            operator_name=payload.operator_name.strip(),
            step_snapshot=steps,
            status="submitted",
            progress=100.0,
            signature_hash=signature,
        )
        db.add(execution)
        db.flush()
        append_log(
            db, shift, user, "谱仪协助", execution.template_title,
            f"现场双人复核完成：{execution.template_code}。所有安全前置门槛全部自检合格，责任人：{execution.leader_name} + {execution.operator_name}。",
            "一般", "已闭环", f"{execution.leader_name}+{execution.operator_name}", "sop_execution", str(execution.id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"id": execution.id, "signature_hash": signature, "submitted_at": iso(execution.submitted_at), "status": execution.status}


@app.get("/api/v1/dashboard/summary")
def dashboard_summary(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shift = get_active_shift(db)
    slots = db.scalars(select(PatrolSlot).where(PatrolSlot.shift_id == shift.id)).all()
    todos = list_visible_todos(db)
    unresolved_logs = db.scalar(select(func.count()).select_from(ShiftLog).where(ShiftLog.shift_id == shift.id, ShiftLog.status != "已闭环")) or 0
    stage_counts = {stage: db.scalar(select(func.count()).select_from(Event).where(Event.stage == stage)) or 0 for stage in STAGES}
    return {
        "shift": shift_dict(shift),
        "patrol": {"completed": sum(1 for item in slots if item.status != "pending"), "total": len(slots)},
        "todos": {"completed": sum(1 for item in todos if item.is_done), "total": len(todos)},
        "unresolved_logs": unresolved_logs,
        "events": stage_counts,
        "event_total": sum(stage_counts.values()),
    }


@app.get("/api/v1/bootstrap")
def bootstrap(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    shift = get_active_shift(db)
    forecast = db.scalar(select(BeamForecast).where(BeamForecast.is_active.is_(True)).order_by(BeamForecast.id.desc()))
    events = db.scalars(select(Event).options(selectinload(Event.history)).order_by(Event.created_at.desc())).all()
    nav_items = db.scalars(select(NavItem).where(NavItem.is_active.is_(True)).order_by(NavItem.sort_order, NavItem.id)).all()
    logs = db.scalars(select(ShiftLog).where(ShiftLog.shift_id == shift.id).order_by(ShiftLog.occurred_at.desc()).limit(200)).all()
    record = sync_shift_record(db, shift)
    db.commit()
    shift_payload = shift_dict(shift)
    shift_payload.update(
        {
            "shift_label": shift_type_label(shift.shift_type),
            "is_handed_over": bool(record.is_handed_over),
            "revision_version": int(record.revision_version or 1),
            "last_revised_by": record.last_revised_by,
            "last_revised_at": record.last_revised_at,
        }
    )
    todos = list_visible_todos(db)
    slots = sorted(shift.patrol_slots, key=lambda item: item.planned_at)
    record_payload = shift_record_dict(record)
    record_payload["is_locked"] = shift_is_locked(db, shift=shift, shift_date=record.shift_date,
                                                 label=record.shift_type)
    record_payload["is_today"] = shift.status == "active"
    return {
        "shift": shift_payload,
        "shift_record": record_payload,
        "patrol_slots": [slot_dict(item) for item in slots],
        "todos": [todo_dict(item) for item in todos],
        "logs": [log_dict(item) for item in logs],
        "events": [event_dict(item) for item in events],
        "nav_items": [nav_dict(item) for item in nav_items],
        "forecast": forecast_dict(forecast) if forecast else None,
        "stage_names": STAGE_NAMES,
    }


@app.get("/", include_in_schema=False)
def serve_index():
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html 不存在")
    # 单页应用入口禁用浏览器缓存，确保发版后内网客户端立即拿到最新页面
    return FileResponse(
        INDEX_FILE,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)
