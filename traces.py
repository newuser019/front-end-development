from flask import Blueprint, request, jsonify, Response
from db import get_db_connection
from datetime import date, datetime
import mysql.connector
import csv
import io
from urllib.parse import quote

traces_bp = Blueprint('traces', __name__, url_prefix='/api/traces')

# ===================== 获取列表（分页 + 搜索 + 类型筛选） =====================
@traces_bp.route('/', methods=['GET'])
def get_traces():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '')
    analysis_type = request.args.get('type', '')
    offset = (page - 1) * limit

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 带表别名，用于子查询
    base_sql = "FROM traces t WHERE 1=1"
    params = []
    if search:
        base_sql += " AND t.theme LIKE %s"
        params.append(f'%{search}%')
    if analysis_type:
        base_sql += " AND t.analysis_type = %s"
        params.append(analysis_type)

    # 统计总数（不需要子查询，提高性能）
    count_sql = f"SELECT COUNT(*) as total {base_sql}"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()['total']

    # 数据查询：增加患者姓名字段
    data_sql = f"""
        SELECT t.*,
            (SELECT GROUP_CONCAT(p.name SEPARATOR ',') 
             FROM patients p 
             WHERE FIND_IN_SET(p.id, t.patient_ids)) AS patient_names
        {base_sql}
        ORDER BY t.created_at DESC
        LIMIT %s OFFSET %s
    """
    cursor.execute(data_sql, params + [limit, offset])
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in rows:
        if row.get('created_at'):
            row['created_at'] = row['created_at'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(row['created_at'], datetime) else row['created_at']
        # 将 patient_ids 字符串转列表（前端需要）
        if row.get('patient_ids') and isinstance(row['patient_ids'], str):
            row['patient_ids'] = row['patient_ids'].split(',') if ',' in row['patient_ids'] else [row['patient_ids']]

    return jsonify({
        'data': rows,
        'total': total,
        'page': page,
        'limit': limit,
        'totalPages': (total + limit - 1) // limit
    })

# ===================== 单条查询 =====================
@traces_bp.route('/<int:trace_id>', methods=['GET'])
@traces_bp.route('/<int:trace_id>', methods=['GET'])
def get_trace(trace_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT t.*,
            (SELECT GROUP_CONCAT(p.name SEPARATOR ',')
             FROM patients p
             WHERE FIND_IN_SET(p.id, t.patient_ids)) AS patient_names
        FROM traces t
        WHERE t.id = %s
    """, (trace_id,))
    row = cursor.fetchone()
    # ... 后续处理
    cursor.close()
    conn.close()
    if not row:
        return jsonify({'error': '记录不存在'}), 404
    if row.get('patient_ids') and isinstance(row['patient_ids'], str):
        row['patient_ids'] = row['patient_ids'].split(',') if ',' in row['patient_ids'] else [row['patient_ids']]
    if row.get('created_at'):
        row['created_at'] = row['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    return jsonify(row)

# ===================== 新增 =====================
@traces_bp.route('/', methods=['POST'])
def add_trace():
    data = request.json
    if not data.get('theme'):
        return jsonify({'error': '分析主题不能为空'}), 400

    patient_ids = data.get('patient_ids')
    if isinstance(patient_ids, list):
        patient_ids = ','.join(str(pid) for pid in patient_ids)
    elif patient_ids and isinstance(patient_ids, str):
        patient_ids = patient_ids
    else:
        patient_ids = None

    analysis_no = data.get('analysis_no') or f"SY-{int(datetime.now().timestamp()*1000)}"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO traces 
            (analysis_no, theme, analysis_type, target_type, patient_ids,
             diagnosis_filter, start_date, end_date, behavior_type,
             effect_index, analysis_dimension, output_format, purpose)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            analysis_no, data['theme'], data.get('analysis_type'), data.get('target_type'),
            patient_ids, data.get('diagnosis_filter'), data.get('start_date'),
            data.get('end_date'), data.get('behavior_type'), data.get('effect_index'),
            data.get('analysis_dimension'), data.get('output_format'), data.get('purpose')
        ))
        conn.commit()
        return jsonify({'id': cursor.lastrowid, 'message': '溯源分析创建成功'}), 201
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({'error': f'数据库错误: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()

# ===================== 更新 =====================
@traces_bp.route('/<int:trace_id>', methods=['PUT'])
def update_trace(trace_id):
    data = request.json
    allowed_fields = ['theme', 'analysis_type', 'target_type', 'diagnosis_filter',
                      'start_date', 'end_date', 'behavior_type', 'effect_index',
                      'analysis_dimension', 'output_format', 'purpose', 'status']
    updates = []
    values = []
    for field in allowed_fields:
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            values.append(data[field])
    if 'patient_ids' in data:
        pid = data['patient_ids']
        if isinstance(pid, list):
            pid_str = ','.join(str(p) for p in pid)
        else:
            pid_str = pid
        updates.append("patient_ids = %s")
        values.append(pid_str)
    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400
    values.append(trace_id)
    sql = f"UPDATE traces SET {', '.join(updates)} WHERE id = %s"
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
@traces_bp.route('/<int:trace_id>', methods=['DELETE'])
def delete_trace(trace_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM traces WHERE id = %s", (trace_id,))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    if affected == 0:
        return jsonify({'error': '记录不存在'}), 404
    return jsonify({'message': '删除成功'})

# ===================== 批量删除 =====================
@traces_bp.route('/', methods=['DELETE'])
def delete_traces():
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'error': '请提供要删除的ID列表'}), 400
    placeholders = ','.join(['%s'] * len(ids))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM traces WHERE id IN ({placeholders})", ids)
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    conn.close()
    return jsonify({'message': f'成功删除 {deleted} 条记录'})

# ===================== 导出 CSV =====================
@traces_bp.route('/export/csv', methods=['GET'])
def export_traces_csv():
    try:
        search = request.args.get('search', '')
        analysis_type = request.args.get('type', '')

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        sql = "SELECT * FROM traces WHERE 1=1"
        params = []
        if search:
            sql += " AND theme LIKE %s"
            params.append(f'%{search}%')
        if analysis_type:
            sql += " AND analysis_type = %s"
            params.append(analysis_type)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "分析编号", "分析主题", "分析类型", "目标对象", "关联患者ID",
            "诊断筛选", "开始日期", "结束日期", "康复行为类型", "效果指标",
            "分析维度", "输出格式", "目的说明", "状态", "创建人", "创建时间"
        ])

        for r in rows:
            start = r.get('start_date')
            end = r.get('end_date')
            if start:
                start = "'" + (start.strftime('%Y-%m-%d') if isinstance(start, date) else start)
            if end:
                end = "'" + (end.strftime('%Y-%m-%d') if isinstance(end, date) else end)
            created_at = r.get('created_at')
            if created_at:
                created_at = created_at.strftime('%Y-%m-%d %H:%M:%S') if isinstance(created_at, datetime) else created_at

            writer.writerow([
                r.get('analysis_no', ''),
                r.get('theme', ''),
                r.get('analysis_type', ''),
                r.get('target_type', ''),
                r.get('patient_ids', ''),
                r.get('diagnosis_filter', ''),
                start,
                end,
                r.get('behavior_type', ''),
                r.get('effect_index', ''),
                r.get('analysis_dimension', ''),
                r.get('output_format', ''),
                r.get('purpose', ''),
                r.get('status', ''),
                r.get('created_by', ''),
                created_at
            ])

        csv_data = output.getvalue()
        today = date.today()
        filename = f"溯源分析_{today.strftime('%Y%m%d')}.csv"
        filename_encoded = quote(filename)

        return Response(
            b'\xef\xbb\xbf' + csv_data.encode('utf-8'),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"}
        )
    except Exception as e:
        print("导出错误:", e)
        return jsonify({"code": 500, "msg": f"导出失败: {str(e)}"}), 500