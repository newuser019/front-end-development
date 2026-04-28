from flask import Blueprint, request, jsonify, Response
from db import get_db_connection
from utils import compute_postop_days, format_date, dict_to_csv
import mysql.connector
from datetime import date, datetime


# ========== 新增：封装校验函数 ==========
def validate_patient_data(data, is_update=False):
    """
    校验患者数据合法性
    :param data: 前端传入的患者数据（dict）
    :param is_update: 是否为更新操作（True=更新，False=新增）
    :return: 校验通过返回None；校验失败返回 (错误信息dict, HTTP状态码)
    """
    # 1. 新增场景：必传字段非空校验（复用原有逻辑）
    if not is_update:
        required = ['hospitalization_id', 'name', 'gender', 'age', 'diagnosis', 'surgery_name', 'surgery_date']
        for field in required:
            if not data.get(field):
                return {'error': f'缺少必要字段: {field}'}, 400

    # 2. 年龄校验（新增/更新都要校验，若传了age）
    if 'age' in data and data['age'] is not None:
        if not isinstance(data['age'], int) or data['age'] <= 0 or data['age'] > 120:
            return {'error': '年龄必须是1-120的整数'}, 400

    # 3. 性别校验（新增/更新都要校验，若传了gender）
    if 'gender' in data and data['gender'] is not None:
        if data['gender'] not in ['男', '女', '未知']:
            return {'error': '性别只能是「男」「女」「未知」'}, 400

    # 4. 手术日期格式校验 + 不能晚于今天
    if 'surgery_date' in data and data['surgery_date'] is not None:
        try:
            surgery_date = datetime.strptime(data['surgery_date'], "%Y-%m-%d")
        except ValueError:
            return {'error': '手术日期格式错误，需为YYYY-MM-DD'}, 400
        # 校验：手术日期不能晚于今天
        today = date.today()
        if surgery_date.date() > today:
            return {'error': '手术日期不能晚于今天'}, 400

    # 5. 康复阶段校验（新增/更新都要校验，若传了recovery_stage）
    if 'recovery_stage' in data and data['recovery_stage'] is not None:
        valid_stages = ['术前准备期', '术后早期', '康复期']
        if data['recovery_stage'] not in valid_stages:
            return {'error': f'康复阶段只能是{valid_stages}'}, 400

    # 6. 风险等级校验（新增/更新都要校验，若传了risk_level）
    if 'risk_level' in data and data['risk_level'] is not None:
        valid_risk = ['低危', '中危', '高危']
        if data['risk_level'] not in valid_risk:
            return {'error': f'风险等级只能是{valid_risk}'}, 400

    # 7. 其他自定义校验（可按需扩展，比如住院号格式、手术名称非空等）
    if 'hospitalization_id' in data and data['hospitalization_id'] is not None:
        if not data['hospitalization_id'].strip():
            return {'error': '住院号不能为空字符串'}, 400
        # 示例：住院号必须是数字+字母组合（按需调整）
        # if not data['hospitalization_id'].replace('-', '').isalnum():
        #     return {'error': '住院号只能包含数字、字母和短横线'}, 400

    # 所有校验通过
    return None

patients_bp = Blueprint('patients', __name__, url_prefix='/api/patients')

@patients_bp.route('/', methods=['GET'])
def get_patients():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    search = request.args.get('search', '')
    stage = request.args.get('stage', '')
    offset = (page - 1) * limit

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    base_sql = "SELECT * FROM patients WHERE 1=1"
    params = []

    if search:
        base_sql += " AND (name LIKE %s OR hospitalization_id LIKE %s)"
        params.extend([f'%{search}%', f'%{search}%'])
    if stage:
        base_sql += " AND recovery_stage = %s"
        params.append(stage)

    count_sql = f"SELECT COUNT(*) as total FROM ({base_sql}) as t"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()['total']

    data_sql = f"{base_sql} ORDER BY hospitalization_id ASC LIMIT %s OFFSET %s"
    cursor.execute(data_sql, params + [limit, offset])
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    today = date.today()
    for row in rows:
        surgery_date = row['surgery_date']
        if surgery_date:
            if isinstance(surgery_date, str):
                surgery_date = datetime.strptime(surgery_date, "%Y-%m-%d").date()
            days = (today - surgery_date).days
            row['postop_days'] = days
            row['postop_days_text'] = f'术后第{days}天' if days >= 0 else '术前'
        else:
            row['postop_days'] = None
            row['postop_days_text'] = ''
        row['surgery_date'] = format_date(row['surgery_date'])

    return jsonify({
        'data': rows,
        'total': total,
        'page': page,
        'limit': limit,
        'totalPages': (total + limit - 1) // limit
    })

@patients_bp.route('/<int:patient_id>', methods=['GET'])
def get_patient(patient_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return jsonify({'error': '患者不存在'}), 404

    if row['surgery_date']:
        row['surgery_date'] = format_date(row['surgery_date'])
    return jsonify(row)

@patients_bp.route('/', methods=['POST'])
def add_patient():
    data = request.json
    # required = ['hospitalization_id', 'name', 'gender', 'age', 'diagnosis', 'surgery_name', 'surgery_date']
    # for field in required:
    #     if not data.get(field):
    #         return jsonify({'error': f'缺少必要字段: {field}'}), 400
    # ========== 新增：调用校验函数 ==========
    validate_result = validate_patient_data(data, is_update=False)
    if validate_result:  # 校验失败返回错误信息
        return jsonify(validate_result[0]), validate_result[1]

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO patients 
            (hospitalization_id, name, gender, age, diagnosis, surgery_name, surgery_date,
             surgeon, anesthesia_type, incision_type, recovery_stage, risk_level,
             medical_history, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data['hospitalization_id'], data['name'], data['gender'], data['age'],
            data['diagnosis'], data['surgery_name'], data['surgery_date'],
            data.get('surgeon'), data.get('anesthesia_type'), data.get('incision_type'),
            data.get('recovery_stage', '术后早期'), data.get('risk_level', '中危'),
            data.get('medical_history'), data.get('notes')
        ))
        conn.commit()
        return jsonify({'id': cursor.lastrowid, 'message': '患者添加成功'}), 201
    except mysql.connector.IntegrityError as e:
        return jsonify({'error': '住院号已存在'}), 409
    finally:
        cursor.close()
        conn.close()

@patients_bp.route('/<int:patient_id>', methods=['PUT'])
def update_patient(patient_id):
    data = request.json
    allowed_fields = ['name', 'gender', 'age', 'diagnosis', 'surgery_name', 'surgery_date',
                      'surgeon', 'anesthesia_type', 'incision_type', 'recovery_stage',
                      'risk_level', 'medical_history', 'notes']
    # ========== 新增：调用校验函数（is_update=True）==========
    validate_result = validate_patient_data(data, is_update=True)
    if validate_result:  # 校验失败返回错误信息
        return jsonify(validate_result[0]), validate_result[1]
    updates = []
    values = []
    for field in allowed_fields:
        if field in data and data[field] is not None:
            updates.append(f"{field} = %s")
            values.append(data[field])
    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400

    values.append(patient_id)
    sql = f"UPDATE patients SET {', '.join(updates)}, updated_at = NOW() WHERE id = %s"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(sql, values)
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()

    if affected == 0:
        return jsonify({'error': '患者不存在'}), 404
    return jsonify({'message': '患者信息更新成功'})

@patients_bp.route('/', methods=['DELETE'])
def delete_patients():
    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'error': '请提供要删除的ID列表'}), 400
    placeholders = ','.join(['%s'] * len(ids))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"DELETE FROM patients WHERE id IN ({placeholders})", ids)
    conn.commit()
    deleted = cursor.rowcount
    cursor.close()
    conn.close()
    return jsonify({'message': f'成功删除 {deleted} 条记录'})

# ===================== 导出CSV（完美修复版）=====================
@patients_bp.route('/export/csv', methods=['GET'])
def export_patients_csv():
    from datetime import date
    import csv
    import io
    from urllib.parse import quote

    search = request.args.get('search', '')
    stage = request.args.get('stage', '')
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        sql = """
            SELECT hospitalization_id, name, gender, age, diagnosis, surgery_name,
                   surgery_date, recovery_stage, risk_level
            FROM patients WHERE 1=1
        """
        params = []
        if search:
            sql += " AND (name LIKE %s OR hospitalization_id LIKE %s)"
            params.extend([f'%{search}%', f'%{search}%'])
        if stage:
            sql += " AND recovery_stage = %s"
            params.append(stage)
        sql += " ORDER BY hospitalization_id ASC"
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        today = date.today()
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["住院号","姓名","性别","年龄","诊断","手术名称","手术日期","术后天数","康复阶段","风险等级"])

        for r in rows:
            # ✅ 关键修改：在日期前加单引号，强制 Excel 按文本格式解析
            if r.get("surgery_date"):
                s_date = f"'{r['surgery_date'].strftime('%Y-%m-%d')}"
                days = (today - r["surgery_date"]).days
                postop = f"术后第{days}天" if days >= 0 else "术前"
            else:
                s_date = ""
                postop = ""

            writer.writerow([
                r["hospitalization_id"], r["name"], r["gender"], r["age"], r["diagnosis"],
                r["surgery_name"], s_date, postop, r["recovery_stage"], r["risk_level"]
            ])

        csv_data = output.getvalue()

        filename = f"患者数据_{today.strftime('%Y%m%d')}.csv"
        filename_encoded = quote(filename)

        return Response(
            b'\xef\xbb\xbf' + csv_data.encode('utf-8'),
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}"
            }
        )
    except Exception as e:
        print("导出错误：", e)
        return {"code": 500, "msg": str(e)}, 500