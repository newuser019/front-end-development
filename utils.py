import csv
from io import StringIO
from datetime import datetime, date

def compute_postop_days(surgery_date):
    """计算术后天数，返回类似 '术后第5天' 的字符串"""
    if not surgery_date:
        return ''
    days = (date.today() - surgery_date).days
    if days <= 0:
        return '术前'
    return f'术后第{days}天'

def format_datetime(dt):
    """格式化 datetime 对象为 YYYY-MM-DD HH:MM:SS"""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_date(d):
    if d:
        return d.strftime('%Y-%m-%d')
    return ''

import csv
import io

def dict_to_csv(data):
    if not data:
        return ''
    output = io.StringIO()
    # 获取表头
    fieldnames = data[0].keys()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(data)
    return output.getvalue()


# utils.py 新增校验函数
def validate_numeric_range(value, min_val, max_val, field_name):
    """
    验证数值是否在指定范围内
    :param value: 要验证的值（可以是None）
    :param min_val: 最小值
    :param max_val: 最大值
    :param field_name: 字段名称（用于错误提示）
    :return: 无异常返回True，异常则抛出ValueError
    """
    if value is None or value == '':
        return True

    try:
        num_value = float(value)
        if not (min_val <= num_value <= max_val):
            raise ValueError(f"{field_name} 必须在 {min_val} 到 {max_val} 之间，当前值：{value}")
    except ValueError as e:
        if "could not convert string to float" in str(e):
            raise ValueError(f"{field_name} 必须是有效的数字，当前值：{value}")
        raise e

    return True