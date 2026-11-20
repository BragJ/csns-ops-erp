from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


# 全局唯一的业务时区：靶站谱仪运行组统一按北京时间（UTC+8）记台账
CN_TZ = timezone(timedelta(hours=8))


def get_beijing_now() -> datetime:
    """返回不带 tzinfo 但数值严格为东八区（北京时间）的 datetime。

    统一入库口径：SQLite 用 DateTime 存 naive 值，这里直接给出北京时间的
    naive 数值，读取时无需再做任何时区换算，避免 UTC 与本地时间互相抵消出错。
    """
    return datetime.now(CN_TZ).replace(tzinfo=None)


def get_beijing_str(fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.now(CN_TZ).strftime(fmt)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    real_name: Mapped[str] = mapped_column(String(80), nullable=False)
    dept: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DutyShift(Base):
    __tablename__ = "duty_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, default=lambda: get_beijing_now().date(), index=True, nullable=False)
    shift_type: Mapped[str] = mapped_column(String(20), default="day", nullable=False)
    planned_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    planned_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    leader_name: Mapped[str] = mapped_column(String(80), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(80), nullable=False)
    student_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    beam_mode: Mapped[str] = mapped_column(String(40), default="供束打靶", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    patrol_slots: Mapped[list["PatrolSlot"]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    todos: Mapped[list["ShiftTodo"]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    logs: Mapped[list["ShiftLog"]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    handovers: Mapped[list["ShiftHandover"]] = relationship(back_populates="shift", cascade="all, delete-orphan")


class PatrolSlot(Base):
    __tablename__ = "patrol_slots"
    __table_args__ = (UniqueConstraint("shift_id", "slot_key", name="uq_patrol_shift_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("duty_shifts.id", ondelete="CASCADE"), index=True, nullable=False)
    shift_date: Mapped[Optional[str]] = mapped_column(String(20), index=True, nullable=True)
    shift_type: Mapped[Optional[str]] = mapped_column(String(10), index=True, nullable=True)
    slot_key: Mapped[str] = mapped_column(String(40), nullable=False)
    planned_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    result: Mapped[str] = mapped_column(Text, default="待到点巡查", nullable=False)
    anomaly_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checker_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    shift: Mapped["DutyShift"] = relationship(back_populates="patrol_slots")


class ShiftRecord(Base):
    """班次主档案：以 (shift_date, shift_type) 唯一标识一个历史班次台账。"""

    __tablename__ = "shift_records"
    __table_args__ = (UniqueConstraint("shift_date", "shift_type", name="uq_shift_record_date_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_date: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    shift_type: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    shift_leader: Mapped[str] = mapped_column(String(50), default="周科", nullable=False)
    shift_operator: Mapped[str] = mapped_column(String(50), default="王健威", nullable=False)
    shift_student: Mapped[str] = mapped_column(String(50), default="张超月", nullable=False)
    beam_mode: Mapped[str] = mapped_column(
        String(200), default="加速器 200kW, 25Hz 供束打靶，谱仪正常实验", nullable=False
    )
    is_handed_over: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    revision_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_revised_by: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_revised_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class ShiftTodo(Base):
    __tablename__ = "shift_todos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("duty_shifts.id", ondelete="CASCADE"), index=True, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    completed_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    carried_over: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)

    shift: Mapped["DutyShift"] = relationship(back_populates="todos")


class ShiftLog(Base):
    __tablename__ = "shift_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("duty_shifts.id", ondelete="CASCADE"), index=True, nullable=False)
    shift_date: Mapped[Optional[str]] = mapped_column(String(20), index=True, nullable=True)
    shift_type: Mapped[Optional[str]] = mapped_column(String(10), index=True, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    target: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="一般", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="待处理", index=True, nullable=False)
    author_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    shift: Mapped["DutyShift"] = relationship(back_populates="logs")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_no: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    device: Mapped[str] = mapped_column(String(160), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="重要", index=True, nullable=False)
    reporter_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reporter_name: Mapped[str] = mapped_column(String(80), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source_patrol_slot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patrol_slots.id", ondelete="SET NULL"), nullable=True)
    created_shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("duty_shifts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    history: Mapped[list["EventHistory"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="EventHistory.id"
    )


class EventHistory(Base):
    __tablename__ = "event_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=False)
    from_stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    operator_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operator_name: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    acted_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="history")


class NavItem(Base):
    __tablename__ = "nav_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_path: Mapped[str] = mapped_column(String(500), nullable=False)
    icon: Mapped[str] = mapped_column(String(80), default="ph-compass-tool", nullable=False)
    categories: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, onupdate=get_beijing_now, nullable=False)


class LogTarget(Base):
    __tablename__ = "log_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)


class BeamForecast(Base):
    __tablename__ = "beam_forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_type: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, onupdate=get_beijing_now, nullable=False)


class SopExecution(Base):
    __tablename__ = "sop_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_code: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    template_title: Mapped[str] = mapped_column(String(240), nullable=False)
    shift_id: Mapped[Optional[int]] = mapped_column(ForeignKey("duty_shifts.id", ondelete="SET NULL"), nullable=True)
    leader_name: Mapped[str] = mapped_column(String(80), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(80), nullable=False)
    step_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="submitted", index=True, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)


class ShiftHandover(Base):
    __tablename__ = "shift_handovers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("duty_shifts.id", ondelete="CASCADE"), index=True, nullable=False)
    outgoing_leader_name: Mapped[str] = mapped_column(String(80), nullable=False)
    outgoing_operator_name: Mapped[str] = mapped_column(String(80), nullable=False)
    student_name: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    next_leader_name: Mapped[str] = mapped_column(String(80), nullable=False)
    signed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    unresolved_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="archived", index=True, nullable=False)

    shift: Mapped["DutyShift"] = relationship(back_populates="handovers")


class SpectrometerAssistCategory(Base):
    __tablename__ = "spectrometer_assist_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[str] = mapped_column(String(60), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    badge: Mapped[str] = mapped_column(String(16), default="📌", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)


class SpectrometerAssist(Base):
    __tablename__ = "spectrometer_assists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_shift_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("duty_shifts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("spectrometer_assist_categories.category_id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    action_content: Mapped[str] = mapped_column(Text, nullable=False)
    spectrometer: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    param_condition: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    shift_type: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    count_val: Mapped[str] = mapped_column(String(20), default="1", nullable=False)
    op_date: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    operators: Mapped[str] = mapped_column(String(300), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, index=True, nullable=False)


class EpicsAlarmPV(Base):
    __tablename__ = "epics_alarm_pvs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    subsystem: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    pv_name: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    oncall_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=get_beijing_now, nullable=False)
