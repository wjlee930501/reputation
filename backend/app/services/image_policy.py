"""Policy verdicts and typed rejection for generated cover images."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class ImagePolicyAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    has_text: bool
    has_logo: bool
    has_recognizable_people: bool
    impersonates_real_clinic: bool
    topic_relevant: bool


@dataclass(frozen=True, slots=True)
class ImagePolicyRejectedError(Exception):
    assessment: ImagePolicyAssessment

    def __str__(self) -> str:
        return "generated image failed the publication policy"


def image_is_publishable(assessment: ImagePolicyAssessment) -> bool:
    """Return true only when every blocking policy dimension passes."""
    return (
        assessment.topic_relevant
        and not assessment.has_text
        and not assessment.has_logo
        and not assessment.has_recognizable_people
        and not assessment.impersonates_real_clinic
    )
