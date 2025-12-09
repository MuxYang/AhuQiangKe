#!/usr/bin/env python3
"""
安徽大学选课系统 API 客户端
支持获取课程列表、查询特定课程、自动抢课等功能
"""

import requests
import json
import time
import re
import ntplib
import threading
import os
import sys
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CourseFilter:
    """课程筛选条件"""
    course_name: str = ""  # 课程名称，如"示例课程A"
    weeks: str = ""  # 周次，如"1~12"
    weekday: int = 0  # 星期几，1=周一，2=周二...
    start_unit: int = 0  # 开始节次
    end_unit: int = 0  # 结束节次
    campus: str = "磬苑校区"
    building: str = ""  # 教学楼，如"博北A101"


class ConsoleUI:
    """轻量级控制台UI，提供统一的输出风格。"""

    def __init__(self, width: int = 66):
        self.width = max(50, width)

    # 基础输出
    def line(self, char: str = "-"):
        print(char * self.width)

    def divider(self, title: str = ""):
        print("=" * self.width)
        if title:
            print(title.center(self.width, " "))
            print("=" * self.width)

    def banner(self, title: str):
        print("=" * self.width)
        print(title.center(self.width, " "))
        print("=" * self.width)

    # 状态输出
    def _tag(self, label: str, text: str):
        print(f"[{label}] {text}")

    def info(self, text: str):
        self._tag("INFO", text)

    def success(self, text: str):
        self._tag(" OK ", text)

    def warn(self, text: str):
        self._tag("WARN", text)

    def error(self, text: str):
        self._tag("FAIL", text)

    def step(self, title: str):
        print(f"\n-- {title} " + "-" * max(2, self.width - len(title) - 4))

    def bullet_list(self, items: Iterable[str]):
        for item in items:
            print(f"  - {item}")

    def question(self, text: str) -> str:
        return input(f"[?] {text}: ").strip()


def load_course_targets(file_path: str = "list.json", ui: Optional[ConsoleUI] = None) -> List[Dict[str, Any]]:
    """从JSON文件加载待抢课程列表（包含筛选条件）"""
    ui = ui or ConsoleUI()
    base_dir = Path(__file__).resolve().parent
    target_path = Path(file_path)
    if not target_path.is_absolute():
        target_path = base_dir / target_path

    try:
        with target_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        ui.error(f"未找到课程列表文件: {target_path}")
        ui.info("请在程序目录下创建 list.json（示例已生成）")
        return []
    except Exception as exc:  # 捕获格式错误等异常
        ui.error(f"读取课程列表失败: {exc}")
        return []

    if not isinstance(data, list):
        ui.error("list.json 格式错误，应为数组")
        return []

    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            ui.warn(f"跳过第 {idx + 1} 条记录（应为对象）")
            continue

        filter_obj = CourseFilter(
            course_name=item.get("course_name", ""),
            weeks=item.get("weeks", ""),
            weekday=int(item.get("weekday") or 0),
            start_unit=int(item.get("start_unit") or 0),
            end_unit=int(item.get("end_unit") or 0),
            campus=item.get("campus", "磬苑校区"),
            building=item.get("building", ""),
        )

        normalized.append({
            "course_id": item.get("course_id"),
            "filter": filter_obj,
            "priority": item.get("priority", idx + 1),
        })

    normalized.sort(key=lambda x: x.get("priority", 0))
    return normalized


def clear_screen():
    """清屏以避免刷屏输出。"""
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
    except Exception:
        pass


class AHUCourseSelector:
    """安徽大学选课系统客户端"""
    
    BASE_URL = "https://jw.ahu.edu.cn"
    API_BASE = f"{BASE_URL}/course-selection-api/api/v1/student/course-select"
    
    def __init__(self, token: Optional[str] = None, student_id: Optional[str] = None, ui: Optional[ConsoleUI] = None):
        """
        初始化选课客户端
        
        Args:
            token: JWT token (cs-course-select-student-token)
            student_id: 学生ID (cs-course-select-student-id)
        """
        self.session = requests.Session()
        self.token = token
        self.student_id = student_id
        self.turn_id = None  # 选课批次ID
        self.semester_id = None  # 学期ID
        self.log_path = Path(__file__).resolve().parent / "query.log"
        self.ui = ui or ConsoleUI()
        
        # 设置默认请求头
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.82 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': f'{self.BASE_URL}/course-selection/',
            'Origin': self.BASE_URL,
        })
        
        if token and student_id:
            self._update_auth(token, student_id)

    # 输出包装，保持统一风格
    def _info(self, text: str):
        self.ui.info(text)

    def _success(self, text: str):
        self.ui.success(text)

    def _warn(self, text: str):
        self.ui.warn(text)

    def _error(self, text: str):
        self.ui.error(text)

    @staticmethod
    def _is_duplicate_message(message: str) -> bool:
        """检测是否为“相同教学班只能选一次”类提示。"""
        if not message:
            return False
        return any(keyword in message for keyword in ["相同教学班只能选一次", "Duplicate lessons are not allowed"])

    @staticmethod
    def _extract_text_field(obj: Any) -> str:
        """从 error/result map 中提取 text 字段，默认返回空串。"""
        if isinstance(obj, dict):
            return obj.get('text') or obj.get('textZh') or obj.get('textEn') or ''
        if isinstance(obj, str):
            return obj
        return ''
    
    def _update_auth(self, token: str, student_id: str):
        """更新认证信息"""
        self.token = token
        self.student_id = student_id
        
        # 更新Cookie和Authorization
        self.session.cookies.set('cs-course-select-student-token', token, domain='jw.ahu.edu.cn')
        self.session.cookies.set('cs-course-select-student-id', student_id, domain='jw.ahu.edu.cn')
        self.session.headers.update({'Authorization': token})

    def _build_api_headers(self) -> Dict[str, str]:
        """构造与浏览器一致的 API 头部。"""
        return {
            'Authorization': self.token or '',
            'content-type': 'application/json',
            'contenttype': 'application/json',  # 与 HAR 一致
            'Accept': 'application/json, text/plain, */*',
            'Origin': self.BASE_URL,
            'Referer': f'{self.BASE_URL}/course-selection/',
        }

    def _post_json(self, url: str, payload: Dict[str, Any], label: str = "", timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """统一的 POST JSON 请求，带日志，支持超时。"""
        headers = self._build_api_headers()
        self._log_query(label or "post_request", {"url": url, "payload": payload, "headers": headers})
        resp = self.session.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        self._log_query(label or "post_response", data)
        return data

    def warmup_course_page(self):
        """访问一次课程选择页，确保与浏览器一致的 referer/cookie 场景。"""
        if not self.student_id or not self.turn_id:
            self.get_turn_info()
        if not self.student_id or not self.turn_id:
            return
        url = f"{self.BASE_URL}/course-selection/#/course-select/{self.student_id}/turn/{self.turn_id}/select"
        try:
            headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Referer': f'{self.BASE_URL}/course-selection/',
            }
            self._log_query("warmup_request", {"url": url, "headers": headers})
            resp = self.session.get(url, headers=headers)
            self._log_query("warmup_response", {"status": resp.status_code})
        except Exception as exc:
            self._warn(f"预热访问失败: {exc}")
    
    def load_credentials(self, filepath: str = "credentials.json") -> bool:
        """
        从文件加载凭证
        
        Args:
            filepath: 凭证文件路径
            
        Returns:
            是否成功加载
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                creds = json.load(f)
                self._update_auth(creds['token'], creds['student_id'])
                self._success(f"已加载凭证: student_id={self.student_id}")
                return True
        except FileNotFoundError:
            self._error(f"凭证文件不存在: {filepath}")
            return False
        except Exception as e:
            self._error(f"加载凭证失败: {e}")
            return False
    
    def save_credentials(self, filepath: str = "credentials.json"):
        """保存凭证到文件"""
        if not self.token or not self.student_id:
            self._error("无凭证可保存")
            return
        
        creds = {
            'token': self.token,
            'student_id': self.student_id
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(creds, f, indent=2, ensure_ascii=False)
        self._success(f"凭证已保存到 {filepath}")
    
    def get_turn_info(self) -> Optional[Dict]:
        """
        获取当前选课批次信息
        
        Returns:
            选课批次信息，包含turnId、semesterId等
        """
        if not self.student_id:
            self._error("未设置 student_id")
            return None
        
        url = f"{self.API_BASE}/{self.student_id}/turn/741/select"
        
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('result') == 0 and data.get('data'):
                turn_data = data['data']
                self.turn_id = turn_data['turn']['id']
                self.semester_id = turn_data['semester']['id']

                self._success(f"选课批次: {turn_data['turn']['name']}")
                self._info(f"学期: {turn_data['semester']['nameZh']}")
                self._info(f"turnId: {self.turn_id}, semesterId: {self.semester_id}")
                return turn_data
            else:
                self._error(f"获取批次信息失败: {data.get('message', 'unknown error')}")
                return None
                
        except Exception as e:
            self._error(f"请求失败: {e}")
            return None
    
    def get_selected_courses(self) -> List[Dict]:
        """
        获取已选课程列表
        
        Returns:
            已选课程列表
        """
        if not self.turn_id:
            self.get_turn_info()
        
        if not self.turn_id or not self.student_id:
            self._error("缺少必要参数")
            return []
        
        url = f"{self.API_BASE}/selected-lessons/{self.turn_id}/{self.student_id}"
        
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get('result') == 0 and data.get('data'):
                courses = data['data']
                self._success(f"已选课程数量: {len(courses)}")
                return courses
            else:
                self._error(f"获取已选课程失败: {data.get('message', 'unknown error')}")
                return []
                
        except Exception as e:
            self._error(f"请求失败: {e}")
            return []
    
    def query_lessons(self, 
                      course_id: Optional[int] = None,
                      course_name: str = "",
                      page_no: int = 1,
                      page_size: int = 20) -> Dict[str, Any]:
        """
        查询课程列表
        
        Args:
            course_id: 课程ID（如4319表示公务员考试技巧）
            course_name: 课程名称关键词
            page_no: 页码
            page_size: 每页数量
            
        Returns:
            包含lessons和pageInfo的字典
        """
        if not self.turn_id:
            self.get_turn_info()
        
        if not self.turn_id or not self.student_id or not self.semester_id:
            self._error("缺少必要参数")
            return {'lessons': [], 'pageInfo': {}}
        
        url = f"{self.API_BASE}/query-lesson/{self.student_id}/{self.turn_id}"

        # 优先名称/关键词；有名称时不使用 courseId
        course_id_payload: Optional[int] = None
        if not course_name and course_id is not None:
            course_id_payload = int(course_id)
        
        payload = {
            "turnId": self.turn_id,
            "studentId": int(self.student_id),
            "semesterId": self.semester_id,
            "pageNo": page_no,
            "pageSize": page_size,
            "courseId": course_id_payload,
            "courseNameOrCode": course_name,
            "lessonNameOrCode": "",
            "teacherNameOrCode": "",
            "week": "",
            "grade": "",
            "departmentId": "",
            "majorId": "",
            "adminclassId": "",
            "campusId": "",
            "openDepartmentId": "",
            "courseTypeId": "",
            "coursePropertyId": "",
            "courseTaxonId": "",
            "courseOwnershipId": "",
            "canSelect": 1,
            "_canSelect": "",
            "creditGte": None,
            "creditLte": None,
            "hasCount": None,
            "ids": None,
            "substitutedCourseId": None,
            "courseSubstitutePoolId": None,
            "sortField": "lesson",
            "sortType": "ASC"
        }
        
        try:
            data = self._post_json(url, payload, label="query_lessons")
            
            if data.get('result') == 0 and data.get('data'):
                result = data['data']
                lessons = result.get('lessons', [])
                page_info = result.get('pageInfo', {})

                self._success(f"查询到 {len(lessons)} 门课程 (共 {page_info.get('totalRows', 0)} 门)")
                return result
            else:
                self._error(f"查询课程失败: {data.get('message', 'unknown error')}")
                return {'lessons': [], 'pageInfo': {}}
                
        except Exception as e:
            self._error(f"请求失败: {e}")
            return {'lessons': [], 'pageInfo': {}}
    
    def filter_lessons(self, lessons: List[Dict], filter_obj: CourseFilter) -> List[Dict]:
        """
        根据条件筛选课程
        
        Args:
            lessons: 课程列表
            filter_obj: 筛选条件
            
        Returns:
            符合条件的课程列表
        """
        result = []
        
        for lesson in lessons:
            # 课程名称匹配
            if filter_obj.course_name:
                course_name = lesson.get('course', {}).get('nameZh', '')
                if filter_obj.course_name not in course_name:
                    continue
            
            # 时间地点文本
            date_time_place = lesson.get('dateTimePlace', {}).get('textZh', '')
            
            # 周次匹配
            if filter_obj.weeks:
                if filter_obj.weeks not in date_time_place:
                    continue
            
            # 星期匹配
            if filter_obj.weekday > 0:
                weekday_map = {1: '星期一', 2: '星期二', 3: '星期三', 4: '星期四', 5: '星期五', 6: '星期六', 7: '星期日'}
                if weekday_map.get(filter_obj.weekday, '') not in date_time_place:
                    continue
            
            # 节次匹配
            if filter_obj.start_unit > 0 and filter_obj.end_unit > 0:
                unit_pattern = f"{filter_obj.start_unit}~{filter_obj.end_unit}节"
                if unit_pattern not in date_time_place:
                    continue
            
            # 校区匹配
            if filter_obj.campus and filter_obj.campus not in date_time_place:
                continue
            
            # 教室匹配
            if filter_obj.building and filter_obj.building not in date_time_place:
                continue
            
            result.append(lesson)
        
        return result
    
    def add_course_predicate(self, lesson_id: int, virtual_cost: int = 0, timeout: float = 5.0, suppress_log: bool = False) -> Optional[str]:
        """提交选课预检请求（按浏览器 HAR 头部发送）。"""
        if not self.turn_id or not self.student_id:
            if not self.get_turn_info():
                return None
        
        url = f"{self.API_BASE}/add-predicate"
        payload = {
            "studentAssoc": int(self.student_id),
            "courseSelectTurnAssoc": self.turn_id,
            "requestMiddleDtos": [
                {
                    "lessonAssoc": lesson_id,
                    "virtualCost": virtual_cost
                }
            ],
            "coursePackAssoc": None
        }
        
        try:
            data = self._post_json(url, payload, label="add_predicate", timeout=timeout)
            if data.get('result') == 0 and data.get('data'):
                request_id = data['data']
                if not suppress_log:
                    self._success(f"选课预检请求已提交, requestId: {request_id}")
                return request_id
            else:
                if not suppress_log:
                    self._error(f"提交选课失败: {data.get('message', 'unknown error')}")
                return None
                
        except Exception as e:
            if not suppress_log:
                self._error(f"请求失败: {e}")
            return None

    def add_course_request(self, lesson_id: int, virtual_cost: Optional[int] = None, timeout: float = 5.0, suppress_log: bool = False) -> Optional[str]:
        """提交正式选课请求（与 HAR 一致，virtualCost 允许为 null）。"""
        if not self.turn_id or not self.student_id:
            if not self.get_turn_info():
                return None

        url = f"{self.API_BASE}/add-request"
        payload = {
            "studentAssoc": int(self.student_id),
            "courseSelectTurnAssoc": self.turn_id,
            "requestMiddleDtos": [
                {
                    "lessonAssoc": lesson_id,
                    "virtualCost": virtual_cost
                }
            ],
            "coursePackAssoc": None
        }

        try:
            data = self._post_json(url, payload, label="add_request", timeout=timeout)
            if data.get('result') == 0 and data.get('data'):
                request_id = data['data']
                if not suppress_log:
                    self._success(f"正式选课请求已提交, requestId: {request_id}")
                return request_id
            else:
                if not suppress_log:
                    self._error(f"提交正式选课失败: {data.get('message', 'unknown error')}")
                return None
        except Exception as e:
            if not suppress_log:
                self._error(f"请求失败: {e}")
            return None
    
    def get_predicate_response(self, request_id: str, max_retries: int = 10, poll_interval: float = 0.2, timeout: float = 5.0, suppress_log: bool = False) -> Optional[Dict]:
        """
        轮询选课预检结果
        
        Args:
            request_id: 请求ID
            max_retries: 最大重试次数
            
        Returns:
            选课结果
        """
        if not self.student_id:
            self._error("未设置 student_id")
            return None
        
        url = f"{self.API_BASE}/predicate-response/{self.student_id}/{request_id}"
        
        for i in range(max_retries):
            try:
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                # 记录轮询结果便于排查
                self._log_query("predicate_response_poll", {"request_id": request_id, "round": i+1, "data": data})
                
                if data.get('result') == 0:
                    if data.get('data'):
                        result = data['data']
                        if result.get('success'):
                            if not suppress_log:
                                self._success("选课成功")
                                self._info(f"完整响应: {json.dumps(result, ensure_ascii=False, indent=2)}")
                        else:
                            error_msg = result.get('errorMessage', {}).get('text', '未知错误')
                            if not suppress_log:
                                self._error(f"选课失败: {error_msg}")
                                if result.get('errorMessage'):
                                    self._info(f"错误详情: {json.dumps(result.get('errorMessage'), ensure_ascii=False)}")
                        return result
                    else:
                        # 结果还未准备好，等待重试
                        if i < max_retries - 1:
                            if not suppress_log:
                                self._info(f"等待结果... ({i+1}/{max_retries})")
                            time.sleep(poll_interval)
                        continue
                else:
                    if not suppress_log:
                        self._error(f"查询失败: {data.get('message', 'unknown error')}")
                    return None
                    
            except Exception as e:
                if not suppress_log:
                    self._error(f"请求失败: {e}")
                if i < max_retries - 1:
                    time.sleep(poll_interval)
                    continue
                return None
        
        if not suppress_log:
            self._error("超过最大重试次数")
        return None

    def get_add_drop_response(self, request_id: str, max_retries: int = 10, poll_interval: float = 0.2, timeout: float = 5.0, suppress_log: bool = False) -> Optional[Dict]:
        """轮询正式选课结果（add-drop-response）。"""
        if not self.student_id:
            self._error("未设置 student_id")
            return None

        url = f"{self.API_BASE}/add-drop-response/{self.student_id}/{request_id}"

        for i in range(max_retries):
            try:
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                self._log_query("add_drop_response_poll", {"request_id": request_id, "round": i + 1, "data": data})

                if data.get('result') == 0:
                    result = data.get('data') or {}
                    if result.get('success'):
                        if not suppress_log:
                            self._success(f"正式选课成功: resend={result.get('resend')}")
                        return result
                    if result.get('errorMessage'):
                        err_text = result['errorMessage'].get('text', '未知错误')
                        if self._is_duplicate_message(err_text):
                            if not suppress_log:
                                self._success("已选过该教学班 (add-drop-response)")
                            result['success'] = True
                            result['duplicate'] = True
                            return result
                        if not suppress_log:
                            self._error(f"正式选课失败: {err_text}")
                        return result

                    if i < max_retries - 1:
                        time.sleep(poll_interval)
                    continue
                else:
                    if not suppress_log:
                        self._error(f"查询失败: {data.get('message', 'unknown error')}")
                    return None
            except Exception as e:
                if not suppress_log:
                    self._error(f"请求失败: {e}")
                if i < max_retries - 1:
                    time.sleep(poll_interval)
                    continue
                return None

        if not suppress_log:
            self._error("超过最大重试次数")
        return None

    def _log_query(self, label: str, content: Any):
        """将查询相关内容追加到日志文件，单行压缩并截断，避免日志膨胀。"""
        def _as_json_line(obj: Any) -> str:
            try:
                text = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), default=str)
            except Exception:
                text = str(obj)
            max_len = 1200  # 单条日志最长字符数
            if len(text) > max_len:
                return text[:max_len] + f"...<truncated {len(text) - max_len} chars>"
            return text

        try:
            line = f"[{datetime.now().isoformat()}] {label} " + _as_json_line(content)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
        except Exception as exc:
            self._warn(f"无法写入日志: {exc}")
    
    def force_send_requests(self, lesson_id: int, attempts: int = 10, interval: float = 0.25, request_timeout: float = 5.0) -> bool:
        """
        强制重复发送选课预检请求若干次（用于重试抢课）
        如果任意一次返回成功则立即返回 True。
        """
        self._info(f"强制发送请求: lessonId={lesson_id}, attempts={attempts}, interval={interval}s")
        last_error = None
        status_line = ""

        def update_status(text: str):
            nonlocal status_line
            if text == status_line:
                return
            clear = " " * max(len(status_line), len(text))
            print(f"\r{clear}\r{text}", end='', flush=True)
            status_line = text

        for i in range(1, attempts + 1):
            request_id = self.add_course_predicate(lesson_id, virtual_cost=0, timeout=request_timeout, suppress_log=True)
            if not request_id:
                last_error = "未能获取 request_id"
                update_status(f"第{i}/{attempts}: 未获取request_id")
                time.sleep(interval)
                continue
            result = self.get_predicate_response(request_id, max_retries=3, timeout=request_timeout, suppress_log=True)
            if result:
                if result.get('success'):
                    pred_map = result.get('result') or {}
                    for v in pred_map.values():
                        if isinstance(v, dict) and self._is_duplicate_message(v.get('text', '')):
                            update_status(f"第{i}/{attempts}: 已选过")
                            print()
                            return True
                    add_request_id = self.add_course_request(lesson_id, virtual_cost=None, timeout=request_timeout, suppress_log=True)
                    if not add_request_id:
                        last_error = "未能获取 add-request request_id"
                        update_status(f"第{i}/{attempts}: 无 add-request id")
                        time.sleep(interval)
                        continue
                    final = self.get_add_drop_response(add_request_id, max_retries=10, poll_interval=0.2, timeout=request_timeout, suppress_log=True)
                    if final and final.get('success'):
                        update_status(f"第{i}/{attempts}: 成功")
                        print()
                        return True
                    if self._is_duplicate_message((final or {}).get('errorMessage', {}).get('text', '')) or (final or {}).get('duplicate'):
                        update_status(f"第{i}/{attempts}: 已选过")
                        print()
                        return True
                    msg = (final or {}).get('errorMessage') or (final or {}).get('message') or '无详细信息'
                    last_error = msg
                    update_status(f"第{i}/{attempts}: {self._extract_text_field(msg)}")
                else:
                    msg = result.get('errorMessage') or result.get('message') or '无详细信息'
                    if self._is_duplicate_message(msg if isinstance(msg, str) else msg.get('text', '')):
                        update_status(f"第{i}/{attempts}: 已选过")
                        print()
                        return True
                    last_error = msg
                    update_status(f"第{i}/{attempts}: {self._extract_text_field(msg)}")
            else:
                update_status(f"第{i}/{attempts}: 查询失败")
            time.sleep(interval)
        print()
        self._warn(f"所有尝试结束，最后错误: {last_error}")
        return False
    
    def print_lesson_info(self, lesson: Dict):
        """打印课程信息"""
        course = lesson.get('course', {})
        teachers = lesson.get('teachers', [])
        teacher_names = ', '.join([t.get('nameZh', '') for t in teachers])
        date_time = lesson.get('dateTimePlace', {}).get('textZh', '')
        
        print(f"\n课程ID: {lesson.get('id')}")
        print(f"课程名称: {course.get('nameZh')} ({course.get('code')})")
        print(f"学分: {course.get('credits')}")
        print(f"教师: {teacher_names}")
        print(f"时间地点: {date_time}")
        print(f"限选人数: {lesson.get('limitCount')}")
        print(f"课程类型: {lesson.get('courseType', {}).get('nameZh')}")
        print(f"考核方式: {lesson.get('examMode', {}).get('nameZh')}")
    
    def sync_time_with_ntp(self, ntp_server: str = 'ntp.aliyun.com') -> float:
        """
        通过NTP服务器同步时间，返回时间偏移量（秒）
        取三次测量的平均值以提高准确性
        
        Args:
            ntp_server: NTP服务器地址
            
        Returns:
            本地时间与NTP服务器的时间差（秒），正数表示本地时间慢
        """
        offsets = []
        
        for i in range(3):
            try:
                client = ntplib.NTPClient()
                response = client.request(ntp_server, version=3, timeout=5)
                ntp_time = response.tx_time
                local_time = time.time()
                offset = ntp_time - local_time
                offsets.append(offset)
                self._info(f"第 {i+1} 次测量: {offset:.3f} 秒")
                time.sleep(0.5)  # 间隔0.5秒
            except Exception as e:
                self._error(f"第 {i+1} 次NTP同步失败: {e}")
        
        if offsets:
            avg_offset = sum(offsets) / len(offsets)
            self._success(f"NTP时间同步成功 (服务器: {ntp_server})")
            self._info(f"平均时间偏差: {avg_offset:.3f} 秒 {'(慢)' if avg_offset > 0 else '(快)'} (基于 {len(offsets)} 次测量)")
            return avg_offset
        else:
            self._error("所有NTP同步尝试失败")
            self._info("将使用本地时间")
            return 0.0
    
    def rapid_select_course(self, lesson_id: int, target_time: datetime, 
                           time_offset: float = 0.0,
                           concurrency: int = 4,
                           request_timeout: float = 5.0) -> bool:
        """
        快速抢课（并发请求，每个请求超时5秒）
        """
        max_duration = 180  # 抢课窗口时长，秒

        now_dt = datetime.fromtimestamp(time.time() + time_offset)
        # 提前5秒开始；如果已过目标时间，立即开始
        if now_dt > target_time:
            self._warn("当前时间已晚于目标时间，将立即开始抢课")
            start_time = now_dt - timedelta(seconds=1)
        else:
            start_time = target_time - timedelta(seconds=5)
        deadline = max(target_time, now_dt) + timedelta(seconds=max_duration)

        self._info(f"等待抢课时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self._info(f"将在 {start_time.strftime('%H:%M:%S')} 开始发起请求 (并发: {concurrency})")

        # 等待到开始时间（使用静态显示）
        last_print_time = time.time()
        while True:
            current_time = time.time() + time_offset
            current_dt = datetime.fromtimestamp(current_time)
            
            if current_dt >= start_time:
                break
            
            remaining = (start_time - current_dt).total_seconds()
            
            # 每秒更新一次，避免刷屏
            if time.time() - last_print_time >= 1.0:
                print(f"\r距离开始还有 {int(remaining)} 秒...  ", end='', flush=True)
                last_print_time = time.time()
            
            time.sleep(0.1)
        
        print("\n")
        self._info("开始抢课 (并发请求)")
        print()

        def _single_attempt(attempt_idx: int):
            try:
                req_id = self.add_course_predicate(lesson_id, virtual_cost=0, timeout=request_timeout, suppress_log=True)
                if not req_id:
                    return False, f"尝试{attempt_idx}: 无 request_id"

                pred = self.get_predicate_response(req_id, max_retries=8, poll_interval=0.15, timeout=request_timeout, suppress_log=True)
                if not pred or not pred.get('success'):
                    err = self._extract_text_field((pred or {}).get('errorMessage') if pred else '') or '无结果'
                    if self._is_duplicate_message(err):
                        return True, f"尝试{attempt_idx}: 已选过 (预检)"
                    return False, f"尝试{attempt_idx}: 预检失败 {err}"

                # 预检结果中的 result map 也可能提示已选过
                pred_map = pred.get('result') or {}
                for v in pred_map.values():
                    if isinstance(v, dict) and self._is_duplicate_message(v.get('text', '')):
                        return True, f"尝试{attempt_idx}: 已选过 (预检结果)"

                add_req_id = self.add_course_request(lesson_id, virtual_cost=None, timeout=request_timeout, suppress_log=True)
                if not add_req_id:
                    return False, f"尝试{attempt_idx}: 无 add-request id"

                final = self.get_add_drop_response(add_req_id, max_retries=10, poll_interval=0.2, timeout=request_timeout, suppress_log=True)
                if final and final.get('success'):
                    return True, f"尝试{attempt_idx}: 正式成功"
                err = self._extract_text_field((final or {}).get('errorMessage') if final else '') or '无最终结果'
                if '时间冲突' in err:
                    return False, f"尝试{attempt_idx}: 时间冲突"
                if self._is_duplicate_message(err) or (final or {}).get('duplicate'):
                    return True, f"尝试{attempt_idx}: 已选过 (add-drop)"
                return False, f"尝试{attempt_idx}: 正式失败 {err}"
            except Exception as exc:
                return False, f"尝试{attempt_idx}: 异常 {str(exc)[:80]}"

        attempt_counter = 0
        success = False
        last_error = None

        status_line = ""

        def update_status(text: str):
            nonlocal status_line
            if text == status_line:
                return
            clear = " " * max(len(status_line), len(text))
            print(f"\r{clear}\r{text}", end='', flush=True)
            status_line = text

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = set()

            try:
                while True:
                    current_dt = datetime.fromtimestamp(time.time() + time_offset)
                    if current_dt > deadline:
                        print()
                        self._error(f"抢课超时 (已发起 {attempt_counter} 次)")
                        self._warn(f"最后错误: {last_error}")
                        return False

                    # 补足并发槽位
                    while len(futures) < concurrency:
                        attempt_counter += 1
                        futures.add(executor.submit(_single_attempt, attempt_counter))

                    done, futures = wait(futures, return_when=FIRST_COMPLETED, timeout=0.05)
                    for fut in done:
                        ok, msg = fut.result()
                        last_error = msg
                        if ok:
                            update_status(f"成功 {msg}")
                            success = True
                            break
                        else:
                            # 仅保留尝试次数与文本字段
                            update_status(msg)

                    if success:
                        # 取消剩余未完成的任务，避免多余请求
                        for f in futures:
                            f.cancel()
                            print()  # 换行结束状态行
                            self._success(f"选课成功 (并发尝试次数: {attempt_counter})")
                        return True
            except KeyboardInterrupt:
                for f in futures:
                    f.cancel()
                    print()
                    self._warn("已中断抢课 (Ctrl+C)")
                return False

def prompt_manual_credentials(client: AHUCourseSelector) -> bool:
    """手动输入 student_id 与 token，并立即保存"""
    client.ui.step("手动输入模式：按提示输入学号和 token（不会回显敏感信息）")
    student_id = client.ui.question("学生ID（纯数字）")
    if not student_id.isdigit():
        client._error("学生ID需为纯数字")
        return False

    token = client.ui.question("JWT token")
    if not token:
        client._error("token 不能为空")
        return False

    client._update_auth(token, student_id)
    client.save_credentials()
    return True


def load_credentials_with_retry(client: AHUCourseSelector) -> bool:
    """
    加载凭证，如果失败则要求用户输入新的凭证文件路径
    
    Args:
        client: 选课客户端实例
        
    Returns:
        是否成功加载
    """
    # 尝试默认路径
    if client.load_credentials():
        return True

    while True:
        print()
        client.ui.step("请选择凭证获取方式")
        client.ui.bullet_list([
            "1) 手动输入 学生ID + token",
            "2) 指定凭证文件路径",
            "3) 退出",
        ])

        choice = client.ui.question("选择 (1/2/3)")

        if choice == '1':
            if prompt_manual_credentials(client):
                return True
            continue
        if choice == '2':
            path = client.ui.question("凭证文件路径 (q 返回)")
            if path.lower() == 'q':
                continue
            if client.load_credentials(path):
                return True
            client._warn("无法加载凭证，请检查文件是否存在且格式正确")
            continue
        if choice == '3':
            return False
        client._warn("无效选项，请重新选择")


def verify_credentials(client: AHUCourseSelector) -> bool:
    """
    验证凭证是否有效（尝试获取选课批次信息）
    
    Args:
        client: 选课客户端实例
        
    Returns:
        凭证是否有效
    """
    client.ui.step("验证凭证有效性")
    return client.get_turn_info() is not None


def parse_target_time(time_str: str) -> Optional[datetime]:
    """
    解析目标时间字符串
    
    Args:
        time_str: 时间字符串，格式 YYYYMMDDhhmmss
        
    Returns:
        datetime对象，如果解析失败返回None
    """
    try:
        return datetime.strptime(time_str, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def find_target_lesson(client: AHUCourseSelector, target: Dict[str, Any]) -> Optional[Dict]:
    """根据 list.json 中的筛选条件查找课程"""
    filter_obj: CourseFilter = target["filter"]
    course_name = filter_obj.course_name or ""
    course_id = target.get("course_id")

    client.ui.step(f"查询课程: {course_name or '未命名课程'}")
    page_no = 1
    page_size = 200  # 提升单页数量减少分页次数
    total_pages = 1
    while page_no <= total_pages:
        result = client.query_lessons(
            course_id=course_id,
            course_name=course_name,
            page_no=page_no,
            page_size=page_size,
        )
        lessons = result.get('lessons', [])
        page_info = result.get('pageInfo', {}) or {}
        total_pages = max(total_pages, int(page_info.get('totalPages') or 1))

        filtered = client.filter_lessons(lessons, filter_obj)
        if filtered:
            lesson = filtered[0]
            client._success(f"找到目标课程 (第 {page_no}/{total_pages} 页)")
            client.print_lesson_info(lesson)
            return lesson

        page_no += 1

    client._error(f"未找到符合条件的课程 (共 {total_pages} 页)")
    return None


def search_courses_interactive(client: AHUCourseSelector) -> List[Dict[str, Any]]:
    """按用户输入关键词搜索课程并选择，分页显示（每页10条），清屏翻页。"""
    selected: List[Dict[str, Any]] = []

    while True:
        keyword = client.ui.question("输入课程关键词(支持正则)")
        if not keyword:
            client._warn("关键词为空，取消搜索")
            return selected

        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error as exc:
            client._error(f"正则无效: {exc}")
            continue

        page_no = 1
        page_size = 200
        matches: List[Dict[str, Any]] = []

        while True:
            result = client.query_lessons(course_name=keyword, page_no=page_no, page_size=page_size)
            lessons = result.get('lessons', [])
            if not lessons:
                break
            for lesson in lessons:
                course = lesson.get('course', {})
                text_fields = [
                    course.get('nameZh', ''),
                    course.get('code', ''),
                    lesson.get('name', ''),
                    lesson.get('lessonCode', ''),
                    lesson.get('dateTimePlace', {}).get('textZh', ''),
                ]
                teachers = lesson.get('teachers', [])
                text_fields.extend([t.get('nameZh', '') for t in teachers])
                combined = ' '.join([str(x or '') for x in text_fields])
                if pattern.search(combined):
                    matches.append(lesson)

            page_info = result.get('pageInfo', {}) or {}
            total_pages = int(page_info.get('totalPages') or 1)
            if page_no >= total_pages:
                break
            page_no += 1

        if not matches:
            client._warn("未找到匹配课程")
            continue

        page = 0
        while True:
            clear_screen()
            start = page * 10
            end = start + 10
            chunk = matches[start:end]
            if not chunk:
                client._warn("没有更多结果")
                break
            client.ui.info(f"匹配结果 {start + 1}-{min(end, len(matches))}/{len(matches)} (输入 1-10 选择，11 下一页，0 退出)")
            for idx, lesson in enumerate(chunk, 1):
                course = lesson.get('course', {})
                time_place = lesson.get('dateTimePlace', {}).get('textZh', '')
                teachers = ','.join([t.get('nameZh', '') for t in lesson.get('teachers', [])])
                print(f"[{idx}] lessonId={lesson.get('id')} {course.get('nameZh','')} {course.get('code','')} | {teachers} | {time_place}")

            choice = client.ui.question("选择")
            if not choice.isdigit():
                continue
            num = int(choice)
            if num == 0:
                break
            if num == 11:
                page += 1
                continue
            if 1 <= num <= len(chunk):
                selected_lesson = chunk[num - 1]
                selected.append(selected_lesson)
                client._success(f"已选择: {selected_lesson.get('course', {}).get('nameZh', '')} (lessonId={selected_lesson.get('id')})")
                cont = client.ui.question("返回搜索页面继续搜其它课程? (y/n)").lower()
                if cont == 'y':
                    break  # 跳出分页，回到重新输入关键词
                return selected
            else:
                continue

        cont_search = client.ui.question("继续输入新关键词搜索? (y/n)").lower()
        if cont_search != 'y':
            break

    return selected


def main():
    """主函数 - 自动抢课"""
    ui = ConsoleUI()
    ui.banner("安徽大学选课系统 - 自动抢课脚本")
    
    # 显示系统当前时间
    current_time = datetime.now()
    ui.info(f"系统当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. 初始化客户端并加载凭证
    client = AHUCourseSelector(ui=ui)
    
    # 初次同步阿里云时间（仅供显示）
    ui.step("阿里云NTP时间对齐 (展示用)")
    try:
        ntp_client = ntplib.NTPClient()
        response = ntp_client.request('ntp.aliyun.com', version=3, timeout=5)
        ntp_time = response.tx_time
        local_time = time.time()
        offset = ntp_time - local_time
        ntp_datetime = datetime.fromtimestamp(ntp_time)
        ui.success(f"阿里云时间: {ntp_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
        ui.info(f"时间偏差: {offset} 秒 {'(本地时间慢)' if offset > 0 else '(本地时间快)'}")
    except Exception as e:
        ui.error(f"初次NTP同步失败: {e}")
        ui.info("将继续使用本地时间")
    
    if not load_credentials_with_retry(client):
        ui.warn("程序退出")
        return

    # 初次NTP测试后立即验证凭证，再进入时间输入环节
    if not verify_credentials(client):
        ui.error("凭证无效，请重新加载")
        if not load_credentials_with_retry(client):
            ui.warn("程序退出")
            return
        if not verify_credentials(client):
            ui.error("凭证仍无效，程序退出")
            return
    
    # 2. 获取目标时间
    while True:
        ui.step("请输入抢课时间 (格式: YYYYMMDDhhmmss)")
        ui.info("示例: 20251208153000 表示 2025年12月8日15时30分00秒")
        time_str = ui.question("抢课时间")
        
        target_time = parse_target_time(time_str)
        if target_time:
            ui.success(f"目标时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            break
        else:
            ui.error("时间格式错误，请重新输入")
    
    # 3. 提前3分钟验证凭证，期间每3分钟同步一次系统时间
    verify_time = target_time - timedelta(minutes=3)
    now = datetime.now()
    
    if now < verify_time:
        wait_seconds = (verify_time - now).total_seconds()
        ui.info(f"将在 {verify_time.strftime('%H:%M:%S')} 验证凭证 (距离 {int(wait_seconds)} 秒)")
        
        # 等待到验证时间（静态显示），每3分钟同步一次系统时间
        last_print = time.time()
        last_sync = time.time()
        while datetime.now() < verify_time:
            remaining = (verify_time - datetime.now()).total_seconds()
            
            # 每3分钟同步一次系统时间
            if time.time() - last_sync >= 180:  # 180秒 = 3分钟
                sync_time = datetime.now()
                print(f"\r系统时间同步: {sync_time.strftime('%Y-%m-%d %H:%M:%S')}     ")
                last_sync = time.time()
                last_print = time.time()
            
            if remaining > 0 and time.time() - last_print >= 5.0:  # 每5秒更新一次
                print(f"\r距离验证还有 {int(remaining)} 秒...  ", end='', flush=True)
                last_print = time.time()
            time.sleep(1)
        print()
    
    # 验证凭证
    if not verify_credentials(client):
        ui.error("凭证验证失败，请重新加载凭证")
        if not load_credentials_with_retry(client):
            ui.warn("程序退出")
            return
        
        # 再次验证
        if not verify_credentials(client):
            ui.error("凭证依然无效，程序退出")
            return
    
    ui.success("凭证有效，准备抢课")

    # 预热：访问完整课程选择链接，模拟浏览器页面
    ui.step("预热课程选择页面")
    client.warmup_course_page()
    
    # 4. 读取课程目标列表；若为空提供交互选课或重新指定路径
    lesson_targets: List[Dict[str, Any]] = []
    while True:
        targets = load_course_targets(ui=ui)
        if targets:
            break
        ui.warn("未找到课程配置")
        ui.bullet_list([
            "1) 搜索课程并选择",
            "2) 指定 list.json 路径",
            "3) 退出",
        ])
        choice = ui.question("选择 (1/2/3)")
        if choice == '1':
            selected_lessons = search_courses_interactive(client)
            if selected_lessons:
                for idx, lesson in enumerate(selected_lessons, 1):
                    course = lesson.get('course', {})
                    lesson_targets.append({
                        "lesson": lesson,
                        "priority": idx,
                        "name": course.get('nameZh', ''),
                        "filter": CourseFilter(course_name=course.get('nameZh', '')),
                        "course_id": course.get('id'),
                    })
                break
            else:
                continue
        if choice == '2':
            new_path = ui.question("list.json 路径")
            if new_path:
                targets = load_course_targets(new_path, ui=ui)
                if targets:
                    break
                ui.warn("指定文件为空或无效")
            continue
        if choice == '3':
            ui.warn("程序退出")
            return
        ui.warn("无效选项，请重试")

    # 5. 查找所有目标课程（文件配置）
    if targets:
        for target in targets:
            lesson = find_target_lesson(client, target)
            if lesson:
                lesson_targets.append({
                    "lesson": lesson,
                    "priority": target["priority"],
                    "name": target["filter"].course_name,
                    "filter": target["filter"],
                    "course_id": target.get("course_id"),
                })

    if not lesson_targets:
        ui.error("未找到可用的课程，程序退出")
        return
    
    # 按优先级排序
    lesson_targets.sort(key=lambda x: x["priority"])
    
    ui.success(f"共找到 {len(lesson_targets)} 个目标课程")
    for i, target in enumerate(lesson_targets, 1):
        lesson = target["lesson"]
        ui.info(f"[{i}] {target['name']} - lessonId={lesson['id']}")
        ui.info(f"      {lesson.get('dateTimePlace', {}).get('textZh', '')}")

    # 可选：对优先课程执行强制多次预检请求
    try:
        force_choice = ui.question("是否对优先课程强制发送多次预检请求? (y/n)").lower()
    except Exception:
        force_choice = 'n'

    if force_choice == 'y' and lesson_targets:
        default_attempts = ui.question("尝试次数 (回车默认10)")
        try:
            attempts = int(default_attempts) if default_attempts else 10
        except Exception:
            attempts = 10
        top = lesson_targets[0]
        top_lesson = top['lesson']
        ui.info(f"将对优先课程 {top.get('name')} (lessonId={top_lesson['id']}) 进行 {attempts} 次强制请求")
        success_force = client.force_send_requests(top_lesson['id'], attempts=attempts, interval=0.25)
        if success_force:
            ui.success("已检测到选课成功，程序退出")
            return
    
    # 6. 提前10秒同步NTP时间
    sync_time = target_time - timedelta(seconds=10)
    now = datetime.now()
    
    if now < sync_time:
        wait_seconds = (sync_time - now).total_seconds()
        ui.info(f"将在 {sync_time.strftime('%H:%M:%S')} 同步NTP时间 (距离 {int(wait_seconds)} 秒)")
        
        # 静态等待，每5秒更新一次
        last_print = time.time()
        while datetime.now() < sync_time:
            remaining = (sync_time - datetime.now()).total_seconds()
            if remaining > 0 and time.time() - last_print >= 5.0:
                print(f"\r距离NTP同步还有 {int(remaining)} 秒...  ", end='', flush=True)
                last_print = time.time()
            time.sleep(1)
        print()
    
    # 同步时间
    time_offset = client.sync_time_with_ntp()

    # 同步后对最高优先课程执行完整“搜索-预检-查询结果”流程，验证凭证与会话
    if lesson_targets:
        top_target = lesson_targets[0]
        try:
            ui.step("优先课程预检流程（搜索→提交→查询结果）")
            result = client.query_lessons(
                course_id=top_target.get("course_id"),
                course_name=top_target.get("name", ""),
                page_no=1,
                page_size=10,
            )
            lessons = result.get("lessons", [])
            filtered = client.filter_lessons(lessons, top_target["filter"])
            if not filtered:
                ui.warn("预检: 未找到匹配课程，可能是筛选条件或时间段问题")
            else:
                warm_lesson = filtered[0]
                request_id = client.add_course_predicate(warm_lesson["id"], virtual_cost=0)
                if request_id:
                    client.get_predicate_response(request_id, max_retries=1)
        except Exception as exc:
            ui.warn(f"预检流程失败: {exc}")
    
    # 7. 依次尝试抢课
    success_any = False

    try:
        for i, target in enumerate(lesson_targets, 1):
            lesson = target["lesson"]
            lesson_id = lesson['id']
            course_name = target['name']
            
            ui.divider(f"尝试抢课 [{i}/{len(lesson_targets)}] {course_name} (lessonId={lesson_id})")
            
            success = client.rapid_select_course(lesson_id, target_time, time_offset)
            
            if success:
                success_any = True
                ui.success(f"抢课成功! 课程: {course_name}")
                try:
                    cont = ui.question("是否继续尝试下一个目标课程? (y/n)").lower()
                except Exception:
                    cont = 'n'
                if cont == 'y':
                    continue
                return
            else:
                ui.warn(f"第 {i} 个目标失败，尝试下一个...")
                # 重新获取批次信息，刷新session
                client.get_turn_info()

        # 循环结束：若有成功则不报全部失败
        if success_any:
            ui.success("抢课流程结束，已有课程成功")
        else:
            ui.error("所有课程都抢课失败")
    except KeyboardInterrupt:
        ui.warn("已手动中断 (Ctrl+C)")
        return


if __name__ == "__main__":
    main()
