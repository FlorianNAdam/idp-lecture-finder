import requests
from dataclasses import dataclass, field
from typing import List, Optional

import requests_cache

requests_cache.install_cache(
    "http_cache",  # filename (without extension)
    backend="sqlite",  # sqlite is default
    expire_after=None,  # never expire (or set seconds)
)

# ===== Models =====


@dataclass
class LangData:
    value: str


@dataclass
class SemesterDto:
    id: int


@dataclass
class Course:
    id: int
    courseTitle: LangData
    semesterDto: SemesterDto
    course_type: str


@dataclass
class AppointmentSeries:
    id: int | None = None  # unknown fields not provided in Kotlin snippet


@dataclass
class Group:
    id: int
    name: str
    appointments: List[AppointmentSeries] = field(default_factory=list)


@dataclass
class CourseDetail:
    id: int
    title: str
    semester_id: int
    course_type: str
    description: str
    credits: Optional[str] = None


# ===== Client =====


class CampusApiClient:

    def __init__(
        self,
        base_url: str,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _parse_course(
        self,
        data: dict,
    ) -> Course:
        course_type = data["courseTypeDto"]["courseTypeName"]["value"]
        return Course(
            id=data["id"],
            courseTitle=LangData(value=data["courseTitle"]["value"]),
            semesterDto=SemesterDto(id=data["semesterDto"]["id"]),
            course_type=course_type,
        )

    def _parse_group(
        self,
        data: dict,
    ) -> Group:
        appointments = [
            AppointmentSeries(id=a.get("id"))
            for a in data.get("appointmentSeriesDtos", [])
        ]

        return Group(id=data["id"], name=data["name"], appointments=appointments)

    def get_courses(
        self,
        term_id: int,
        curriculum_version_id: int | None = None,
    ) -> List[Course]:
        all_courses: List[Course] = []

        skip = 0
        top = 50

        while True:
            url = f"{self.base_url}/slc.tm.cp/student/courses"

            filters = [
                "courseNormKey-eq=LVEAB",
                "orgId-eq=1",
                f"termId-eq={term_id}",
            ]

            if curriculum_version_id is not None:
                filters.append(f"curriculumVersionId-eq={curriculum_version_id}")

            params = {
                "$filter": ";".join(filters),
                "$skip": skip,
                "$top": top,
            }

            r = self.session.get(url, params=params)
            r.raise_for_status()

            data = r.json()

            courses = [self._parse_course(c) for c in data.get("courses", [])]
            total_count = data.get("totalCount", 0)

            print(f"Fetched: {len(all_courses)}/{total_count}")

            all_courses.extend(courses)

            if len(all_courses) >= total_count:
                break

            skip = len(all_courses)

        return all_courses

    def get_course_groups(
        self,
        course_id: int,
    ) -> Optional[List[Group]]:
        url = f"{self.base_url}/slc.tm.cp/student/courseGroups/firstGroups/{course_id}"

        r = self.session.get(url)

        if r.status_code in (404, 500):
            return None

        r.raise_for_status()

        data = r.json()

        if "courseGroupDtos" not in data:
            raise Exception("Missing 'courseGroupDtos' node")

        return [self._parse_group(g) for g in data["courseGroupDtos"]]

    def get_course_details(self, course_id: int) -> Optional[CourseDetail]:
        url = f"{self.base_url}/slc.tm.cp/student/courses/{course_id}"
        r = self.session.get(url)

        if r.status_code in (404, 500):
            return None

        r.raise_for_status()
        data = r.json()

        try:
            resource = data["resource"][0]["content"]["cpCourseDetailDto"]
            cp_course = resource["cpCourseDto"]

            cp_description = resource.get("cpCourseDescriptionDto", {})

            # Extract each subfield safely
            course_content = cp_description.get("courseContent", {}).get("value", "")
            previous_knowledge = cp_description.get("previousKnowledge", {}).get(
                "value", ""
            )
            course_objective = cp_description.get("courseObjective", {}).get(
                "value", ""
            )
            teaching_method = cp_description.get("teachingMethod", {}).get("value", "")

            # Combine into a single description string
            description_parts = [
                course_content,
                f"Prerequisites: {previous_knowledge}" if previous_knowledge else "",
                f"Objectives: {course_objective}" if course_objective else "",
                f"Teaching Method: {teaching_method}" if teaching_method else "",
            ]
            description = "\n\n".join(part for part in description_parts if part)

            # Collect credits if present
            credits = None
            norm_configs = cp_course.get("courseNormConfigs", [])
            if norm_configs:
                credits = norm_configs[0].get("value")

            return CourseDetail(
                id=cp_course["id"],
                title=cp_course["courseTitle"]["value"],
                semester_id=cp_course["semesterDto"]["id"],
                course_type=cp_course["courseTypeDto"]["courseTypeName"]["value"],
                credits=credits,
                description=description,
            )

        except KeyError as e:
            raise Exception(f"Unexpected data format in course details: {e}")


# ===== Example usage =====

if __name__ == "__main__":
    client = CampusApiClient("https://campus.tum.de/tumonline/ee/rest/")

    course_details = client.get_course_details(950881094)
    print(course_details)
