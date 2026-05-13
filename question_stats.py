# -*- coding: utf-8 -*-
"""
问题统计模块 v30
========================================
- SQLite 存储，零依赖
- Coze 分类前缀作为主聚类依据
- 嵌入模型语义相似度作为降级兜底（可选）
- 面向 RAG 知识库 & Web 后台可视化
========================================
"""
import sqlite3
import time
import logging
import os
import re
import shutil
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "questions.db")

_embedding_model = None

# ==================== 数据库 ====================
def init_db(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS question_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT UNIQUE NOT NULL,
            representative_text TEXT DEFAULT '',
            count INTEGER DEFAULT 0,
            embedding BLOB,
            first_seen REAL DEFAULT 0,
            last_seen REAL DEFAULT 0,
            best_reply_body TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id INTEGER,
            raw_text TEXT DEFAULT '',
            cleaned_text TEXT DEFAULT '',
            nickname TEXT DEFAULT '',
            category TEXT DEFAULT '',
            reply_full TEXT DEFAULT '',
            reply_body TEXT DEFAULT '',
            timestamp REAL DEFAULT 0,
            chat_user_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (cluster_id) REFERENCES question_clusters(id)
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            created_at REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_questions_cluster_id ON questions(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_clusters_category ON question_clusters(category);
    """)
    try:
        conn.execute("ALTER TABLE questions ADD COLUMN status TEXT DEFAULT 'pending'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status)")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE question_clusters ADD COLUMN is_timely INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE question_clusters ADD COLUMN last_edited REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.execute("UPDATE questions SET status = 'replied' WHERE reply_body != '' AND (status = 'pending' OR status IS NULL)")
    # 默认管理员账号
    row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    if row and row[0] == 0:
        from werkzeug.security import generate_password_hash
        pw = "admin123"
        try:
            from config import WEB_PASSWORD
            pw = WEB_PASSWORD
        except ImportError:
            pass
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES (?, ?, 'admin', ?, ?)",
            ("admin", generate_password_hash(pw), time.time(), time.time())
        )
        logging.info("[Stats] 默认管理员账户已创建 (admin)")
    conn.commit()
    conn.close()
    logging.info("[Stats] 数据库初始化完成")


# ==================== Coze 回复解析 ====================
def parse_coze_category(reply_text):
    if not reply_text:
        return None, ""
    for sep in ("：", ":"):
        if sep in reply_text:
            parts = reply_text.split(sep, 1)
            candidate = parts[0].strip()
            if len(candidate) <= 20 and not re.search(r'[。！？\.\!\?]', candidate):
                logging.info(f"[Stats] Coze分类解析成功: 「{candidate}」")
                return candidate, parts[1].strip()
    logging.info(f"[Stats] Coze分类解析失败，回复前60字: {reply_text[:60]}")
    return None, reply_text.strip()


def _match_managed_category(reply_text):
    if not reply_text:
        return None
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name FROM categories WHERE enabled = 1 ORDER BY length(name) DESC").fetchall()
    conn.close()
    for (name,) in rows:
        if name in reply_text:
            logging.info(f"[Stats] 管理分类匹配成功: 「{name}」")
            return name
    logging.info(f"[Stats] 管理分类匹配失败，回复前60字: {reply_text[:60]}")
    return None


# ==================== 语义相似度兜底 ====================
def _load_embedding_model():
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model
    try:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
        logging.info("[Stats] 嵌入模型加载完成 (BAAI/bge-small-zh-v1.5)")
        return _embedding_model
    except ImportError:
        logging.info("[Stats] sentence-transformers 未安装，语义去重降级为纯文本匹配")
        return None
    except Exception as e:
        logging.warning(f"[Stats] 嵌入模型加载失败: {e}")
        return None


def _encode(text):
    model = _load_embedding_model()
    if model is None:
        return None
    return model.encode(text, normalize_embeddings=True)


def _cosine(a, b):
    return float(np.dot(a, b))


# ==================== 核心 API ====================
class QuestionStats:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        init_db(self.db_path)
        self._auto_backup()

    def _auto_backup(self):
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        if not os.path.exists(self.db_path):
            return
        now = time.time()
        today_key = time.strftime("%Y%m%d")
        today_file = os.path.join(backup_dir, f"questions_{today_key}.db")
        if not os.path.exists(today_file):
            shutil.copy2(self.db_path, today_file)
            logging.info(f"[备份] 已创建今日备份: {today_file}")
        cutoff = now - 30 * 86400
        for f in os.listdir(backup_dir):
            if f.startswith("questions_") and f.endswith(".db"):
                fpath = os.path.join(backup_dir, f)
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    logging.info(f"[备份] 清理过期备份: {f}")

    def _maybe_backup(self):
        backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        if not os.path.exists(self.db_path):
            return
        today_key = time.strftime("%Y%m%d")
        hour_key = time.strftime("%H")
        today_hour_file = os.path.join(backup_dir, f"questions_{today_key}_{hour_key}.db")
        if not os.path.exists(today_hour_file):
            shutil.copy2(self.db_path, today_hour_file)
            logging.info(f"[备份] 已创建小时备份: {today_hour_file}")
            cutoff = time.time() - 30 * 86400
            for f in os.listdir(backup_dir):
                if f.startswith("questions_") and f.endswith(".db"):
                    fpath = os.path.join(backup_dir, f)
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        logging.info(f"[备份] 清理过期备份: {f}")

    def record(self, question, nickname="", reply_full="", chat_user_id=""):
        category, reply_body = parse_coze_category(reply_full)
        if not category:
            category = _match_managed_category(reply_full)
        conn = sqlite3.connect(self.db_path)
        now = time.time()

        try:
            if category:
                cluster_id = self._upsert_category_cluster(conn, category, question, reply_body, now)
            else:
                cluster_id = self._upsert_embedding_cluster(conn, question, reply_body, now)

            conn.execute(
                "INSERT INTO questions (cluster_id, raw_text, cleaned_text, nickname, category, reply_full, reply_body, timestamp, chat_user_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (cluster_id, question, question, nickname, category or "", reply_full, "", now, chat_user_id)
            )

            conn.execute(
                "UPDATE question_clusters SET count = count + 1, last_seen = ? WHERE id = ?",
                (now, cluster_id)
            )

            conn.execute(
                "UPDATE question_clusters SET representative_text = ? WHERE id = ? AND representative_text = ''",
                (question, cluster_id)
            )

            conn.commit()
            logging.info(f"[Stats] ✓ 记录 分类={category or '(未分类)'}  cluster=#{cluster_id}")
            self._maybe_backup()
        except Exception as e:
            logging.error(f"[Stats] 记录失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    def _upsert_category_cluster(self, conn, category, question, reply_body, now):
        row = conn.execute("SELECT id FROM question_clusters WHERE category = ?", (category,)).fetchone()
        if row:
            return row[0]
        cur = conn.execute(
            "INSERT INTO question_clusters (category, representative_text, count, first_seen, last_seen, best_reply_body) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (category, question, now, now, reply_body)
        )
        return cur.lastrowid

    def _upsert_embedding_cluster(self, conn, question, reply_body, now):
        emb = _encode(question)
        emb_bytes = emb.tobytes() if emb is not None else None

        if emb is not None:
            rows = conn.execute("SELECT id, embedding FROM question_clusters WHERE embedding IS NOT NULL").fetchall()
            best_id, best_sim = None, 0.0
            for cid, eb in rows:
                existing = np.frombuffer(eb, dtype=np.float32)
                sim = _cosine(emb, existing)
                if sim > best_sim:
                    best_sim, best_id = sim, cid

            if best_sim >= 0.85 and best_id:
                count_row = conn.execute("SELECT count FROM question_clusters WHERE id = ?", (best_id,)).fetchone()
                n = count_row[0] if count_row else 1
                old = np.frombuffer(
                    conn.execute("SELECT embedding FROM question_clusters WHERE id = ?", (best_id,)).fetchone()[0],
                    dtype=np.float32
                )
                centroid = (old * n + emb) / (n + 1)
                conn.execute("UPDATE question_clusters SET embedding = ? WHERE id = ?", (centroid.tobytes(), best_id))
                return best_id

        row = conn.execute("SELECT id FROM question_clusters WHERE category = ''").fetchone()
        if row:
            conn.execute(
                "UPDATE question_clusters SET last_seen = ?, representative_text = CASE WHEN representative_text = '' THEN ? ELSE representative_text END WHERE id = ?",
                (now, question, row[0])
            )
            return row[0]
        cur = conn.execute(
            "INSERT INTO question_clusters (category, representative_text, count, embedding, first_seen, last_seen, best_reply_body) "
            "VALUES (?, ?, 0, ?, ?, ?, ?)",
            ("", question, emb_bytes, now, now, reply_body)
        )
        return cur.lastrowid

    # ============ 查询接口 ============
    def get_top_clusters(self, n=20):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT id, category, representative_text, count, first_seen, last_seen "
            "FROM question_clusters ORDER BY count DESC LIMIT ?",
            (n,)
        ).fetchall()
        conn.close()
        return rows

    def get_cluster_detail(self, cluster_id):
        conn = sqlite3.connect(self.db_path)
        cluster = conn.execute("SELECT * FROM question_clusters WHERE id = ?", (cluster_id,)).fetchone()
        questions = conn.execute(
            "SELECT id, raw_text, cleaned_text, nickname, reply_full, reply_body, timestamp "
            "FROM questions WHERE cluster_id = ? ORDER BY timestamp DESC",
            (cluster_id,)
        ).fetchall()
        conn.close()
        return cluster, questions

    def get_all_questions(self, limit=500, offset=0):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT q.id, qc.category, q.cleaned_text, q.nickname, q.reply_body, q.timestamp, qc.count, q.status, qc.is_timely, qc.last_edited "
            "FROM questions q LEFT JOIN question_clusters qc ON q.cluster_id = qc.id "
            "ORDER BY q.timestamp DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        conn.close()
        return rows

    def export_qa_pairs(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT category, representative_text, best_reply_body, count "
            "FROM question_clusters WHERE best_reply_body != '' "
            "ORDER BY count DESC"
        ).fetchall()
        conn.close()
        return rows

    def get_summary(self):
        conn = sqlite3.connect(self.db_path)
        total_q = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        total_c = conn.execute("SELECT COUNT(*) FROM question_clusters").fetchone()[0]
        has_cat = conn.execute("SELECT COUNT(*) FROM question_clusters WHERE category != ''").fetchone()[0]
        cnt_replied = conn.execute("SELECT COUNT(*) FROM questions WHERE status = 'replied'").fetchone()[0]
        cnt_pending = conn.execute("SELECT COUNT(*) FROM questions WHERE status = 'pending'").fetchone()[0]
        cnt_dismissed = conn.execute("SELECT COUNT(*) FROM questions WHERE status = 'dismissed'").fetchone()[0]
        conn.close()
        return {
            "total_questions": total_q,
            "total_clusters": total_c,
            "categorized": has_cat,
            "uncategorized": total_c - has_cat,
            "cnt_replied": cnt_replied,
            "cnt_pending": cnt_pending,
            "cnt_dismissed": cnt_dismissed,
        }

    def get_categories(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT name FROM categories WHERE enabled = 1 ORDER BY sort_order, name"
        ).fetchall()
        managed = [r[0] for r in rows]
        auto_rows = conn.execute(
            "SELECT DISTINCT category FROM question_clusters WHERE category != '' ORDER BY category"
        ).fetchall()
        conn.close()
        for r in auto_rows:
            if r[0] not in managed:
                managed.append(r[0])
        return managed

    def get_categories_detail(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, description, sort_order, enabled FROM categories ORDER BY sort_order, name"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_category(self, name, description=""):
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        try:
            conn.execute(
                "INSERT INTO categories (name, description, sort_order, enabled, created_at) VALUES (?, ?, 0, 1, ?)",
                (name.strip(), description, now)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def update_category(self, category_id, name=None, description=None, enabled=None, sort_order=None):
        conn = sqlite3.connect(self.db_path)
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if sort_order is not None:
            updates.append("sort_order = ?")
            params.append(sort_order)
        if updates:
            params.append(category_id)
            conn.execute(f"UPDATE categories SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        conn.close()

    def delete_category(self, category_id):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
        conn.close()

    # ============ 用户管理 ============
    def get_users(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, username, role, created_at, updated_at FROM users ORDER BY id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_user_by_username(self, username):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def add_user(self, username, password_hash, role="user"):
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username.strip(), password_hash, role, now, now)
            )
            conn.commit()
            return True, ""
        except sqlite3.IntegrityError:
            return False, "用户名已存在"
        finally:
            conn.close()

    def update_user(self, user_id, username=None, password_hash=None, role=None):
        conn = sqlite3.connect(self.db_path)
        # 防止将最后一个管理员降级为普通用户
        if role is not None and role != 'admin':
            admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
            current = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            if current and current[0] == 'admin' and admin_count <= 1:
                conn.close()
                return False, "不能将最后一个管理员降级为普通用户"
        updates = []
        params = []
        if username is not None:
            updates.append("username = ?")
            params.append(username.strip())
        if password_hash is not None:
            updates.append("password_hash = ?")
            params.append(password_hash)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if updates:
            updates.append("updated_at = ?")
            params.append(time.time())
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        conn.close()
        return True, ""

    def delete_user(self, user_id):
        conn = sqlite3.connect(self.db_path)
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            conn.close()
            return False, "用户不存在"
        if row[0] == 'admin' and admin_count <= 1:
            conn.close()
            return False, "不能删除最后一个管理员"
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True, ""

    def delete_question(self, question_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT cluster_id FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
            conn.execute("UPDATE question_clusters SET count = count - 1 WHERE id = ?", (row[0],))
            conn.commit()
        conn.close()

    def batch_set_status(self, ids, status):
        conn = sqlite3.connect(self.db_path)
        placeholders = ','.join('?' * len(ids))
        conn.execute(f"UPDATE questions SET status = ? WHERE id IN ({placeholders})", [status] + ids)
        conn.commit()
        count = conn.total_changes
        conn.close()
        return count

    def batch_delete_questions(self, ids):
        conn = sqlite3.connect(self.db_path)
        placeholders = ','.join('?' * len(ids))
        # 更新 cluster count
        rows = conn.execute(f"SELECT cluster_id FROM questions WHERE id IN ({placeholders})", ids).fetchall()
        for (cid,) in rows:
            conn.execute("UPDATE question_clusters SET count = count - 1 WHERE id = ?", (cid,))
        conn.execute(f"DELETE FROM questions WHERE id IN ({placeholders})", ids)
        conn.commit()
        count = conn.total_changes
        conn.close()
        return count

    def search_questions(self, keyword=None, category=None, date_from=None, date_to=None,
                         reply_status=None, is_timely=None, limit=200, offset=0):
        conn = sqlite3.connect(self.db_path)
        conds = []
        params = []
        if keyword:
            conds.append("(q.cleaned_text LIKE ? OR qc.category LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if category:
            conds.append("qc.category = ?")
            params.append(category)
        if date_from:
            conds.append("q.timestamp >= ?")
            params.append(date_from)
        if date_to:
            conds.append("q.timestamp <= ?")
            params.append(date_to)
        if reply_status == "replied":
            conds.append("q.status = 'replied'")
        elif reply_status == "pending":
            conds.append("q.status = 'pending'")
        elif reply_status == "dismissed":
            conds.append("q.status = 'dismissed'")
        if is_timely is not None:
            conds.append("qc.is_timely = ?")
            params.append(1 if is_timely else 0)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""

        count_row = conn.execute(
            f"SELECT COUNT(*) FROM questions q LEFT JOIN question_clusters qc ON q.cluster_id = qc.id{where}",
            params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = conn.execute(
            f"SELECT q.id, qc.category, q.cleaned_text, q.nickname, q.reply_body, q.timestamp, "
            f"qc.id as cluster_id, q.reply_full, q.status, qc.is_timely, qc.last_edited "
            f"FROM questions q LEFT JOIN question_clusters qc ON q.cluster_id = qc.id"
            f"{where} ORDER BY q.timestamp DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        conn.close()
        return {"questions": rows, "total": total}

    def update_reply(self, question_id, reply_body):
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        conn.execute("UPDATE questions SET reply_body = ?, status = 'replied' WHERE id = ?", (reply_body, question_id))
        row = conn.execute("SELECT cluster_id FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE question_clusters SET best_reply_body = ?, last_edited = ? WHERE id = ? AND (best_reply_body = '' OR best_reply_body IS NULL)",
                (reply_body, now, row[0])
            )
        conn.commit()
        conn.close()

    def update_cluster_reply(self, cluster_id, reply_body):
        conn = sqlite3.connect(self.db_path)
        now = time.time()
        conn.execute("UPDATE question_clusters SET best_reply_body = ?, last_edited = ? WHERE id = ?", (reply_body, now, cluster_id))
        conn.execute("UPDATE questions SET reply_body = ?, status = 'replied' WHERE cluster_id = ?", (reply_body, cluster_id))
        conn.commit()
        conn.close()

    def toggle_timely(self, cluster_id):
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE question_clusters SET is_timely = 1 - is_timely WHERE id = ?", (cluster_id,))
        conn.commit()
        row = conn.execute("SELECT is_timely FROM question_clusters WHERE id = ?", (cluster_id,)).fetchone()
        conn.close()
        return row[0] if row else 0

    def set_question_status(self, question_id, status):
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE questions SET status = ? WHERE id = ?", (status, question_id))
        conn.commit()
        conn.close()

    def set_cluster_status(self, cluster_id, status):
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE questions SET status = ? WHERE cluster_id = ?", (status, cluster_id))
        conn.commit()
        conn.close()

    def get_question_detail(self, question_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT q.id, qc.category, q.cleaned_text, q.nickname, q.reply_body, q.timestamp, "
            "qc.id as cluster_id, q.reply_full, q.raw_text, q.status, qc.is_timely, qc.last_edited "
            "FROM questions q LEFT JOIN question_clusters qc ON q.cluster_id = qc.id "
            "WHERE q.id = ?", (question_id,)
        ).fetchone()
        conn.close()
        return row

    def export_excel(self, filepath, keyword=None, category=None, date_from=None, date_to=None, reply_status=None, is_timely=None):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "问题统计"
        ws.append(["ID", "分类", "问题", "昵称", "AI回复", "专业答复", "状态", "时效性", "时间", "聚类ID"])

        STATUS_MAP = {"pending": "待答复", "replied": "已答复", "dismissed": "不予处理"}
        result = self.search_questions(keyword, category, date_from, date_to, reply_status, is_timely, limit=10000, offset=0)
        for row in result["questions"]:
            timestamp = row[5]
            time_str = ""
            if timestamp:
                import datetime
                dt = datetime.datetime.fromtimestamp(timestamp)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            reply_full = row[7] or ""
            _, coze_body = parse_coze_category(reply_full)
            reply_body = row[4] or ""
            status_text = STATUS_MAP.get(row[8], row[8] if len(row) > 8 else "pending")
            is_timely = row[9] if len(row) > 9 else 0
            timely_text = "高时效" if is_timely else "普通"
            ws.append([row[0], row[1] or "", row[2], row[3] or "", coze_body, reply_body, status_text, timely_text, time_str, row[6] or ""])

        ws2 = wb.create_sheet("分类汇总")
        ws2.append(["分类", "代表提问", "专业答复", "次数"])
        pairs = self.export_qa_pairs()
        for p in pairs:
            ws2.append(list(p))

        wb.save(filepath)

    def export_rag_excel(self, filepath):
        import openpyxl
        import datetime
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "知识库"
        ws.append(["问题", "答案", "分类", "高时效性", "最后编辑时间", "建议更新"])

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT category, representative_text, best_reply_body, count, is_timely, last_edited "
            "FROM question_clusters WHERE best_reply_body != '' "
            "ORDER BY count DESC"
        ).fetchall()
        conn.close()
        now = time.time()
        for row in rows:
            category, question, answer, count, is_timely, last_edited = row
            timely_label = "是" if is_timely else "否"
            last_edited_str = ""
            if last_edited:
                dt = datetime.datetime.fromtimestamp(last_edited)
                last_edited_str = dt.strftime("%Y-%m-%d %H:%M")
            need_update = ""
            if is_timely and last_edited and (now - last_edited) > 7 * 86400:
                need_update = "建议更新"
            ws.append([question, answer, category, timely_label, last_edited_str, need_update])

        wb.save(filepath)
