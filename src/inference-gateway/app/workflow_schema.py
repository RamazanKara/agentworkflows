"""The flat JSON Schema subset accepted for workflow forms and run inputs."""

import re
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputProperty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "number", "integer", "boolean", "array"]
    description: str = ""
    default: Any = None
    examples: list[Any] = Field(default_factory=list)
    enum: list[Any] | None = Field(default=None, min_length=1)
    items: dict[str, Literal["string"]] | None = None
    min_length: int | None = Field(default=None, alias="minLength", ge=0, strict=True)
    pattern: str | None = None
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    exclusive_minimum: float | None = Field(default=None, alias="exclusiveMinimum", allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_property(self) -> "InputProperty":
        if (self.type == "array" and self.items != {"type": "string"}) or (
            self.type != "array" and self.items is not None
        ):
            raise ValueError("only arrays of strings are supported")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError("pattern must be a valid regular expression") from exc
        for value in (self.enum or []) + self.examples:
            if self.type_error(value):
                raise ValueError("enum and examples must match the property type")
        if "default" in self.model_fields_set and self.error(self.default):
            raise ValueError("default must match the property type and enum")
        return self

    def type_error(self, value: Any) -> str | None:
        valid = {
            "string": isinstance(value, str),
            "number": type(value) is int or (type(value) is float and isfinite(value)),
            "integer": type(value) is int or (type(value) is float and isfinite(value) and value.is_integer()),
            "boolean": isinstance(value, bool),
            "array": isinstance(value, list) and all(isinstance(item, str) for item in value),
        }[self.type]
        expected = {"array": "an array of strings", "integer": "an integer"}.get(self.type, f"a {self.type}")
        return None if valid else f"Must be {expected}."

    def error(self, value: Any) -> str | None:
        problem = self.type_error(value)
        if problem:
            return problem
        if self.enum is not None and value not in self.enum:
            return "Must be one of the declared enum values."
        if self.type == "string":
            if self.min_length is not None and len(value) < self.min_length:
                return f"Must contain at least {self.min_length} characters."
            if self.pattern is not None and re.search(self.pattern, value) is None:
                return "Does not match the required pattern."
        if self.type in {"number", "integer"}:
            if self.minimum is not None and value < self.minimum:
                return f"Must be at least {self.minimum:g}."
            if self.maximum is not None and value > self.maximum:
                return f"Must be at most {self.maximum:g}."
            if self.exclusive_minimum is not None and value <= self.exclusive_minimum:
                return f"Must be greater than {self.exclusive_minimum:g}."
        return None


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_uri: Literal["https://json-schema.org/draft/2020-12/schema"] = Field(
        default="https://json-schema.org/draft/2020-12/schema", alias="$schema"
    )
    type: Literal["object"]
    properties: dict[str, InputProperty]
    required: list[str] = Field(default_factory=list)
    description: str = ""
    additional_properties: bool = Field(default=True, alias="additionalProperties", strict=True)

    @model_validator(mode="after")
    def validate_required(self) -> "InputSchema":
        if set(self.required) - self.properties.keys() or len(self.required) != len(set(self.required)):
            raise ValueError("required must contain unique declared property names")
        return self

    def errors(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, dict):
            return [{"field": "input", "message": "Must be an object."}]
        errors = [
            {"field": f"input.{name}", "message": "Field is required."} for name in self.required if name not in value
        ]
        for name, item in value.items():
            prop = self.properties.get(name)
            problem = prop.error(item) if prop else None if self.additional_properties else "Unknown field."
            if problem:
                errors.append({"field": f"input.{name}", "message": problem})
        return errors
