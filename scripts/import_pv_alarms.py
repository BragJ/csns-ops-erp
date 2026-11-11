from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import Base, SessionLocal, engine  # noqa: E402
from models import EpicsAlarmPV  # noqa: E402


EXCEL_NAME = 'pv_alarm_list.xlsx'
COLUMN_ALIASES = {
    'system_name': ('系统名称', '系统', '所属系统'),
    'subsystem': ('所属组别', '所属系统', '组别', '部件', '子系统'),
    'oncall_raw': ('oncall人员', 'oncall 人员', '联系人', 'oncall', '值班联系人'),
    'pv_name': ('报警PV', '报警pv', '报警 PV', 'PV变量名', 'PV', '测点'),
    'description': ('中文描述', '描述', '报警描述'),
}


def locate_excel(root: Optional[Path] = None) -> Path:
    base = root or ROOT_DIR
    for candidate in (base / EXCEL_NAME, base / 'data' / EXCEL_NAME):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f'未找到 {EXCEL_NAME}（已检查根目录与 data/ 目录）')


def normalize_name(value: object) -> str:
    return ''.join(str(value or '').replace('\u3000', ' ').split()).lower()


def pick_column(columns: list[object], aliases: tuple[str, ...]) -> object:
    normalized = {normalize_name(column): column for column in columns}
    for alias in aliases:
        found = normalized.get(normalize_name(alias))
        if found is not None:
            return found
    return None


def resolve_columns(frame: pd.DataFrame) -> dict[str, object]:
    mapping: dict[str, object] = {}
    missing: list[str] = []
    for field, aliases in COLUMN_ALIASES.items():
        column = pick_column(list(frame.columns), aliases)
        if column is None:
            missing.append(field)
        else:
            mapping[field] = column
    if missing:
        raise ValueError(f'Excel 缺少必要列: {missing}，实际表头: {list(frame.columns)}')
    return mapping


def read_workbook(path: Path) -> pd.DataFrame:
    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    frames: list[pd.DataFrame] = []
    for name, frame in sheets.items():
        if frame.empty or not any(str(column).strip() for column in frame.columns):
            continue
        try:
            resolve_columns(frame)
        except ValueError:
            print(f'[skip] 工作表 {name} 缺少 PV 台账表头，跳过')
            continue
        frames.append(frame)
    if not frames:
        raise ValueError('Excel 中没有可识别的 EPICS 报警 PV 工作表')
    return pd.concat(frames, ignore_index=True)


def clean_rows(frame: pd.DataFrame) -> list[dict[str, str]]:
    columns = resolve_columns(frame)
    work = frame.rename(columns={value: key for key, value in columns.items()}).copy()
    for field in ('system_name', 'subsystem', 'oncall_raw'):
        work[field] = work[field].ffill()
    for field in ('system_name', 'subsystem', 'pv_name', 'description', 'oncall_raw'):
        work[field] = work[field].fillna('').astype(str).str.strip()
    work = work[work['pv_name'] != '']

    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in work.to_dict('records'):
        pv_name = str(row['pv_name']).strip()
        if not pv_name or pv_name in seen:
            continue
        seen.add(pv_name)
        records.append(
            {
                'system_name': str(row['system_name'])[:100],
                'subsystem': str(row['subsystem'])[:100],
                'pv_name': pv_name[:150],
                'description': str(row['description'])[:255],
                'oncall_raw': str(row['oncall_raw']),
            }
        )
    return records


def upsert_records(db: Session, records: list[dict[str, str]]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    for row in records:
        item = db.scalar(select(EpicsAlarmPV).where(EpicsAlarmPV.pv_name == row['pv_name']))
        if item is None:
            db.add(EpicsAlarmPV(**row))
            inserted += 1
            continue
        changed = False
        for field, value in row.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed = True
        if changed:
            updated += 1
    db.commit()
    return inserted, updated


def import_pv_alarms(path: Optional[Path] = None, db: Optional[Session] = None, verbose: bool = True) -> dict[str, object]:
    excel_path = path or locate_excel()
    frame = read_workbook(excel_path)
    records = clean_rows(frame)
    owns_session = db is None
    session = db or SessionLocal()
    try:
        Base.metadata.create_all(bind=engine)
        inserted, updated = upsert_records(session, records)
        total = len(records)
        examples = session.scalars(select(EpicsAlarmPV).order_by(EpicsAlarmPV.id).limit(2)).all()
        if verbose:
            print(f'成功导入/更新 PV 测点总数量: {total}（新增 {inserted}，更新 {updated}）')
            for item in examples:
                print(
                    f'示例: {item.system_name} / {item.subsystem} / {item.pv_name} / '
                    f'{item.description} / {item.oncall_raw!r}'
                )
        return {'total': total, 'inserted': inserted, 'updated': updated, 'path': str(excel_path)}
    finally:
        if owns_session:
            session.close()


def main() -> int:
    try:
        import_pv_alarms()
    except (FileNotFoundError, ValueError) as exc:
        print(f'导入失败: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
