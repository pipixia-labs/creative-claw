"""Private product-ppt skill discovery for PptProductManager."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from conf.path import PROJECT_PATH

PRODUCT_PPT_SKILLS_DIR = Path("skills/product-ppt-skills")


@dataclass(frozen=True, slots=True)
class ProductPptSkillInfo:
    """Metadata for one private product-ppt skill."""

    name: str
    path: Path
    relative_path: str
    description: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe representation of this skill."""
        return {
            "name": self.name,
            "path": self.relative_path,
            "description": self.description,
        }


class ProductPptSkillRegistry:
    """Discover and read PptProductManager-private skills."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        skills_dir: str | Path | None = None,
    ) -> None:
        """Initialize the registry under the project-private PPT skill root."""
        self.project_root = Path(project_root or PROJECT_PATH).resolve()
        self.skills_dir = (
            Path(skills_dir).resolve()
            if skills_dir is not None
            else (self.project_root / PRODUCT_PPT_SKILLS_DIR).resolve()
        )

    def list_skills(self) -> list[ProductPptSkillInfo]:
        """Return all private product-ppt skills."""
        if not self.skills_dir.exists() or not self.skills_dir.is_dir():
            return []

        skills: list[ProductPptSkillInfo] = []
        for child in sorted(self.skills_dir.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
                continue
            skills.append(
                ProductPptSkillInfo(
                    name=child.name,
                    path=skill_file.resolve(),
                    relative_path=_display_path(skill_file.resolve(), self.project_root),
                    description=_extract_description(skill_file) or child.name,
                )
            )
        return skills

    def read_skill(self, name: str) -> str:
        """Read one private product-ppt skill by exact folder name."""
        skill_name = str(name or "").strip()
        if not skill_name:
            raise ValueError("Skill name cannot be empty.")

        for skill in self.list_skills():
            if skill.name == skill_name:
                return skill.path.read_text(encoding="utf-8")
        raise ValueError(f"Product PPT skill '{skill_name}' not found.")


def _extract_description(skill_file: Path) -> str:
    """Extract the YAML frontmatter description from a standard SKILL.md."""
    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return ""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return ""
    frontmatter = match.group(1)
    for line in frontmatter.splitlines():
        if not line.strip().startswith("description:"):
            continue
        _, value = line.split(":", 1)
        return value.strip().strip("\"'")
    return ""


def _display_path(path: Path, project_root: Path) -> str:
    """Return a stable, non-local path for tool-visible skill metadata."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return path.name


__all__ = [
    "PRODUCT_PPT_SKILLS_DIR",
    "ProductPptSkillInfo",
    "ProductPptSkillRegistry",
]
