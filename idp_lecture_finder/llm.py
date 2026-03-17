from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import List, Tuple
import math


class LectureScore(BaseModel):
    id: str = Field(..., description="Lecture ID, must match input tuple")
    score: float = Field(..., ge=0, le=10, description="Relevance score 0-10")


class LectureScoreBatch(BaseModel):
    lectures: List[LectureScore] = Field(..., description="List of scored lectures")


def rate_lectures_structured(
    model: str,
    lectures: List[Tuple[str, str]],
    idp_topic: str,
    batch_size=50,
):
    llm = init_chat_model(model)
    structured_llm = llm.with_structured_output(LectureScoreBatch)

    system_prompt = """
    You are an expert student advisor.
    You are given a pre-filtered list of possibly relevant lectures.
    For each lecture (id, title), provide a relevance score from 0 to 10 for the given IDP topic.
    Assign a score of at least 1 if the lecture has any chance of being relevant, where 1 indicates very low relevance.
    It is possible that all given lectures are relevant or that all are completely irrelevant.
    Do not change the lecture IDs.
    Return a JSON object with a field 'lectures' containing the scored lectures.
    """

    results = []
    num_batches = math.ceil(len(lectures) / batch_size)

    for i in range(num_batches):
        batch = lectures[i * batch_size : (i + 1) * batch_size]

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"IDP topic: {idp_topic}"),
            HumanMessage(content=f"Lectures: {batch}"),
        ]

        batch_result = structured_llm.invoke(messages)

        # Verify that returned IDs exist in the input batch
        input_ids = {lec[0] for lec in batch}
        for item in batch_result.lectures:
            if item.id not in input_ids:
                raise ValueError(f"LLM returned hallucinated ID: {item.id}")

        returned_ids = {item.id for item in batch_result.lectures}
        missing = input_ids - returned_ids

        if missing:
            raise ValueError(f"LLM missed IDs: {missing}")

        print(f"Processed batch {i+1}/{num_batches}")

        results.extend(batch_result.lectures)

    return results


def filter_lectures(
    model: str,
    lectures: List[Tuple[str, str]],
    idp_topic: str,
    score_cutoff: float = 2.0,
) -> List[Tuple[str, str, float]]:
    scored = rate_lectures_structured(model, lectures, idp_topic)

    id_to_title = {lec[0]: lec[1] for lec in lectures}

    filtered = [
        (lec.id, id_to_title[lec.id], lec.score)
        for lec in scored
        if lec.score >= score_cutoff
    ]

    filtered.sort(key=lambda x: x[1])
    filtered.sort(key=lambda x: x[2], reverse=True)

    return filtered
