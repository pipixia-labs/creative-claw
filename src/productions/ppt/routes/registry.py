"""Product-level registry for PPT route workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.productions.ppt.routes.html import build_html_route
from src.productions.ppt.routes.svg import build_svg_route
from src.productions.ppt.schemas import DeckContentPlan, PptRouteBuildPackage


PptRouteHandler = Callable[
    [DeckContentPlan, Path, str, str],
    PptRouteBuildPackage,
]


@dataclass(frozen=True)
class PptRouteRegistration:
    """One route workflow registered behind the PPT product manager."""

    route: str
    workflow_name: str
    handler: PptRouteHandler | None
    implemented: bool
    description: str

    def summary(self) -> dict[str, object]:
        """Return a JSON-safe summary for diagnostics and tests."""
        return {
            "route": self.route,
            "workflow_name": self.workflow_name,
            "implemented": self.implemented,
            "description": self.description,
        }


def _html_route_handler(
    content_plan: DeckContentPlan,
    output_dir: Path,
    aspect_ratio: str,
    template_id: str,
) -> PptRouteBuildPackage:
    """Execute the current HTML route implementation."""
    return build_html_route(
        content_plan=content_plan,
        output_dir=output_dir,
        aspect_ratio=aspect_ratio,
        template_id=template_id,
    )


def _svg_route_handler(
    content_plan: DeckContentPlan,
    output_dir: Path,
    aspect_ratio: str,
    template_id: str,
) -> PptRouteBuildPackage:
    """Execute the current SVG route implementation."""
    return build_svg_route(
        content_plan=content_plan,
        output_dir=output_dir,
        aspect_ratio=aspect_ratio,
        template_id=template_id,
    )


def build_default_ppt_route_registry() -> dict[str, PptRouteRegistration]:
    """Build the default route registry for the PPT product line."""
    return {
        "html": PptRouteRegistration(
            route="html",
            workflow_name="HtmlRouteSequentialAgent",
            handler=_html_route_handler,
            implemented=True,
            description="HTML route MVP: no-template free design by default, optional system template, HTML preview, PNG previews, native editable PPTX.",
        ),
        "svg": PptRouteRegistration(
            route="svg",
            workflow_name="SvgRouteSequentialAgent",
            handler=_svg_route_handler,
            implemented=True,
            description="SVG route MVP: design strategy, per-slide SVG generation, SVG quality checks, and editable PPTX export.",
        ),
        "xml": PptRouteRegistration(
            route="xml",
            workflow_name="XmlRouteSequentialAgent",
            handler=None,
            implemented=False,
            description="Deferred XML route for user PPTX template analysis and native OOXML editing.",
        ),
    }


__all__ = [
    "PptRouteHandler",
    "PptRouteRegistration",
    "build_default_ppt_route_registry",
]
