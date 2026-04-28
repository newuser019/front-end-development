from flask import Blueprint, request, jsonify, Response
from db import get_db_connection
from utils import format_datetime, format_date, validate_numeric_range
from datetime import date, datetime
import mysql.connector
import csv
import io
from urllib.parse import quote

monitors_bp = Blueprint('monitors', __name__, url_prefix='/api/monitors')

# ===================== 获取列表（分页 + 搜索 + 类型筛选） =====================
@monitors_bp.route('/', methods=['GET'])
def get_monitors():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '')
    training_type = request.args.get('type', '')
    offset = (page - 1) * limit

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    base_sql = "SELECT * FROM monitors WHERE 1=1"
    params = []
    if search:
        base_sql += " AND patient_name LIKE %s"
        params.append(f'%{search}%')
    if training_type:
        base_sql += " AND training_type = %s"
        params.append(training_type)

    count_sql = f"SELECT COUNT(*) as total FROM ({base_sql}) as t"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()['total']

    data_sql = f"{base_sql} ORDER BY monitor_datetime DESC LIMIT %s OFFSET %s"
    cursor.execute(data_sql, params + [limit, offset])
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in rows:
        row['monitor_datetime'] = format_datetime(row['monitor_datetime'])

    return jsonify({
        'data': rows,
        'total': total,
        'page': page,
        'limit': limit,
        'totalPages': (total + limit - 1) // limit
    })

# ===================== 单条查询 =====================
@monitors_bp.route('/<int:monitor_id>', methods=['GET'])
def get_monitor(monitor_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM monitors WHERE id = %s", (monitor_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return jsonify({'error': '记录不存在'}), 404
    if row['monitor_datetime']:
        row['monitor_datetime'] = format_datetime(row['monitor_datetime'])
    return jsonify(row)

# ===================== 新增 =====================
@monitors_bp.route('/', methods=['POST'])
def add_monitor():
    data = request.json
    # ===================== 修复：移除 patient_name 必填 =====================
    required = ['patient_id', 'training_type', 'monitor_datetime']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必要字段: {field}'}), 400

        # ===================== 新增：数据校验 =====================
        try:
            # 血氧 0~100
            if 'spo2' in data:
                validate_numeric_range(data['spo2'], 0, 100, '血氧值')
            # 心率 0~250
            if 'heart_rate' in data:
                validate_numeric_range(data['heart_rate'], 0, 250, '心率')
            # 时长（分钟） 0~1440（一天最大分钟数）
            if 'duration_minutes' in data:
                validate_numeric_range(data['duration_minutes'], 0, 1440, '时长(分钟)')
            # 执行次数 0~999
            if 'execution_times' in data:
                validate_numeric_range(data['execution_times'], 0, 999, '执行次数')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

    patient_id = data['patient_id']

    # ===================== 核心修复：自动查询患者姓名、住院号 =====================
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT name, hospitalization_id FROM patients WHERE id = %s", (patient_id,))
    patient = cursor.fetchone()
    if not patient:
        cursor.close()
        conn.close()
        return jsonify({'error': '患者不存在'}), 400

    patient_name = patient['name']
    hospitalization_id = patient['hospitalization_id']

    # 生成记录编号
    record_no = data.get('record_no') or f"KF-{int(datetime.now().timestamp() * 1000)}"

    try:
        cursor.execute("""
            INSERT INTO monitors 
            (record_no, patient_id, patient_name, hospitalization_id, training_type,
             monitor_datetime, duration_minutes, execution_times, intensity, quality,
             cooperation, spo2, heart_rate, adverse_reaction, nurse_observation, nurse_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            record_no,
            patient_id,
            patient_name,  # 自动填充
            hospitalization_id,  # 自动填充
            data['training_type'],
            data['monitor_datetime'],
            data.get('duration_minutes'),
            data.get('execution_times'),
            data.get('intensity'),
            data.get('quality'),
            data.get('cooperation'),
            data.get('spo2'),
            data.get('heart_rate'),
            data.get('adverse_reaction'),
            data.get('nurse_observation'),
            data.get('nurse_name', '张楠')
        ))
        conn.commit()
        return jsonify({'id': cursor.lastrowid, 'message': '监测记录添加成功'}), 201
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({'error': f'数据库错误: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


# ===================== 更新 =====================
@monitors_bp.route('/<int:monitor_id>', methods=['PUT'])
def update_monitor(monitor_id):
    data = request.json

    # ===================== 新增：数据校验 =====================
    try:
        # 血氧 0~100
        if 'spo2' in data:
            validate_numeric_range(data['spo2'], 0, 100, '血氧值')
        # 心率 0~250
        if 'heart_rate' in data:
            validate_numeric_range(data['heart_rate'], 0, 250, '心率')
        # 时长（分钟） 0~1440
        if 'duration_minutes' in data:
            validate_numeric_range(data['duration_minutes'], 0, 1440, '时长(分钟)')
        # 执行次数 0~999
        if 'execution_times' in data:
            validate_numeric_range(data['execution_times'], 0, 999, '执行次数')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # 如果传了 patient_id，自动更新姓名和住院号
    patient_id = data.get('patient_id')
    auto_fields = {}

    if patient_id:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name, hospitalization_id FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        if not patient:
            cursor.close()
            conn.close()
            return jsonify({'error': '患者不存在'}), 400

        auto_fields['patient_name'] = patient['name']
        auto_fields['hospitalization_id'] = patient['hospitalization_id']
        cursor.close()
        conn.close()

    allowed_fields = ['patient_id', 'training_type',
                      'monitor_datetime', 'duration_minutes', 'execution_times', 'intensity',
                      'quality', 'cooperation', 'spo2', 'heart_rate', 'adverse_reaction',
                      'nurse_observation', 'nurse_name']

    updates = []
    values = []

    # 加入自动获取的字段
    if 'patient_name' in auto_fields:
        updates.append("patient_name = %s")
        values.append(auto_fields['patient_name'])
    if 'hospitalization_id' in auto_fields:
        updates.append("hospitalization_id = %s")
        values.append(auto_fields['hospitalization_id'])

    # 加入其他更新字段
    for field in allowed_fields:
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            values.append(data[field])

    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400

    values.append(monitor_id)
    sql = f"UPDATE monitors SET {', '.join(updates)} WHERE id = %s"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql, values)
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()

    if affected == 0:
        return jsonify({'error': '记录不存在'}), 404
    return jsonify({'message': '更新成功'})

# ===================== 单条删除 =====================
@monitors_bp.route('/<int:monitor_id>', methods=['DELETE'])
def delete_monitor(monitor_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM monitors WHERE id = %s", (monitor_id,))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    if affected == 0:
        return jsonify({'error': '记录不存在'}), 404
    return jsonify({'message': '删除成功'})

# ===================== 批量删除 =====================
@monitors_bp.route('/', methods=['DELETE'])
def delete_monitors():
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'error': '请提供要删除的ID列表'}), 400
    placeholders = ','.join(['%s'] * len(ids))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM monitors WHERE id IN ({placeholders})", ids)
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    conn.close()
    return jsonify({'message': f'成功删除 {deleted} 条记录'})

# ===================== 导出 CSV =====================
@monitors_bp.route('/export/csv', methods=['GET'])
def export_monitors_csv():
    try:
        search = request.args.get('search', '')
        training_type = request.args.get('type', '')

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        sql = "SELECT * FROM monitors WHERE 1=1"
        params = []
        if search:
            sql += " AND patient_name LIKE %s"
            params.append(f'%{search}%')
        if training_type:
            sql += " AND training_type = %s"
            params.append(training_type)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "记录编号", "患者ID", "患者姓名", "住院号", "训练类型", "监测时间",
            "时长(分钟)", "执行次数", "训练强度", "完成质量", "配合度",
            "血氧(%)", "心率(次/分)", "不良反应", "护士观察", "护士姓名"
        ])

        for r in rows:
            monitor_dt = r.get('monitor_datetime')
            if monitor_dt:
                if isinstance(monitor_dt, datetime):
                    monitor_dt = monitor_dt.strftime('%Y-%m-%d %H:%M:%S')
                monitor_dt = "'" + monitor_dt   # 加单引号防止Excel自动转换
            else:
                monitor_dt = ''

            writer.writerow([
                r.get('record_no', ''),
                r.get('patient_id', ''),
                r.get('patient_name', ''),
                r.get('hospitalization_id', ''),
                r.get('training_type', ''),
                monitor_dt,
                r.get('duration_minutes', ''),
                r.get('execution_times', ''),
                r.get('intensity', ''),
                r.get('quality', ''),
                r.get('cooperation', ''),
                r.get('spo2', ''),
                r.get('heart_rate', ''),
                r.get('adverse_reaction', ''),
                r.get('nurse_observation', ''),
                r.get('nurse_name', '')
            ])

        csv_data = output.getvalue()
        today = date.today()
        filename = f"监测记录_{today.strftime('%Y%m%d')}.csv"
        filename_encoded = quote(filename)

        return Response(
            b'\xef\xbb\xbf' + csv_data.encode('utf-8'),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"}
        )
    except Exception as e:
        print("导出错误:", e)
        return jsonify({"code": 500, "msg": f"导出失败: {str(e)}"}), 500