"""The ``merchant`` backend's inputs: what a store's catalog search takes, validated before anything is sent."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from web_router.types_options import NonBlankText

__all__ = ["MerchantInputs"]


class MerchantInputs(BaseModel):
    """One catalog search. An option the store has not been shown to honour is refused, never dropped."""

    model_config = ConfigDict(extra="forbid")

    query: Annotated[NonBlankText, Field(description="Keywords to search the store's catalog for.")]
    limit: int | None = Field(default=None, ge=1, le=50, description="Products per page.")
    min_price: int | None = Field(
        default=None, ge=0, description="Lowest price in minor currency units (cents): 1000 is 10.00. Never converted."
    )
    max_price: int | None = Field(
        default=None, ge=0, description="Highest price in minor currency units (cents): 50000 is 500.00."
    )
    country: str | None = Field(default=None, description="Buyer country code, e.g. 'DE'.")
    currency: str | None = Field(default=None, description="Currency code, e.g. 'EUR'.")

    @field_validator("limit", "min_price", "max_price", mode="before")
    @classmethod
    def _not_a_boolean(cls, value: Any) -> Any:
        """``True`` is an ``int`` to Python and would be sent as 1; a number is a number."""
        if isinstance(value, bool):
            raise ValueError("must be a whole number, not a boolean")
        return value

    def specified(self) -> set[str]:
        """The options the caller set."""
        return {name for name in self.model_fields_set if getattr(self, name) is not None}
