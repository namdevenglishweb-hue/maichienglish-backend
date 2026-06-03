"""Class management service — CRUD + membership + RBAC scoping helpers.

Owns the business logic behind:
  - Admin class CRUD + teacher/student membership
    (docs/class-management/class-management-design.md §5)
  - Teacher "my classes" + class submissions listing (§6)
  - The two RBAC predicates other features reference for class-scoping (§4):
      * teacher_shares_class_with(teacher, student)  — per-student
      * teacher_teaches_class(teacher, class)        — per-class

No HTTP imports — routes map ServiceError subclasses to status codes.

Rules enforced here (not at the DB):
  - delete a class only when it has 0 teachers AND 0 students
  - a student belongs to at most one class (DB also enforces via
    UNIQUE(student_id); we pre-check to give a friendly 400 with the
    current class name)
"""

import logging
from typing import Any, Optional

import asyncpg

from services.exceptions import (
    AlreadyExistsError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)


class ClassService:
    def __init__(self, db_pool=None):
        self._db_pool = db_pool

    @property
    def db(self):
        if self._db_pool is None:
            from config.database import get_db_pool
            self._db_pool = get_db_pool()
        return self._db_pool

    # ================================================================== #
    # Admin — class CRUD                                                  #
    # ================================================================== #

    async def create_class(
        self, name: str, description: Optional[str] = None
    ) -> dict[str, Any]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.classes (name, description)
                VALUES ($1, $2)
                RETURNING id, name, description, created_at
                """,
                name, description,
            )
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "teacher_count": 0,
            "student_count": 0,
            "created_at": row["created_at"],
        }

    async def list_classes(self) -> list[dict[str, Any]]:
        """All classes with member counts — admin management view."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.name, c.description, c.created_at,
                       COUNT(DISTINCT ct.teacher_id) AS teacher_count,
                       COUNT(DISTINCT cs.student_id) AS student_count
                FROM public.classes c
                LEFT JOIN public.class_teachers ct ON ct.class_id = c.id
                LEFT JOIN public.class_students cs ON cs.class_id = c.id
                GROUP BY c.id
                ORDER BY c.created_at DESC
                """,
            )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"],
                "teacher_count": r["teacher_count"],
                "student_count": r["student_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def get_class(self, class_id: str) -> dict[str, Any]:
        """Class detail incl. teacher + student member lists.

        Raises NotFoundError when the class doesn't exist.
        """
        async with self.db.acquire() as conn:
            cls = await self._fetch_class_or_404(conn, class_id)
            teachers = await conn.fetch(
                """
                SELECT p.id, p.full_name, p.email
                FROM public.class_teachers ct
                JOIN public.profiles p ON p.id = ct.teacher_id
                WHERE ct.class_id = $1
                ORDER BY p.full_name
                """,
                class_id,
            )
            students = await conn.fetch(
                """
                SELECT p.id, p.full_name, p.email
                FROM public.class_students cs
                JOIN public.profiles p ON p.id = cs.student_id
                WHERE cs.class_id = $1
                ORDER BY p.full_name
                """,
                class_id,
            )
        def members(rows):
            return [
                {
                    "id": str(r["id"]),
                    "full_name": r["full_name"],
                    "email": r["email"],
                }
                for r in rows
            ]

        return {
            "id": str(cls["id"]),
            "name": cls["name"],
            "description": cls["description"],
            "created_at": cls["created_at"],
            "teacher_count": len(teachers),
            "student_count": len(students),
            "teachers": members(teachers),
            "students": members(students),
        }

    async def update_class(
        self,
        class_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        description_set: bool = False,
    ) -> dict[str, Any]:
        """Patch name and/or description. `updated_at` is always bumped.

        `description_set` distinguishes "description omitted" from
        "description explicitly set to null".
        """
        sets = ["updated_at = now()"]
        vals: list[Any] = []
        if name is not None:
            vals.append(name)
            sets.append(f"name = ${len(vals)}")
        if description_set:
            vals.append(description)
            sets.append(f"description = ${len(vals)}")

        vals.append(class_id)
        sql = (
            "UPDATE public.classes SET "
            + ", ".join(sets)
            + f" WHERE id = ${len(vals)} "
            + "RETURNING id, name, description, created_at"
        )
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(sql, *vals)
            if not row:
                raise NotFoundError(f"Class {class_id} not found")
            counts = await self._member_counts(conn, class_id)
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "teacher_count": counts[0],
            "student_count": counts[1],
            "created_at": row["created_at"],
        }

    async def delete_class(self, class_id: str) -> None:
        """Hard-delete — only when the class has 0 teachers AND 0 students.

        Raises NotFoundError if missing, ValidationError if non-empty.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                await self._fetch_class_or_404(conn, class_id)
                tc, sc = await self._member_counts(conn, class_id)
                total = tc + sc
                if total > 0:
                    raise ValidationError(
                        f"Class has {total} members; remove all first"
                    )
                await conn.execute(
                    "DELETE FROM public.classes WHERE id = $1", class_id
                )

    # ================================================================== #
    # Admin — teacher membership                                          #
    # ================================================================== #

    async def add_teacher(self, class_id: str, teacher_id: str) -> None:
        async with self.db.acquire() as conn:
            await self._fetch_class_or_404(conn, class_id)
            await self._require_user_role(conn, teacher_id, "teacher")
            try:
                await conn.execute(
                    """
                    INSERT INTO public.class_teachers (class_id, teacher_id)
                    VALUES ($1, $2)
                    """,
                    class_id, teacher_id,
                )
            except asyncpg.UniqueViolationError:
                raise AlreadyExistsError(
                    "User is already a teacher of this class"
                )

    async def remove_teacher(self, class_id: str, teacher_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM public.class_teachers
                WHERE class_id = $1 AND teacher_id = $2
                """,
                class_id, teacher_id,
            )
        if result == "DELETE 0":
            raise NotFoundError("Teacher is not a member of this class")

    # ================================================================== #
    # Admin — student membership (1-class-per-student)                    #
    # ================================================================== #

    async def add_student(self, class_id: str, student_id: str) -> None:
        async with self.db.acquire() as conn:
            await self._fetch_class_or_404(conn, class_id)
            await self._require_user_role(conn, student_id, "student")

            existing = await conn.fetchrow(
                """
                SELECT cs.class_id, c.name
                FROM public.class_students cs
                JOIN public.classes c ON c.id = cs.class_id
                WHERE cs.student_id = $1
                """,
                student_id,
            )
            if existing is not None:
                if str(existing["class_id"]) == str(class_id):
                    raise AlreadyExistsError(
                        "Student is already in this class"
                    )
                raise ValidationError(
                    f"Student already in class {existing['name']}; remove first"
                )

            try:
                await conn.execute(
                    """
                    INSERT INTO public.class_students (class_id, student_id)
                    VALUES ($1, $2)
                    """,
                    class_id, student_id,
                )
            except asyncpg.UniqueViolationError:
                # Lost a race against a concurrent add into another class —
                # re-resolve to surface the same friendly errors. (R8)
                other = await conn.fetchrow(
                    """
                    SELECT cs.class_id, c.name
                    FROM public.class_students cs
                    JOIN public.classes c ON c.id = cs.class_id
                    WHERE cs.student_id = $1
                    """,
                    student_id,
                )
                if other and str(other["class_id"]) == str(class_id):
                    raise AlreadyExistsError("Student is already in this class")
                name = other["name"] if other else "another class"
                raise ValidationError(
                    f"Student already in class {name}; remove first"
                )

    async def remove_student(self, class_id: str, student_id: str) -> None:
        async with self.db.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM public.class_students
                WHERE class_id = $1 AND student_id = $2
                """,
                class_id, student_id,
            )
        if result == "DELETE 0":
            raise NotFoundError("Student is not a member of this class")

    # ================================================================== #
    # RBAC predicates (§4) — referenced by teacher-grading + attempt      #
    # detail when class-scoping lands (phase 2).                          #
    # ================================================================== #

    async def teacher_shares_class_with(
        self, teacher_id: str, student_id: str
    ) -> bool:
        """True iff the teacher teaches a class the student belongs to."""
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM public.class_teachers ct
                  JOIN public.class_students cs ON cs.class_id = ct.class_id
                  WHERE ct.teacher_id = $1 AND cs.student_id = $2
                )
                """,
                teacher_id, student_id,
            )

    async def teacher_teaches_class(
        self, teacher_id: str, class_id: str
    ) -> bool:
        """True iff the teacher is a member of class_teachers for the class."""
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT EXISTS (
                  SELECT 1 FROM public.class_teachers
                  WHERE teacher_id = $1 AND class_id = $2
                )
                """,
                teacher_id, class_id,
            )

    # ================================================================== #
    # Teacher — list classes + submissions (§6)                          #
    # ================================================================== #

    async def list_teacher_classes(
        self, teacher_id: Optional[str]
    ) -> list[dict[str, Any]]:
        """Classes a teacher teaches (admin: pass teacher_id=None → all).

        Each item carries studentCount + pendingGradingCount (submitted,
        not abandoned, not fully-graded attempts of the class's students).
        Computed in one aggregate to avoid N+1.
        """
        pending_filter = (
            "WHERE a.submitted_at IS NOT NULL "
            "AND NOT a.is_abandoned AND NOT a.is_fully_graded"
        )
        if teacher_id is not None:
            sql = f"""
                SELECT c.id, c.name,
                       COUNT(DISTINCT cs.student_id) AS student_count,
                       COUNT(a.id) FILTER ({pending_filter}) AS pending_grading_count
                FROM public.classes c
                JOIN public.class_teachers ct
                  ON ct.class_id = c.id AND ct.teacher_id = $1
                LEFT JOIN public.class_students cs ON cs.class_id = c.id
                LEFT JOIN public.attempts a ON a.user_id = cs.student_id
                GROUP BY c.id, c.name
                ORDER BY c.created_at DESC
            """
            args = (teacher_id,)
        else:
            sql = f"""
                SELECT c.id, c.name,
                       COUNT(DISTINCT cs.student_id) AS student_count,
                       COUNT(a.id) FILTER ({pending_filter}) AS pending_grading_count
                FROM public.classes c
                LEFT JOIN public.class_students cs ON cs.class_id = c.id
                LEFT JOIN public.attempts a ON a.user_id = cs.student_id
                GROUP BY c.id, c.name
                ORDER BY c.created_at DESC
            """
            args = ()
        async with self.db.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "student_count": r["student_count"],
                "pending_grading_count": r["pending_grading_count"],
            }
            for r in rows
        ]

    async def list_class_submissions(
        self, class_id: str, status: str = "all"
    ) -> list[dict[str, Any]]:
        """Submitted (non-abandoned) attempts of a class's students.

        `status="pending"` further filters to is_fully_graded=false.
        Raises NotFoundError if the class doesn't exist (caller checks
        teaching authorization separately — see routes).
        """
        pending_clause = (
            "AND a.is_fully_graded = false" if status == "pending" else ""
        )
        async with self.db.acquire() as conn:
            await self._fetch_class_or_404(conn, class_id)
            rows = await conn.fetch(
                f"""
                SELECT a.id AS attempt_id, a.submitted_at, a.is_fully_graded,
                       a.score, a.total_points, a.percentage,
                       p.id AS student_id, p.full_name,
                       e.id AS exam_id, e.title, e.level, e.skill
                FROM public.attempts a
                JOIN public.class_students cs
                  ON cs.student_id = a.user_id AND cs.class_id = $1
                JOIN public.profiles p ON p.id = a.user_id
                JOIN public.exams e ON e.id = a.exam_id
                WHERE a.submitted_at IS NOT NULL
                  AND NOT a.is_abandoned
                  {pending_clause}
                ORDER BY a.submitted_at DESC
                """,
                class_id,
            )
        return [
            {
                "attempt_id": str(r["attempt_id"]),
                "student": {
                    "id": str(r["student_id"]),
                    "full_name": r["full_name"],
                },
                "exam": {
                    "id": str(r["exam_id"]),
                    "title": r["title"],
                    "level": r["level"],
                    "skill": r["skill"],
                },
                "submitted_at": r["submitted_at"],
                "is_fully_graded": r["is_fully_graded"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "total_points": (
                    float(r["total_points"])
                    if r["total_points"] is not None else None
                ),
                "percentage": (
                    float(r["percentage"])
                    if r["percentage"] is not None else None
                ),
            }
            for r in rows
        ]

    # ================================================================== #
    # Internal helpers                                                    #
    # ================================================================== #

    async def class_exists(self, class_id: str) -> bool:
        async with self.db.acquire() as conn:
            try:
                return await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.classes WHERE id = $1)",
                    class_id,
                )
            except asyncpg.DataError:
                return False

    async def _fetch_class_or_404(self, conn, class_id: str):
        try:
            row = await conn.fetchrow(
                "SELECT id, name, description, created_at "
                "FROM public.classes WHERE id = $1",
                class_id,
            )
        except asyncpg.DataError:
            # Malformed uuid string → treat as not found.
            row = None
        if not row:
            raise NotFoundError(f"Class {class_id} not found")
        return row

    async def _member_counts(self, conn, class_id: str) -> tuple[int, int]:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM public.class_teachers WHERE class_id = $1) AS tc,
              (SELECT COUNT(*) FROM public.class_students WHERE class_id = $1) AS sc
            """,
            class_id,
        )
        return int(row["tc"]), int(row["sc"])

    async def _require_user_role(self, conn, user_id: str, role: str) -> None:
        """404 if user missing; 400 (ValidationError) if role mismatch."""
        try:
            actual = await conn.fetchval(
                "SELECT role FROM public.profiles WHERE id = $1", user_id
            )
        except asyncpg.DataError:
            actual = None
        if actual is None:
            raise NotFoundError(f"User {user_id} not found")
        if actual != role:
            raise ValidationError(
                f"User must have role '{role}' (found '{actual}')"
            )


class_service = ClassService()
