import argparse
import os
from typing import List, Tuple
import json

from idp_lecture_finder.campus_api import CampusApiClient
from idp_lecture_finder.llm import filter_lectures

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import BaseCallbackHandler

from rich.console import Console
from rich.markdown import Markdown

LECTURES_FILE = "./data/lectures.txt"
SCORED_FILE = "./data/scored_lectures.txt"
FILTERED_FILE = "./data/filtered_lectures.txt"
ENRICHED_FILE = "./data/enriched_lectures.txt"
IDP_FILE = "./data/idp.txt"


# -----------------------
# Helpers
# -----------------------
def load_lectures(path: str) -> List[Tuple[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip().split(" ", 1) for line in f if line.strip()]


def save_lectures(path: str, lectures: List[Tuple[str, str, float]]):
    with open(path, "w", encoding="utf-8") as f:
        for lec_id, title, score in lectures:
            f.write(f"{lec_id} {score:.2f} {title}\n")


def load_idp(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class StreamingMarkdownHandler(BaseCallbackHandler):
    def __init__(self, console):
        self.console = console
        self.buffer = ""

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.buffer += token
        if "\n" in self.buffer:
            lines = self.buffer.split("\n")
            for line in lines[:-1]:
                self.console.print(Markdown(line))
            self.buffer = lines[-1]


# -----------------------
# Stage 1: Scrape
# -----------------------
def stage_scrape(args):
    print("🔄 Scraping lectures from TUM API...")

    client = CampusApiClient("https://campus.tum.de/tumonline/ee/rest/")
    all_courses = client.get_courses(term_id=args.term)

    info_courses = []
    for curriculum in args.curricula:
        info_courses += client.get_courses(
            term_id=args.term, curriculum_version_id=curriculum
        )

    info_ids = {c.id for c in info_courses}
    filtered_courses = [c for c in all_courses if c.id not in info_ids]

    lecture_courses = [
        c
        for c in filtered_courses
        if c.course_type in ["Vorlesung", "Vorlesung mit integrierten Übungen"]
    ]

    with open(args.output, "w", encoding="utf-8") as f:
        for course in lecture_courses:
            f.write(f"{course.id} {course.courseTitle.value.strip()}\n")

    print(f"✅ Saved {len(lecture_courses)} lectures → {args.output}")


# -----------------------
# Stage 2: Score
# -----------------------
def stage_score(args):
    if not os.path.exists(args.input):
        print(f"❌ Input file not found: {args.input}")
        return

    lectures = load_lectures(args.input)
    print(f"🤖 Scoring {len(lectures)} lectures using model {args.model}...")

    scored = filter_lectures(  # returns (id, title, score)
        model=args.model,
        lectures=lectures,
        idp_topic=args.topic,
        score_cutoff=0,  # keep everything
    )

    # Save all scores
    with open(args.output, "w", encoding="utf-8") as f:
        for lec_id, title, score in scored:
            f.write(f"{lec_id} {score:.2f} {title}\n")

    print(f"✅ Scored {len(scored)} lectures → {args.output}")


# -----------------------
# Stage 3: Filter
# -----------------------
def stage_filter(args):
    if not os.path.exists(args.input):
        print(f"❌ Input file not found: {args.input}")
        return

    # Read scored lectures
    scored = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(" ", 2)
            if len(parts) == 3:
                lec_id, score, title = parts
                scored.append((lec_id, title, float(score)))

    # Apply cutoff
    filtered = [x for x in scored if x[2] >= args.cutoff]

    # Optionally top-k
    if args.top_k:
        filtered = filtered[: args.top_k]

    # Save final filtered
    with open(args.output, "w", encoding="utf-8") as f:
        for lec_id, title, score in filtered:
            f.write(f"{lec_id} {score:.2f} {title}\n")

    print(f"✅ Filtered {len(filtered)} lectures → {args.output}")


# -----------------------
# Stage 4: Enrich
# -----------------------
def stage_enrich(args):
    if not os.path.exists(args.input):
        print(f"❌ Filtered lectures not found: {args.input}")
        return

    # Read filtered lectures
    lectures = []
    with open(args.input, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split(" ", 2)
            if len(parts) == 3:
                lec_id, score, title = parts
                lectures.append((int(lec_id), title, float(score)))

    client = CampusApiClient(args.base_url)

    enriched = []
    for lec_id, title, score in lectures:
        try:
            details = client.get_course_details(lec_id)
            if details:
                enriched.append(
                    {
                        "id": details.id,
                        "title": details.title,
                        "score": score,
                        "credits": details.credits,
                        "semester_id": details.semester_id,
                        "course_type": details.course_type,
                        "description": details.description,
                    }
                )
            else:
                enriched.append(
                    {
                        "id": lec_id,
                        "title": title,
                        "score": score,
                        "credits": None,
                        "semester_id": None,
                        "course_type": None,
                        "description": None,
                    }
                )
        except Exception as e:
            print(f"⚠️ Error fetching details for {lec_id}: {e}")
            enriched.append(
                {
                    "id": lec_id,
                    "title": title,
                    "score": score,
                    "credits": None,
                    "semester_id": None,
                    "course_type": None,
                    "description": None,
                }
            )

    # Save enriched lectures as JSON for easy inspection
    import json

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"✅ Enriched {len(enriched)} lectures → {args.output}")


# -----------------------
# Stage 5: Recommend
# -----------------------
def stage_recommend(args):
    if not os.path.exists(args.lectures):
        print(f"❌ Enriched lectures not found: {args.lectures}")
        return

    # Load enriched JSON
    with open(args.lectures, "r", encoding="utf-8") as f:
        lectures = json.load(f)

    idp = load_idp(args.idp)

    system_prompt = """
You are an expert student advisor.
Given a definition of an IDP and a list of lectures (with title, description, credits, semester, and type),
recommend 5-10 suitable lectures that best match the IDP.
Avoid Q&As, seminars, and practical courses.
"""

    llm = init_chat_model(args.model, streaming=True)
    config = RunnableConfig(callbacks=[StreamingMarkdownHandler(Console())])

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"IDP: {idp}"),
        HumanMessage(content=f"Lectures: {lectures}"),
        HumanMessage(content=f"Topic: {args.topic}"),
    ]

    print("\nAssistant:\n")
    llm.invoke(messages, config)
    print()

    print("\nYou can type 'exit' or 'quit' to stop.\n")

    while True:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            break

        messages.append(HumanMessage(content=user_input))
        print("\nAssistant:\n")
        result = llm.invoke(messages, config)
        print("\n")
        messages.append(result)


# -----------------------
# Run pipeline
# -----------------------
def stage_run(args):
    stages = ["scrape", "score", "filter", "enrich", "recommend"]
    start_index = stages.index(args.from_stage)

    for stage in stages[start_index:]:
        print(f"\n=== Running {stage} stage ===")
        if stage == "scrape":
            stage_scrape(args)
        elif stage == "score":
            stage_score(args)
        elif stage == "filter":
            stage_filter(args)
        elif stage == "enrich":
            stage_enrich(args)
        elif stage == "recommend":
            stage_recommend(args)


# -----------------------
# CLI
# -----------------------
def main():
    parser = argparse.ArgumentParser("lecture_pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- scrape ----
    p = sub.add_parser("scrape")
    p.add_argument("--term", type=int, default=206)
    p.add_argument("--curricula", nargs="+", type=int, default=[5217])
    p.add_argument("--output", default=LECTURES_FILE)
    p.set_defaults(func=stage_scrape)

    # ---- score ----
    p = sub.add_parser("score", help="Score lectures using LLM")
    p.add_argument("--input", default=LECTURES_FILE)
    p.add_argument("--output", default=SCORED_FILE)
    p.add_argument("--topic", required=True)
    p.add_argument("--model", default="openai:gpt-5.2")
    p.set_defaults(func=stage_score)

    # ---- filter ----
    p = sub.add_parser("filter", help="Filter scored lectures")
    p.add_argument("--input", default=SCORED_FILE)
    p.add_argument("--output", default=FILTERED_FILE)
    p.add_argument("--cutoff", type=float, default=2.0)
    p.add_argument(
        "--top-k", type=int, default=None, help="Optionally take top-k lectures"
    )
    p.set_defaults(func=stage_filter)

    # ---- enrich ----
    p = sub.add_parser(
        "enrich", help="Enrich filtered lectures with full course details"
    )
    p.add_argument("--input", default=FILTERED_FILE)
    p.add_argument("--output", default=ENRICHED_FILE)
    p.add_argument("--base-url", default="https://campus.tum.de/tumonline/ee/rest/")
    p.set_defaults(func=stage_enrich)

    # ---- recommend ----
    p = sub.add_parser("recommend")
    p.add_argument("--lectures", default=ENRICHED_FILE)
    p.add_argument("--idp", default=IDP_FILE)
    p.add_argument("--topic", required=True)
    p.add_argument("--model", default="openai:gpt-5.2")
    p.set_defaults(func=stage_recommend)

    # ---- run pipeline ----
    p = sub.add_parser("run")
    p.add_argument(
        "--from-stage",
        choices=["scrape", "score", "filter", "enrich", "recommend"],
        default="scrape",
    )
    p.add_argument("--term", type=int, default=206)
    p.add_argument("--curricula", nargs="+", type=int, default=[5217])
    p.add_argument("--topic", required=True)
    p.add_argument("--cutoff", type=float, default=2.0)
    p.add_argument("--model", default="openai:gpt-5.2")
    p.add_argument("--input", default=LECTURES_FILE)
    p.add_argument("--output", default=FILTERED_FILE)
    p.add_argument("--lectures", default=FILTERED_FILE)
    p.add_argument("--idp", default=IDP_FILE)
    p.set_defaults(func=stage_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
