---
feature: class-management
status: implemented     # phase 1-2 + v2 đã code + deploy dev; prod chờ release (migrations 0013 + 0015)
type: design
last-updated: 2026-06-06
author: Backend team
depends-on: [admin-users, teacher-grading, attempt-lifecycle]
---

# Class Management — Thiết kế & Core feature

> **Scope**: admin tạo/quản lý **lớp học (class)**; gán teacher + student vào lớp; và
> **tầng phân quyền (scoping)** để teacher chỉ thấy/chấm bài của học sinh trong lớp mình
> dạy. Đây là tầng nền mà `docs/teacher-grading/` dựa vào để giới hạn quyền chấm.
>
> Tài liệu cùng feature: [Frontend notes](class-management-frontend.md) ·
> [Test cases](class-management-testcases.md). Chi tiết API xem **Swagger**.
>
> **Ảnh hưởng tới feature đã có:** feature này **sửa RBAC** ở 2 chỗ — quyền chấm/comment
> (teacher-grading) và quyền xem attempt detail của teacher (attempt-lifecycle). Trước đây
> là "bất kỳ teacher xem/chấm mọi bài"; nay **scoped theo lớp**. Xem [§7](#7-tích-hợp--sửa-rbac-feature-đã-có).

---

## Mục lục

1. [Overview](#1-overview)
2. [Design decisions / Trade-offs](#2-design-decisions--trade-offs)
3. [DB schema — migration 0013](#3-db-schema--migration-0013)
4. [RBAC helper (lõi scoping)](#4-rbac-helper-lõi-scoping)
5. [API — Admin](#5-api--admin)
6. [API — Teacher](#6-api--teacher)
7. [Tích hợp — sửa RBAC feature đã có](#7-tích-hợp--sửa-rbac-feature-đã-có)
8. [Future v2 roadmap](#8-future-v2-roadmap)
9. [Appendix: files to change](#9-appendix-files-to-change)
10. [v2 — Multi-class & class views](#10-v2--multi-class--class-views)

---

## 1. Overview

- Admin tạo **lớp**; mỗi lớp có **1+ teacher** (role `teacher`) và **nhiều student** (role `student`).
- **Một student có thể thuộc NHIỀU lớp** *(v2 — đổi từ "1 lớp/HS" của v1; xem [§10](#10-v2--multi-class--class-views))*.
- Teacher chỉ thấy/chấm bài của student trong lớp mình dạy; ngoài ra teacher xem được **chi tiết lớp** (roster + tiến độ HS).
- Student xem được **lớp của mình** (giáo viên + bạn học).
- Admin có trang quản lý lớp; **xóa lớp chỉ khi đã kick hết teacher + student** (tránh xóa nhầm).

> **🛠️ Trạng thái triển khai:** §3–§9 (foundation + scoping, **phase 1-2**) + **§10 (v2 — multi-class
> + teacher class-detail + student my-classes)** đều **đã implement + deploy dev**
> ([class_service.py](../../services/class_service.py), [api/classes/](../../api/classes/), migration 0013 + 0015).
> **Prod chờ release** (migration 0013 + 0015 chưa apply prod).

Luồng teacher: đăng nhập → chọn 1 lớp mình dạy → xem danh sách bài đã nộp của lớp → chọn 1 bài → chấm (feature teacher-grading).

---

## 2. Design decisions / Trade-offs

| # | Quyết định | Phương án đã chọn | Phương án loại bỏ | Lý do |
|---|-----------|-------------------|-------------------|-------|
| 1 | Membership model | **2 junction table** `class_teachers` (N-N) + `class_students` (N-N) | Cột `profiles.class_id` | Junction đồng nhất cho cả teacher & student; PK `(class_id, *_id)` chặn trùng cùng lớp; dễ "kick" + đếm thành viên. |
| 2 | 1 student / nhiều lớp? | **HS thuộc NHIỀU lớp** *(v2 — đổi từ "1 lớp/HS" v1)* | 1 lớp/HS | Client đổi ý: HS học nhiều lớp cùng lúc. Bỏ `UNIQUE(student_id)` ([§10.1](#101-multi-class--đổi-data-model)). Helper `EXISTS`-based vẫn đúng. |
| 3 | "Lớp đang chọn" của teacher | **Stateless — FE gửi `classId` mỗi request** | Server lưu active class | Không state, không migration, đổi lớp = đổi param. |
| 4 | Quyền chấm/xem của teacher | **`teacher_shares_class_with(teacher, student)`** (chung lớp bất kỳ) | Đúng lớp đang chọn (theo classId request) | `classId` chỉ để *lọc danh sách*; authorization dựa "có chung lớp" → đã sẵn sàng cho multi-class (HS ở 2 lớp → cả 2 GV chấm được). |
| 5 | Xóa lớp | **Hard-delete, chỉ khi 0 thành viên** (app check → 400 nếu còn) | Soft-delete (`deleted_at`) | Lớp không sở hữu dữ liệu con (attempts gắn user, không gắn lớp) → xóa không mất gì. Luật "phải rỗng" đã chống nhầm. |
| 6 | List bài của lớp | **Mặc định tất cả đã nộp + `?status=pending\|all`** (loại abandoned) | Chỉ pending | Teacher xem lại được bài đã chấm; `pending` lọc `is_fully_graded=false`. |
| 7 | Thêm HS đã có lớp | **Cho thêm vào nhiều lớp** *(v2)*; chỉ chặn trùng **cùng** lớp (409) | Reject "đã thuộc lớp khác" (v1) | HS multi-class → không reject lớp khác; PK vẫn chặn add trùng cùng lớp. |
| 8 | Admin | **Bypass scoping — xem/chấm hết** | Admin cũng bị scope | Admin là quyền cao nhất. |
| 9 | Nơi đặt luật scoping | **Feature này sở hữu** RBAC helper; teacher-grading & attempt-lifecycle *tham chiếu* | Lặp luật ở mỗi feature | Single source of truth cho phân quyền lớp. |
| 10 | Teacher xem chi tiết lớp | **Roster + tiến độ học tập mỗi HS** (`submittedCount`, `averagePercentage` chỉ bài đã chấm xong, `pendingGradingCount`, `lastSubmittedAt`) | Chỉ hồ sơ cơ bản | Client muốn GV theo dõi tiến độ. Tiến độ **toàn cục theo HS** (attempt không gắn lớp). [§10.3](#103-api--teacher-class-detail) |
| 11 | Student xem lớp của mình | **`/api/me/classes` (+ detail): GV + bạn học** | Không cho HS xem | Client muốn HS biết lớp/GV/bạn học. [§10.4](#104-api--student-my-classes) |
| 12 | Privacy projection | **3 dạng theo người xem** (teacher↔HS: +email; student↔bạn học: chỉ tên; student↔GV: +email) | Cùng 1 projection | Bạn học không lộ email cho nhau; GV có email để liên hệ. [§10.5](#105-profile-projections-privacy) |

---

## 3. DB schema — migration 0013

`migrations/0013_classes.sql` — additive, idempotent:

```sql
CREATE TABLE IF NOT EXISTS public.classes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.class_teachers (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  teacher_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, teacher_id)
);
CREATE INDEX IF NOT EXISTS class_teachers_teacher_idx ON public.class_teachers (teacher_id);

CREATE TABLE IF NOT EXISTS public.class_students (
  class_id   uuid NOT NULL REFERENCES public.classes(id)  ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (class_id, student_id),
  UNIQUE (student_id)        -- v1: ép 1 lớp / HS — ⚠️ BỎ ở v2 (§10.1)
);
CREATE INDEX IF NOT EXISTS class_students_class_idx ON public.class_students (class_id);
```

Lưu ý:
- `PRIMARY KEY (class_id, teacher_id/student_id)` cho lookup theo `class_id` (prefix); `class_teachers_teacher_idx` cho "lớp teacher dạy".
- ⚠️ **`UNIQUE(student_id)` chỉ tồn tại ở v1** (ép 1-lớp/HS) — **v2 drop** để HS đa lớp ([§10.1](#101-multi-class--đổi-data-model)). PK `(class_id, student_id)` vẫn chặn trùng cùng lớp.
- FK membership → classes `ON DELETE CASCADE` chỉ là vệ sinh; **luật "xóa khi rỗng" enforce ở app** ([§5](#5-api--admin)).
- `schema.sql` update lockstep.

---

## 4. RBAC helper (lõi scoping)

Có **2 predicate** khác nhau — đừng nhầm:

### 4.1 `teacher_shares_class_with(teacher_id, student_id)` — kiểm theo **học sinh**
Dùng khi thao tác trên **bài của 1 HS cụ thể** (grade/comment, attempt detail).

```sql
SELECT EXISTS (
  SELECT 1 FROM public.class_teachers ct
  JOIN public.class_students cs ON cs.class_id = ct.class_id
  WHERE ct.teacher_id = $1 AND cs.student_id = $2
);
```

### 4.2 `teacher_teaches_class(teacher_id, class_id)` — kiểm theo **lớp**
Dùng khi thao tác **ở phạm vi 1 lớp cụ thể** (list submissions của lớp).

```sql
SELECT EXISTS (
  SELECT 1 FROM public.class_teachers
  WHERE teacher_id = $1 AND class_id = $2
);
```

- Service: cả hai ở `class_service`.
- Dùng ở:
  - `teacher_shares_class_with` → teacher-grading (grade/comment), attempt-lifecycle (attempt detail khi viewer là teacher).
  - `teacher_teaches_class` → `GET /api/teacher/classes/{id}/submissions` ([§6](#6-api--teacher)).
- Với 1-lớp/HS, hai predicate **đồng nhất về kết quả** khi HS thuộc lớp đó, nhưng **cài đặt khác nhau** (per-student vs per-class) → dùng đúng cái theo ngữ cảnh.
- **Admin bypass** — không gọi helper nào, xem/chấm hết.

---

## 5. API — Admin

Mount `/api/admin/classes`, router-level `require_admin`.

| Method | Path | Hành vi & lỗi chính |
|---|---|---|
| POST | `/api/admin/classes` | Tạo `{name (required), description?}` → **201** `{class:{id,name,description,teacherCount:0,studentCount:0,createdAt}}` |
| GET | `/api/admin/classes` | List `{items:[{id,name,description,teacherCount,studentCount,createdAt}]}` |
| GET | `/api/admin/classes/{id}` | Detail `{class:{...,teachers:[{id,fullName,email}],students:[{id,fullName,email}]}}`; 404 nếu không có |
| PATCH | `/api/admin/classes/{id}` | Sửa `{name?,description?}` → class đã update. **Set `updated_at=now()`** (cột không tự đổi). |
| DELETE | `/api/admin/classes/{id}` | **Chỉ khi 0 teacher & 0 student** → **204**; còn thành viên → **400** `Class has N members; remove all first`; 404 |
| POST | `/api/admin/classes/{id}/teachers` | `{teacherId}` → **201**. 400 nếu user role≠teacher; 404 class/user; **409** nếu đã là teacher của lớp |
| DELETE | `/api/admin/classes/{id}/teachers/{teacherId}` | **204**; 404 nếu không phải member |
| POST | `/api/admin/classes/{id}/students` | `{studentId}` → **201**. 400 nếu role≠student; 404 class/user; **409** nếu đã ở **chính lớp này**. *(v2: HS được thêm vào nhiều lớp — **bỏ** lỗi 400 "đã thuộc lớp khác"; xem [§10.2](#102-sửa-admin-add-student-multi-class).)* |
| DELETE | `/api/admin/classes/{id}/students/{studentId}` | **204**; 404 nếu không phải member |

> FE lấy danh sách teacher/student để gán dùng `GET /api/admin/users?role=teacher\|student` (sẵn có) — không cần endpoint mới.
> *(v2: HS đa lớp → không còn khái niệm "chuyển lớp"; muốn rời 1 lớp thì DELETE lớp đó, thêm lớp khác thì POST — độc lập.)*

---

## 6. API — Teacher

Router prefix `/api/teacher`, `require_teacher_or_admin`. **Định nghĩa trong
`api/classes/routes.py`** (một teacher-router riêng với prefix `/api/teacher`), tách khỏi
router admin cùng file. *(Không nhét vào `api/teacher/routes.py` của teacher-grading — giữ
code lớp ở một chỗ.)*

| Method | Path | Hành vi & lỗi |
|---|---|---|
| GET | `/api/teacher/classes` | Các lớp **tôi dạy** `{items:[{id,name,studentCount,pendingGradingCount}]}`. Admin: thấy tất cả lớp. `pendingGradingCount` = số bài chờ chấm của lớp (HS trong lớp, submitted, không abandoned, `is_fully_graded=false`) — để teacher thấy ngay từ danh sách lớp (demo gọn). |
| GET | `/api/teacher/classes/{id}` *(v2)* | **Chi tiết lớp**: roster HS + tiến độ học tập + co-teachers. Auth `teacher_teaches_class(me,id)` → else 403; 404 nếu lớp không có. Shape + query: [§10.3](#103-api--teacher-class-detail). |
| GET | `/api/teacher/classes/{id}/submissions?status=pending\|all` | Bài nộp của HS trong lớp `id`. **Auth: `teacher_teaches_class(me, id)`** ([§4.2](#42-teacher_teaches_classteacher_id-class_id--kiểm-theo-lớp)) → else **403**; admin bypass. Loại **abandoned**. `status=all` (default) = mọi bài submitted; `status=pending` = `is_fully_graded=false`. Mỗi item: `{attemptId, student:{id,fullName}, exam:{id,title,level,skill}, submittedAt, isFullyGraded, score, totalPoints, percentage}`. 404 nếu lớp không tồn tại. |

Query gốc của submissions:
```sql
SELECT a.*, p.full_name, e.title, ...
FROM attempts a
JOIN class_students cs ON cs.student_id = a.user_id AND cs.class_id = $1
JOIN profiles p ON p.id = a.user_id
JOIN exams e ON e.id = a.exam_id
WHERE a.submitted_at IS NOT NULL AND NOT a.is_abandoned
  -- AND a.is_fully_graded = false   (khi status=pending)
ORDER BY a.submitted_at DESC
```

`pendingGradingCount` cho `GET /api/teacher/classes`: tính 1 lần bằng aggregate (tránh
N+1) — vd `LEFT JOIN` lớp với count attempts pending của HS lớp đó, GROUP BY class:
```sql
SELECT c.id, c.name, COUNT(DISTINCT cs2.student_id) AS student_count,
       COUNT(a.id) FILTER (
         WHERE a.submitted_at IS NOT NULL AND NOT a.is_abandoned AND NOT a.is_fully_graded
       ) AS pending_grading_count
FROM classes c
JOIN class_teachers ct ON ct.class_id = c.id AND ct.teacher_id = $1   -- lớp tôi dạy
LEFT JOIN class_students cs2 ON cs2.class_id = c.id
LEFT JOIN attempts a ON a.user_id = cs2.student_id
GROUP BY c.id, c.name
```

---

## 7. Tích hợp — sửa RBAC feature đã có

> ✅ **Đã implement (phase 2 — commit `feat(classes): phase 2`).** Mục này giờ là *tài liệu hành vi*, không còn là việc cần làm.

### 7.1 teacher-grading
- `POST /grade`, các endpoint comment writing/speaking: trước khi thao tác, load `attempt.user_id` rồi check `teacher_shares_class_with(current_teacher, student)` (admin bypass) → else **403**.
- Cập nhật `docs/teacher-grading/` mục RBAC: "any teacher" → "teacher phải dạy lớp của HS".

### 7.2 attempt-lifecycle (attempt detail)
- `GET /api/attempts/{id}` khi viewer là **teacher**: cùng check shared-class → else 403. Owner/admin/parent giữ nguyên.
- Cập nhật `docs/attempt-lifecycle/` mục visibility (V10 "teacher xem mọi bài" → scoped).

> Lưu ý: luật strip đáp án ở attempt-lifecycle đã chốt **state-based** (2026-06-03) — code đã đúng, không cần sửa kèm. Đợt này chỉ thêm **class-scoping cho quyền MỞ detail** của teacher (owner/admin/parent giữ nguyên).

---

## 8. Future v2 roadmap

- ~~HS thuộc nhiều lớp~~ → **đã đưa vào v2** ([§10](#10-v2--multi-class--class-views)).
- **Server-stored active class** — nếu cần nhớ lớp đang chọn xuyên thiết bị.
- **Gán exam theo lớp** — lớp được giao đề cụ thể (hiện exam là chung) → khi đó tiến độ HS mới tách được "theo lớp" thay vì toàn cục.
- **Tiến độ/analytics theo lớp** — hiện tiến độ HS là toàn cục (mọi đề); per-class cần exam↔lớp.

---

## 9. Appendix: files to change

### 9.1 Đã có (foundation + scoping, phase 1-2 — ✅ implement)
| File | Trạng thái |
|------|-----------|
| `migrations/0013_classes.sql` | ✅ 3 bảng + index (đã apply dev) |
| `api/classes/{__init__,routes,schemas}.py` | ✅ Admin + teacher endpoints (2 router) |
| [services/class_service.py](../../services/class_service.py) | ✅ CRUD + membership + 2 helper + list-submissions |
| [services/grading_service.py](../../services/grading_service.py), [services/comment_service.py](../../services/comment_service.py), [services/attempt_service.py](../../services/attempt_service.py) | ✅ scoping qua `teacher_shares_class_with` (phase 2) |
| [main.py](../../main.py), `schema.sql` | ✅ mount router + mirror 0013 |

### 9.2 v2 — multi-class + class views (✅ implemented + deployed dev)
| File | Change | Trạng thái |
|------|---------|-----------|
| `migrations/0015_class_students_multi.sql` | **Drop** `class_students_student_id_key` ([§10.1](#101-multi-class--đổi-data-model)) | ✅ applied dev (⚠️ prod chờ release) |
| `services/class_service.py` `add_student` | Bỏ pre-check "lớp khác"; chỉ catch UniqueViolation → 409 cùng lớp | ✅ |
| `services/class_service.py` | `get_teacher_class_detail` + `list_student_classes` + `get_student_class_detail` | ✅ |
| `dependencies.py` | `require_student` | ✅ |
| `api/classes/routes.py` + `schemas.py` + `__init__.py` | `GET /api/teacher/classes/{id}` + `me_router` `/api/me/classes` (+detail) | ✅ |
| `main.py` | mount `me_router` | ✅ |
| `schema.sql` | Bỏ dòng `UNIQUE (student_id)` | ✅ |
| `tests/` | CL1/MS3/MS5/R8/R2 sang multi-class + `test_class_v2_views.py` (TD*/ME*) | ✅ |

---

## 10. v2 — Multi-class & class views

> Mở rộng feature: (1) HS đa lớp; (2) teacher xem chi tiết lớp + tiến độ HS; (3) student xem
> lớp/GV/bạn học. **✅ Đã implement + deploy dev** (migration 0015 đã apply dev; **prod chờ release**).
> Foundation §3–§9 không đổi ngoài các điểm dưới.

### 10.1 Multi-class — đổi data model
```sql
-- migrations/0015_class_students_multi.sql  (số trống kế tiếp: 0014 đã là form_completion)
ALTER TABLE public.class_students
  DROP CONSTRAINT IF EXISTS class_students_student_id_key;   -- bỏ ép 1-lớp/HS
```
- PK `(class_id, student_id)` **giữ** → vẫn chặn add trùng cùng lớp.
- Không migrate data (HS đang 1 lớp vẫn hợp lệ). `schema.sql` bỏ dòng `UNIQUE (student_id)`.
- **Không đụng**: 2 helper, scoping grade/comment/detail, `list_teacher_classes`, submissions — đều `EXISTS`/join-based, đã multi-class-ready.
- **Semantic mới**: attempt không gắn lớp (chỉ user+exam). HS ở lớp A (GV T1) + B (GV T2) → bài chờ chấm hiện ở **cả 2 lớp**; T1 **hoặc** T2 chấm đều được (chấm xong rớt khỏi cả hai). `pendingGradingCount`/submissions có thể đếm cùng 1 bài ở nhiều lớp — **chấp nhận v1**.

### 10.2 Sửa admin add-student (multi-class)
`class_service.add_student`: **bỏ** pre-check "HS đã thuộc lớp khác" + race-handler. Còn:
```python
try: INSERT INTO class_students (class_id, student_id) VALUES ($1,$2)
except UniqueViolationError: raise AlreadyExistsError("Student is already in this class")  # 409
```
Validate `role='student'` giữ nguyên. `remove_student` không đổi.

> **Envelope:** mọi response v2 bọc chuẩn như §5/§6 — detail = `{status, data:{class:{…}}}`,
> list = `{status, data:{items:[…]}}`. JSON dưới chỉ ghi phần `class`/item cho gọn.

### 10.3 API — Teacher class-detail
`GET /api/teacher/classes/{id}` · `require_teacher_or_admin` + `teacher_teaches_class(me,id)` (admin bypass) → **403**; **404** nếu lớp không có.
```jsonc
{ "id","name","description","createdAt",
  "teachers":[{ "id","fullName" }],                    // co-teachers (KHÔNG email)
  "students":[{ "id","fullName","email",               // hồ sơ + tiến độ TOÀN CỤC theo HS
     "submittedCount":12, "averagePercentage":78.5,    // avg null nếu chưa có bài đã chấm xong
     "pendingGradingCount":2, "lastSubmittedAt":"..." }] }
```
Query tiến độ (1 aggregate, tránh N+1):
```sql
SELECT cs.student_id, p.full_name, p.email,
  COUNT(a.id) FILTER (WHERE a.submitted_at IS NOT NULL AND NOT a.is_abandoned)                            AS submitted_count,
  AVG(a.percentage) FILTER (WHERE a.is_fully_graded AND a.submitted_at IS NOT NULL AND NOT a.is_abandoned) AS avg_pct,
  COUNT(a.id) FILTER (WHERE a.submitted_at IS NOT NULL AND NOT a.is_abandoned AND NOT a.is_fully_graded)   AS pending_count,
  MAX(a.submitted_at) FILTER (WHERE a.submitted_at IS NOT NULL AND NOT a.is_abandoned)                     AS last_submitted_at
FROM public.class_students cs JOIN public.profiles p ON p.id = cs.student_id
LEFT JOIN public.attempts a ON a.user_id = cs.student_id
WHERE cs.class_id = $1 GROUP BY cs.student_id, p.full_name, p.email ORDER BY p.full_name
```
- `averagePercentage` chỉ tính trên bài **đã chấm xong** (`is_fully_graded=true`, loại abandoned); làm tròn 2 chữ số ở service; `null` nếu chưa có.
- **Toàn cục theo HS** (attempt không gắn lớp) — không phải "tiến độ trong lớp này".

### 10.4 API — Student my-classes
Student router (cùng `api/classes/routes.py`, prefix `/api/me`, `require_student` hoặc check membership).

| Method | Path | Hành vi |
|---|---|---|
| GET | `/api/me/classes` | Lớp **em đang học** `{items:[{id,name,description,teacherCount,studentCount}]}`. Rỗng nếu chưa vào lớp nào. |
| GET | `/api/me/classes/{id}` | Em **phải là thành viên** → else **404** (không lộ tồn tại lớp). Trả GV + bạn học. |

```jsonc
// GET /api/me/classes/{id}
{ "id","name","description",
  "teachers":[{ "id","fullName","email" }],   // GV: CÓ email liên hệ
  "classmates":[{ "id","fullName" }] }         // bạn học: CHỈ tên, ĐÃ loại chính em
```

### 10.5 Profile projections (privacy)
Cùng `profiles` nhưng **3 projection khác nhau theo người xem** — serializer phải tách rõ:

| Endpoint | Người được liệt kê | Field trả |
|---|---|---|
| teacher `/classes/{id}` | students | id, fullName, **email** + 4 field tiến độ |
| teacher `/classes/{id}` | co-teachers | id, fullName |
| student `/me/classes/{id}` | teachers | id, fullName, **email** |
| student `/me/classes/{id}` | classmates | id, fullName *(loại chính em)* |

### 10.6 RBAC tổng hợp (v2)
| Endpoint | Auth | Lỗi chính |
|---|---|---|
| `GET /teacher/classes/{id}` | teacher dạy lớp / admin | 403 không dạy lớp · 404 lớp |
| `GET /me/classes` | student (self) | — (rỗng) |
| `GET /me/classes/{id}` | student là thành viên | 404 nếu không phải thành viên / không tồn tại |
| `POST /admin/.../students` | admin | 409 trùng **cùng** lớp (bỏ 400 "lớp khác") |
