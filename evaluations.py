from flask import Blueprint, request, jsonify, Response
from db import get_db_connection
from utils import format_date, validate_numeric_range
from datetime import date, datetime
import mysql.connector
import csv
import io
from urllib.parse import quote

evaluations_bp = Blueprint('evaluations', __name__, url_prefix='/api/evaluations')

# ===================== 获取列表（分页 + 搜索 + 结果筛选） =====================
@evaluations_bp.route('/', methods=['GET'])
def get_evaluations():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '')
    result_filter = request.args.get('result', '')
    offset = (page - 1) * limit

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    base_sql = "SELECT * FROM evaluations WHERE 1=1"
    params = []
    if search:
        base_sql += " AND patient_name LIKE %s"
        params.append(f'%{search}%')
    if result_filter:
        base_sql += " AND result = %s"
        params.append(result_filter)

    count_sql = f"SELECT COUNT(*) as total FROM ({base_sql}) as t"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()['total']

    data_sql = f"{base_sql} ORDER BY evaluate_date DESC LIMIT %s OFFSET %s"
    cursor.execute(data_sql, params + [limit, offset])
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in rows:
        row['evaluate_date'] = format_date(row['evaluate_date'])

    return jsonify({
        'data': rows,
        'total': total,
        'page': page,
        'limit': limit,
        'totalPages': (total + limit - 1) // limit
    })

# ===================== 单条查询 =====================
@evaluations_bp.route('/<int:eval_id>', methods=['GET'])
def get_evaluation(eval_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM evaluations WHERE id = %s", (eval_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return jsonify({'error': '记录不存在'}), 404
    if row['evaluate_date']:
        row['evaluate_date'] = format_date(row['evaluate_date'])
    return jsonify(row)

# ===================== 新增 =====================
@evaluations_bp.route('/', methods=['POST'])
def add_evaluation():
    data = request.json
    required = ['patient_id', 'evaluate_date']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必要字段: {field}'}), 400

    # 只获取一次数据库连接，整个函数复用
    conn = get_db_connection()
    if conn is None:
        return jsonify({'error': '数据库连接失败'}), 500

    try:
        # 1. 自动查询患者姓名
        patient_id = data['patient_id']
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name FROM patients WHERE id = %s", (patient_id,))
        patient = cursor.fetchone()
        cursor.close()
        if not patient:
            return jsonify({'error': '患者不存在'}), 400
        patient_name = patient['name']

        # 2. 数据校验（原代码不变）
        try:
            if 'spo2' in data:
                validate_numeric_range(data['spo2'], 0, 100, '血氧值')
            if 'respiratory_rate' in data:
                validate_numeric_range(data['respiratory_rate'], 0, 100, '呼吸频率')
            if 'vital_capacity' in data:
                validate_numeric_range(data['vital_capacity'], 0, 10000, '肺活量')
            if 'mip' in data:
                validate_numeric_range(data['mip'], 0, 200, '最大吸气压')
            if 'six_min_walk' in data:
                validate_numeric_range(data['six_min_walk'], 0, 1000, '6分钟步行距离')
            if 'pain_score' in data:
                validate_numeric_range(data['pain_score'], 0, 10, '疼痛评分')
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        # 3. 生成评估编号（复用 conn，不再获取新连接）
        evaluate_no = data.get('evaluate_no')
        if not evaluate_no:
            eval_date = datetime.strptime(data['evaluate_date'], '%Y-%m-%d')
            current_year = eval_date.year
            cursor = conn.cursor()
            query_sql = """
                SELECT MAX(CAST(SUBSTRING_INDEX(evaluate_no, '-', -1) AS UNSIGNED)) 
                FROM evaluations 
                WHERE evaluate_no LIKE %s
            """
            cursor.execute(query_sql, (f"PG-{current_year}-%",))
            max_seq = cursor.fetchone()[0]
            cursor.close()
            new_seq = max_seq + 1 if max_seq else 1
            evaluate_no = f"PG-{current_year}-{new_seq:03d}"

        # 4. 插入数据
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO evaluations 
            (evaluate_no, patient_id, patient_name, evaluate_date, spo2, respiratory_rate,
             vital_capacity, mip, six_min_walk, pain_score, cough_effectiveness,
             sputum_property, activity_endurance, result, analysis_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            evaluate_no, patient_id, patient_name, data['evaluate_date'],
            data.get('spo2'), data.get('respiratory_rate'), data.get('vital_capacity'),
            data.get('mip'), data.get('six_min_walk'), data.get('pain_score'),
            data.get('cough_effectiveness'), data.get('sputum_property'),
            data.get('activity_endurance'), data.get('result'), data.get('analysis_note')
        ))
        conn.commit()
        return jsonify({'id': cursor.lastrowid, 'message': '评估记录添加成功'}), 201

    except mysql.connector.IntegrityError as e:
        conn.rollback()
        if "Duplicate entry" in str(e):
            return jsonify({'error': '评估编号已存在'}), 400
        else:
            return jsonify({'error': f'数据库错误: {str(e)}'}), 500
    except mysql.connector.Error as e:
        conn.rollback()
        return jsonify({'error': f'数据库错误: {str(e)}'}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if conn:
            conn.close()

# ===================== 更新 =====================
@evaluations_bp.route('/<int:eval_id>', methods=['PUT'])
def update_evaluation(eval_id):
    data = request.json

    # ===================== 新增：数据校验 =====================
    try:
        # 血氧 0~100
        if 'spo2' in data:
            validate_numeric_range(data['spo2'], 0, 100, '血氧值')
        # 呼吸频率 0~100（次/分）
        if 'respiratory_rate' in data:
            validate_numeric_range(data['respiratory_rate'], 0, 100, '呼吸频率')
        # 肺活量 0~10000（mL）
        if 'vital_capacity' in data:
            validate_numeric_range(data['vital_capacity'], 0, 10000, '肺活量')
        # 最大吸气压 0~200（cmH2O）
        if 'mip' in data:
            validate_numeric_range(data['mip'], 0, 200, '最大吸气压')
        # 6分钟步行 0~1000（m）
        if 'six_min_walk' in data:
            validate_numeric_range(data['six_min_walk'], 0, 1000, '6分钟步行距离')
        # 疼痛评分 0~10
        if 'pain_score' in data:
            validate_numeric_range(data['pain_score'], 0, 10, '疼痛评分')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    allowed_fields = ['evaluate_date', 'spo2', 'respiratory_rate',
                      'vital_capacity', 'mip', 'six_min_walk', 'pain_score', 'cough_effectiveness',
                      'sputum_property', 'activity_endurance', 'result', 'analysis_note']
    updates = []
    values = []
    for field in allowed_fields:
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            values.append(data[field])
    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400
    values.append(eval_id)
    sql = f"UPDATE evaluations SET {', '.join(updates)} WHERE id = %s"
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
@evaluations_bp.route('/<int:eval_id>', methods=['DELETE'])
def delete_evaluation(eval_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM evaluations WHERE id = %s", (eval_id,))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    if affected == 0:
        return jsonify({'error': '记录不存在'}), 404
    return jsonify({'message': '删除成功'})

# ===================== 批量删除 =====================
@evaluations_bp.route('/', methods=['DELETE'])
def delete_evaluations():
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'error': '请提供要删除的ID列表'}), 400
    placeholders = ','.join(['%s'] * len(ids))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM evaluations WHERE id IN ({placeholders})", ids)
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    conn.close()
    return jsonify({'message': f'成功删除 {deleted} 条记录'})

# ===================== 导出 CSV =====================
@evaluations_bp.route('/export/csv', methods=['GET'])
def export_evaluations_csv():
    try:
        search = request.args.get('search', '')
        result = request.args.get('result', '')

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        sql = "SELECT * FROM evaluations WHERE 1=1"
        params = []
        if search:
            sql += " AND patient_name LIKE %s"
            params.append(f'%{search}%')
        if result:
            sql += " AND result = %s"
            params.append(result)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "评估编号", "患者ID", "患者姓名", "评估日期", "血氧(%)", "呼吸频率(次/分)",
            "肺活量(mL)", "最大吸气压(cmH2O)", "6分钟步行(m)", "疼痛评分(0-10)",
            "咳嗽有效性", "痰液性状", "活动耐力", "评估结果", "分析说明"
        ])

        for r in rows:
            eval_date = r.get('evaluate_date')
            if eval_date:
                if isinstance(eval_date, date):
                    eval_date = eval_date.strftime('%Y-%m-%d')
                eval_date = "'" + eval_date   # 加单引号防止Excel自动转换
            else:
                eval_date = ''

            writer.writerow([
                r.get('evaluate_no', ''),
                r.get('patient_id', ''),
                r.get('patient_name', ''),
                eval_date,
                r.get('spo2', ''),
                r.get('respiratory_rate', ''),
                r.get('vital_capacity', ''),
                r.get('mip', ''),
                r.get('six_min_walk', ''),
                r.get('pain_score', ''),
                r.get('cough_effectiveness', ''),
                r.get('sputum_property', ''),
                r.get('activity_endurance', ''),
                r.get('result', ''),
                r.get('analysis_note', '')
            ])

        csv_data = output.getvalue()
        today = date.today()
        filename = f"评估记录_{today.strftime('%Y%m%d')}.csv"
        filename_encoded = quote(filename)

        return Response(
            b'\xef\xbb\xbf' + csv_data.encode('utf-8'),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"}
        )
    except Exception as e:
        print("导出错误:", e)
        return jsonify({"code": 500, "msg": f"导出失败: {str(e)}"}), 500